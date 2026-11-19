---
name: drama-recreation
description: 短剧视听复刻与视频生成转换技能。秉持“资产先行（Asset-First）”原则，先提取并锁死整剧的角色四视图、场景空镜与道具母版，再将 video_parser、rea-toon 或 rea-video 逆向分析出的单集分镜头编译为 MiniMax H3、SeaDance 的工程级多模态提示词与音效设计，并支持通过 Toonflow MCP 自动搭建可视化节点流执行复刻。
---

# 短剧视听复刻转换技能 (Drama Recreation Pipeline)

本技能专用于将 **VideoShelf / rea-toon / rea-video** 逆向拆解出的短剧结构化资料，转化为符合工业级制作规范的 **AI 视频复刻方案**。

---

## 核心工业化原则：资产先行 (Asset-First Architecture)

在影视制作和剧集复刻中，**绝对不能跳过资产直接生视频**。如果逐镜自由生成，角色每镜都会换脸、换衣服、换体型，场景光影与尺度也会失控。

标准的工业复刻严格遵循 **两阶段流水线**：

```mermaid
flowchart TD
    subgraph 阶段一: 剧集恒定资产库 (跨集通用)
        C["角色资产 (CHAR)<br>横向四视图 / 面部特写 / 音色样本"]
        S["场景资产 (SCENE)<br>无人物空间主视图空镜"]
        P["道具资产 (PROP)<br>2x2结构三视图与细节"]
    end

    subgraph 阶段二: 单集分镜头执行 (逐镜驱动)
        S1["单镜起幅构图 (First Frame)"]
        S2["多模态导演提示词 (Motion & Micromovement)"]
        S3["对白与真实环境音效 (Soundscape)"]
    end

    C --> |作为 Picture 1/2 绑定| S2
    S --> |空间透视基准| S2
    P --> |道具形制基准| S2
    S1 & S2 & S3 --> V["视频大模型 (MiniMax H3 / SeaDance)"]
    V --> Output["连贯成片"]
```

---

## 一、阶段一：剧集恒定资产设计规范

在进入任何一集的分镜渲染前，必须先在剧集根目录确立三大资产母版：

### 1. 角色资产：横向四视图设计板 (Character 4-View Board)
- **规格**：单张图横向排列：**头部特写、正面全身、右侧90°全身、背面全身**。
- **作用**：作为视频大模型的 `<Picture 1>`、`<Picture 2>` 多图主体参考（Ref2VA）。
- **去 CG 化实拍物理降级**：
  - 严禁“精美CG网游捏脸白光”，强制锁死**离地 1.6 米平视视高**；
  - **无摄影机正面补光**，自然侧漫射光线随距离自然衰减；
  - 哑光棉麻/丝缎刺绣织物，无油腻镜面反光；
  - 真实皮肤肌理（如两栖类湿润皮质褶皱、微小毛细孔与自然微汗）。
- **配套音色**：自动提取角色声音切片（`*_voice.mp3`）用于后续声音克隆对轨。

### 2. 场景资产：空间主视图 (Environment Master View)
- **规格**：单张大景深开阔主视图，交代前中后景与空间通道。
- **铁律**：**画面内绝对禁止出现人物、人影、动物**。
- **作用**：锁定室内空间长宽比、门窗位置、罗汉榻/案几固定陈设，防止切机位时房间格局漂移。

### 3. 道具资产：2×2 结构板 (Prop 2×2 Board)
- **规格**：同一画面 2×2 四宫格：左上正面、右上右侧90°、左下背面、右下关键结构/材质磨损特写。
- **作用**：锁定关键戏剧冲突道具（如“梳形画具”）的尺寸、木纹旧化与缺齿形制。

---

## 二、阶段二：单集分镜头复刻规范

在资产定稿后，读取每集的 `shots/*.md` 和 `frames/` 进行分镜头生成：

### 1. MiniMax H3 转换标准
- **模式**：
  - 首选 **I2VA**（以原片 `shot_xxx_first.jpg` 为起幅，注入参考图母版）；
  - 时长智能归一化到 **4.0s - 10.0s**。
- **提示词构建**：
  - `integrated_multimodal_description`：融合机位运镜、微表情变化（眼珠外凸震颤、嘴角抽搐、足尖踢箱）、服装动作链与老镜头物理降级；
  - `overall_soundscape`：提取角色真实对白与物理动作摩擦声。

### 2. SeaDance (字节即梦) 转换标准
- **起落幅控制**：`first_frame` 锁定起幅，`last_frame` 锁定落幅；
- **运镜动力学**：`Static`、`Slow Push In`、`Crane Up`。

---

## 三、本地一键转换命令

```bash
# 自动生成包含【阶段一资产母版】与【阶段二单集分镜提示词】的完整施工图
python3 skills/drama-recreation/scripts/convert.py -d data/dramas/<drama_id>
```

产出物包括：
- `assets.json`：全套角色四视图、场景空镜、道具 2x2 板提示词及原片提取的面部/造型/音色文件路径；
- `minimax_h3.json`：符合 MiniMax H3 规范的分镜生成 payload；
- `seadance.json`：符合 SeaDance 规范的分镜生成 payload；
- `recreation_plan.md`：排版清晰的完整影视级复刻施工手册。

