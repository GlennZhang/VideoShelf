[English](README.md) | **简体中文**

# VideoShelf（本地视频资料库）

VideoShelf 用于管理、搜索和整理本地视频，适用于绘画教学、课程、Vlog 等不同类型的视频。绘画是当前重点使用场景，分类和画家信息也可按需使用。项目同时提供浏览器资料库和命令行视频分析工具。

![VideoShelf 界面截图](screenshot.png)

## 功能特性

- **本地视频资料库**：索引多个文件夹，浏览、搜索、收藏和整理视频
- **自动视频扫描**：从目录中发现视频文件（MP4、AVI、MOV、MKV、WebM）
- **智能帧提取**：使用多种策略提取具有代表性的关键帧
- **AI 智能分类**：基于本地 qwen3-VL:8B 模型的两阶段分类
- **标签生成**：基于可配置的 YAML 标签体系生成结构化标签
- **多种导出格式**：JSON、CSV、SQLite，支持增量更新
- **本地优先**：默认使用本机 Ollama/oMLX 服务；若配置远端 oMLX，媒体分析内容会发送到该服务

## 安装

### 前置要求

- Python 3.10 或更高版本
- 已安装 Ollama 及 qwen3-VL 模型

### 安装步骤

1. 克隆仓库：
```bash
git clone <repository-url>
cd VideoShelf
```

2. 创建虚拟环境：
```bash
python -m venv venv
source venv/bin/activate  # Windows 下使用: venv\Scripts\activate
```

3. 安装依赖：
```bash
pip install -r requirements.txt
```

4. 拉取 Ollama 模型：
```bash
ollama pull qwen3-vl:8b
```

## 使用方法

### 基本用法

解析目录中的所有视频：
```bash
python src/cli.py parse /path/to/videos
```

### 命令

- `parse` - 处理视频并生成标签
- `scan` - 仅扫描目录中的视频，不做处理
- `stats` - 显示标签统计信息
- `tags` - 管理标签配置

### 选项

- `--output, -o` - 输出文件路径
- `--format, -f` - 输出格式（json、csv、sqlite）
- `--recursive, -r` - 递归扫描目录
- `--force` - 重新处理已处理过的视频
- `--config` - 自定义配置文件路径

### 示例

以指定格式解析并输出：
```bash
python src/cli.py parse /path/to/videos --format csv --output results.csv
```

仅扫描不处理：
```bash
python src/cli.py scan /path/to/videos --recursive
```

查看统计信息：
```bash
python src/cli.py stats results.json
```

## 配置

### 应用配置（config.yaml）

主要应用设置，包括：
- 模型配置
- 帧提取策略
- 分类参数
- 输出格式
- 日志设置

### 标签配置（tags_config.yaml）

定义标签体系：
- 层级分类
- 每个分类的预定义标签
- 自由文本标签的允许范围
- 分类专属的子分类

## 项目结构

```
VideoShelf/
├── src/
│   ├── video_parser/       # 命令行分析核心模块
│   ├── config/             # 配置管理
│   └── cli.py              # CLI 入口
├── tests/                  # 测试文件
├── config.yaml             # 应用配置
├── tags_config.yaml        # 标签体系
└── requirements.txt        # Python 依赖
```

## 开发

### 运行测试

```bash
pytest tests/
```

### 代码格式化

```bash
black src/ tests/
flake8 src/ tests/
```

## VideoShelf 本地视频资料库（library）

可浏览、搜索和整理各类本地视频；绘画视频是重点使用场景之一。原视频保持原位不修改，资料库数据存于 SQLite。需求与设计见 `spec/local-video-library.md` 与 `openspec/changes/add-local-video-library/`。

运行时数据库、转写、截图和音频缓存保存在 `data/`，审计报告保存在 `artifacts/`。这些文件可能包含私人媒体内容、转写文本和本机路径，已从 Git 提交范围中排除。

### 功能

- 多文件夹递归导入（MP4/MKV/MOV），完整 SHA-256 去重，内容与文件路径分离（移动/重复导入自动重新关联）
- 经 oMLX 的中文转写 + 关键帧视觉联合分析：标题建议、简介、主分类/主题/形式、标签、画家与来源账号候选（含角色与依据）、1–2 张可跳转播放的代表截图
- 无有效语音或转写服务失败时回退纯视觉分析并显式记录状态
- AI 建议与人工修改分列保存，重新分析永不覆盖人工值
- 中文全文搜索（FTS5 trigram）覆盖标题、画家及别名、简介、标签、转写
- 浏览器播放（MP4 直读，MOV/MKV 实时转封装/转码）、截图时间跳转、定位原文件
- 收藏、虚拟合集、备注、批量编辑、待确认列表、任务进度与失败重试
- 资料库备份与恢复（数据库+截图，不含原视频），支持跨机器根目录重绑定

### 快速开始

```bash
# 1. 环境体检（ffmpeg、FTS5、oMLX 连通性与模型）
python -m src.cli library doctor

# 2. 启动本地 Web 应用（默认 http://127.0.0.1:8900）
python -m src.cli library serve

# 3. 浏览器打开后点击「添加文件夹」，扫描与分析自动排队执行
```

### oMLX 配置

`config.yaml` 的 `omlx:` 节配置服务地址、API key 与模型；环境变量 `OMLX_BASE_URL` / `OMLX_API_KEY` / `OMLX_ASR_MODEL` / `OMLX_VISION_MODEL` 优先于文件值。语音转写模型（默认 `Qwen3-ASR-1.7B-8bit`）需先在 oMLX 应用中加载，否则该视频自动回退纯视觉分析（状态可见）。

### 备份与恢复

```bash
python -m src.cli library backup                  # 生成 library-backup-*.zip
python -m src.cli library restore backup.zip      # 恢复到当前数据目录
python -m src.cli library restore backup.zip \
    --old-root /Volumes/old --new-root /Volumes/new   # 跨机器迁移重绑定
```

### 测试

```bash
python -m pytest tests/library/ -q
```

## 许可证

MIT 许可证 - 详见 LICENSE 文件
