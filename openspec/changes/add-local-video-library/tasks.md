## 1. 基础设施与配置

- [x] 1.1 创建 `src/library/` 包骨架（app/db/scanner/hashing/pipeline/omlx_client/media/search/backup/api/static 分模块）
- [x] 1.2 `config.yaml` 新增 `library:`（数据目录、服务端口等）与 `omlx:`（base_url、api_key_env、asr_model、vision_model）配置节及加载/校验代码
- [x] 1.3 `omlx_client.py`：OpenAI 兼容客户端（chat completions 含 base64 图像、audio transcriptions 含 language=zh），API key 从配置或 `OMLX_API_KEY`
- [x] 1.4 `library doctor` 命令：检查 ffmpeg/ffprobe、SQLite FTS5/trigram 能力、oMLX 连通性与模型可用性
- [x] 1.5 单元测试：omlx_client 请求构造与错误处理（mock HTTP）

## 2. 存储层

- [x] 2.1 `db.py`：连接管理（WAL、外键）、`meta.schema_version` 版本化迁移框架与 v1 全量 schema（folders/contents/files/videos/transcripts/screenshots/people/video_people/collections/collection_items/tasks）
- [x] 2.2 `hashing.py`：流式 SHA-256（1MiB 分块）；（大小, mtime_ns）快速判断
- [x] 2.3 数据访问层：内容/文件增删改查、内容与路径分离关联、missing 标记不级联删除
- [x] 2.4 AI/用户双列模型：读取时 `user_*` 优先；重新分析只写 `ai_*` 的守卫测试
- [x] 2.5 FTS5：trigram 探测与建表（external-content），索引（标题、画家及别名、简介、标签、转写），短查询 LIKE 回退，重建命令
- [x] 2.6 单元测试：schema 迁移、哈希、内容/文件关联（移动、重复导入、修改后新哈希、missing 保留人工数据）、FTS 中文检索

## 3. 导入与扫描

- [x] 3.1 文件夹管理：添加/列出/移除文件夹；路径规范化与重复添加拒绝
- [x] 3.2 递归扫描：MP4/MKV/MOV 大小写不敏感识别；新增/移动/修改/删除检测；（大小, mtime）优化 + 变化时重算哈希
- [x] 3.3 扫描后自动为新增内容排队分析任务（任务队列入队）
- [x] 3.4 扫描进度与结果记录（last_scan_at、last_scan_result）
- [x] 3.5 单元测试（构造临时目录树覆盖上述场景）

## 4. 分析管线

- [x] 4.1 `media.py`：ffprobe 元信息、ffmpeg 音频抽取（16kHz mono WAV）、有效语音启发式检测、OpenCV 帧采样（复用/改造 frame_extractor）
- [x] 4.2 阶段状态机 `pipeline.py`：probe→hash→frames→audio→transcribe→analyze→finalize；阶段产物按（sha256, pipeline_version）缓存于 `data/analysis/`；失败记录与从失败阶段重试
- [x] 4.3 转写：分段（≤10 分钟）调用 oMLX，分段起止时间入库；无语音/失败回退 `visual_only_fallback` 状态
- [x] 4.4 联合分析：关键帧+转写窗口输入视觉模型，结构化 JSON 输出（分类/主题/形式/画种/工具/技法/标签/标题建议/简介/画家与来源候选含角色和依据/代表帧选择），带 JSON 解析容错
- [x] 4.5 finalize：写 `ai_*` 列、创建/关联 people 候选、登记待确认项
- [x] 4.6 后台工作线程 + SQLite 任务队列：顺序消费、进度上报、错误挂起、重试入队
- [x] 4.7 集成测试（mock oMLX + 生成的小视频文件，覆盖成功、无音轨回退、中途失败重试、缓存命中）

## 5. Web API

- [x] 5.1 FastAPI 应用骨架：静态资源、127.0.0.1 绑定、启动时迁移
- [x] 5.2 文件夹与任务接口：增删查、触发重扫描、任务列表/进度/重试
- [x] 5.3 视频接口：列表（分页/筛选）、详情（含转写、截图、文件位置、识别依据）、编辑（写 `user_*`）、收藏、备注
- [x] 5.4 画家接口：列表、别名管理、人工合并/拆分、确认状态更新
- [x] 5.5 合集接口：创建、增删成员；批量操作（指定画家/改分类/加标签/加入合集）
- [x] 5.6 搜索接口：关键词 + 筛选组合
- [x] 5.7 播放流：Range 直读 MP4；MOV/MKV 转封装/实时转码回退；截图文件服务；在 Finder 中定位原文件
- [x] 5.8 接口测试（httpx + 临时库）

## 6. Web 界面

- [x] 6.1 三栏布局骨架与路由（文件夹/分类/画家/合集导航、中间列表、右侧详情）
- [x] 6.2 视频卡片（截图、标题、画家、短简介、标签、时长、状态）与详情编辑表单
- [x] 6.3 播放器：抽屉式播放、点击截图跳转时间戳
- [x] 6.4 搜索框 + 筛选器（分类、画家、形式、时长、文件夹、收藏、合集）
- [x] 6.5 批量编辑与合集管理交互
- [x] 6.6 待确认列表（人物/分类/分析异常）集中处理
- [x] 6.7 任务进度面板与失败重试入口

## 7. 备份与恢复

- [x] 7.1 `library backup`：zip（VACUUM INTO 快照 + screenshots + analysis 清单 + library 配置 + meta），排除原视频
- [x] 7.2 `library restore`：重建库与资产；路径校验，缺失内容标 missing 且保留人工数据
- [x] 7.3 根目录重绑定：旧根→新根映射，按 SHA-256 重新关联文件
- [x] 7.4 测试：备份→清空→恢复→数据一致；移动根目录后重绑定

## 8. 验证与文档

- [x] 8.1 端到端冒烟：添加样例文件夹 → 扫描 → 分析（真实 oMLX，若不可达则验证降级）→ 浏览/搜索/播放/编辑
- [x] 8.2 `pytest tests/` 全量通过；black/flake8 清洁
- [x] 8.3 README 增补 library 使用说明（serve/doctor/backup/restore、oMLX 配置）
