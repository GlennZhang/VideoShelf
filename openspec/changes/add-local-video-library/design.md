# 设计：本地视频资料库（第一版）

## 背景

- 硬件：MacBook M1 Pro，32GB。推理经本机已运行的 oMLX 服务（实测 `127.0.0.1:8989`，需 API key 鉴权）。
- 现有 `src/video_parser/` 提供视频扫描、OpenCV 帧提取、基于 Ollama 的分类和 JSON/CSV/SQLite 导出。资料库是并排的新子系统，不是对它的重写。
- 所有视频保持原位，资料库只读访问。
- 界面形态已定：**本地 Web 应用**。无有效语音时回退纯视觉分析：**允许**，且显式记录状态。

## 目标 / 非目标

- 目标：`spec/local-video-library.md` §10 第一版范围——导入/去重、oMLX 分析、metadata 模型、浏览/搜索/整理界面、备份、人工结果保护。
- 非目标：关键时间点、疑似重复、学习状态、系列关联、语义搜索、文件夹自动监控、桌面打包。

## 决策

### D1：架构 —— FastAPI 本地 Web 应用 + 进程内后台工作线程

- `src/library/` 包：`app.py`（FastAPI）、`db.py`（schema/迁移）、`scanner.py`、`hashing.py`、`pipeline.py`（分析阶段）、`omlx_client.py`、`media.py`（ffmpeg/ffprobe/OpenCV 辅助）、`search.py`（FTS）、`backup.py`、`api/`（路由）、`static/` + `templates/`（前端）。
- 单个 uvicorn 服务仅绑定 127.0.0.1；无鉴权（单用户本地工具）。`library serve` CLI 命令启动。
- 后台任务：单工作线程消费 SQLite 持久化队列。按需求 §2 分析严格顺序执行（一次一个视频）；扫描任务进同一队列但优先级更高。任务通过轮询接口暴露分阶段进度/状态（v1 不用 websocket）。
- 否决的替代方案：桌面应用（打包维护成本更高，播放约束相同）；Celery/RQ（过重，且增加需要备份的服务）。

### D2：存储 —— SQLite WAL、内容/文件分离、AI/用户双列

核心表：

- `folders(id, path, added_at, last_scan_at, last_scan_result)`
- `contents(id, sha256 UNIQUE, size_bytes, duration_s, width, height, video_codec, audio_codec, container, created_at)` —— 每个不同文件内容一行
- `files(id, content_id 外键, path 唯一, size_bytes, mtime_ns, status)` —— `status ∈ {available, missing}`；missing 不级联删除内容或人工数据
- `videos(content_id 主键)` 成对列：`ai_title/user_title`、`ai_summary/user_summary`、`ai_category/user_category`、`ai_topic/user_topic`、`ai_form/user_form`、`ai_tags/user_tags`、`user_notes`、`user_favorite`、`analysis_status`、`analysis_error`、`analysis_run_at`、`pipeline_version` 等。展示层 `user_*` 非空时始终优先。
- `transcripts(content_id, idx, start_s, end_s, text)`，外加 `contents` 上的 `transcript_status`（none / no_speech / failed / ok / visual_only_fallback）
- `screenshots(id, content_id, path, timestamp_s, is_representative, chosen_by)` —— `chosen_by ∈ {ai, user}`
- `people(id, kind ∈ {painter, source_account}, canonical_name, aliases_json, confirmed_status, evidence_json)`
- `video_people(content_id, person_id, role ∈ {author, presenter, featured_painter, reposting_account}, confirmed_status, source ∈ {ai, user})`
- `collections(id, name)` / `collection_items(collection_id, content_id)`
- `tasks(id, type, payload_json, status, stage, progress, error, created_at, updated_at)`
- `meta(key, value)` 保存 schema 版本与根目录绑定

重新分析只写 `ai_*` 列与分析元数据；`user_*` 列仅由用户编辑接口触碰。

### D3：身份 —— 流式完整文件 SHA-256

- 首次导入按 1MiB 分块计算哈希，存于 `contents`。
- 重扫描快速路径：路径的（大小， mtime_ns）未变则跳过哈希。任何变化 → 重算哈希；哈希相同 → 重新关联既有内容（移动/重复场景）；新哈希 → 新内容，旧内容若仍有其他路径则保留记录，否则保留人工数据但标记 `orphaned_missing`，不删除。
- 重复候选：先比大小，再比完整哈希（需求 §3.3）。
- 改名/重编码产生不同哈希；疑似重复检测不在本版范围。

### D4：中文全文搜索 —— FTS5

- FTS5 external-content 表覆盖（标题、画家名+别名、简介、标签、转写文本）。
- 分词器：系统 SQLite 支持 `trigram` 时使用（macOS python sqlite3 已验证 FTS5 可用），对 CJK 子串匹配无需分词依赖。回退方案：字符二元组自定义分词或 `jieba` 预分词；由启动时能力探测决定并记入 `meta`。
- 查询：单一关键词输入框 + 结构化筛选（分类、画家、形式、时长区间、文件夹、收藏、合集）在 SQL 中组合；结果按时间/收藏排序。对 trigram 无法命中的短查询（<3 字符）回退 LIKE。

### D5：分析管线 —— 分阶段、可缓存、可续跑

每个视频的阶段（状态机，持久化于 `tasks` + `contents.analysis_status`）：

1. `probe` —— ffprobe 流/元信息
2. `hash` —— D3
3. `frames` —— OpenCV 采样（复用 `frame_extractor` 策略；约 8～12 帧，另生成时间轴缩略图）
4. `audio` —— ffmpeg 抽 16kHz 单声道 WAV；启发式检测有效语音（能量/VAD；无语音 → 回退路径）
5. `transcribe` —— oMLX `/v1/audio/transcriptions`，`language=zh`，按 ≤10 分钟分段，分段带偏移入库
6. `analyze` —— oMLX chat completions，关键帧（base64）+ 对应转写窗口联合输入；结构化 JSON 输出：分类、主题、形式、画种/媒介、工具、技法、标签、标题建议、约 100～200 字简介、画家/来源候选（含角色、依据、置信度）、1～2 张代表帧及时间戳
7. `finalize` —— 写 `ai_*` 列、关联人物候选、登记待确认项

- 缓存：各阶段产物存于 `data/analysis/<sha256[:2]>/<sha256>/<stage>.json`（及媒体文件）；阶段产物存在且 `pipeline_version` 匹配时跳过重跑。提示词/逻辑变更时提升管线版本常量。
- 失败：记录阶段错误；重试从失败阶段进入。oMLX 不可达 → 任务挂起并报错，用户从界面重试。
- 回退：无音轨/无语音/转写失败 → `visual_only_fallback` 状态；`analyze` 仅基于帧运行；仍产出简介与 metadata，转写检索字段为空。
- 模型可配置（`omlx.asr_model`、`omlx.vision_model`）；默认 `mlx-community/Qwen3-ASR-1.7B-8bit` 与 `mlx-community/Qwen3.5-9B-4bit`。API key 来自配置或环境变量 `OMLX_API_KEY`，绝不入库入仓。

### D6：播放 —— 原生流式优先，ffmpeg 转封装/转码回退

- `/api/files/{file_id}/stream`：MP4/H.264+AAC 以 HTTP Range 直接服务。
- MOV/MKV 或浏览器不兼容编码：编码浏览器兼容（H.264/HEVC 按 Safari 支持、AAC/ALAC）时用 ffmpeg `-c copy` 经 HTTP 实时转封装为 fMP4；否则实时转码 H.264（faststart 预设）。原文件不动。
- 时间跳转：前端播放器按截图时间戳设 `currentTime`；v1 不生成代理文件。

### D7：备份与恢复

- 备份 = zip 包：SQLite 快照（`VACUUM INTO`）、`screenshots/`、`analysis/` 缓存清单、`config.yaml` library 配置节、`meta`。
- 原视频默认不纳入（需求 §9 明确范围）。
- 恢复：重建数据库+资产；文件路径重新校验——无文件的内容转为 `missing`，人工数据全部保留。根目录重绑定：用户映射旧根 → 新根，扫描器按 SHA-256 重新关联。

### D8：前端 —— 无构建原生 SPA

- 单个 `static/index.html` + `app.js` + `app.css`，由 FastAPI 服务；基于 fetch 的 JSON API；`<video>` 元素实现点击截图跳转。三栏响应式布局按需求 §8。不引入 npm 工具链；缩小备份/恢复与贡献面。

## 风险 / 取舍

- oMLX 接口兼容性（转写响应结构、视觉图像输入）未在本机验证 → 缓解：`omlx_client` 隔离在单模块 + `library doctor` 连通性/模型检查命令；管线各阶段优雅降级。
- trigram FTS 索引体积随转写增长 → 个人资料库规模可接受；提供索引重建命令。
- 长 MKV 实时转码吃 CPU → 转封装优先策略规避大多数场景；转码预设保守。
- 顺序分析对大库较慢 → 按需求 §2 接受；并行化延后。

## 迁移计划

- 全新子系统；现有 CLI（`src/cli.py`）不动。新增 `library` 子命令入口。回滚 = 删除 `src/library/` 并还原配置；现有 results/ 输出不受影响。
- schema 迁移：`db.py` 内版本化迁移列表，启动时按 `meta.schema_version` 应用。

## 待定问题

- 无阻塞 v1 的问题；模型质量对比（Qwen3-ASR vs whisper-turbo、Qwen3.5-9B vs 现有 qwen3-vl）是首次端到端运行后的运营评估事项，不是设计依赖。
