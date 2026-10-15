# 现成视频资料库项目调研

日期：2026-10-13

对应需求：[本地绘画视频资料库](local-video-library.md)。本次核查公开文档、许可证和部分关键源码，未安装或实测。未找到证据不等于功能一定不存在；涉及本机性能、格式播放、中文搜索和 oMLX 的结论须通过试用确认。

## 1. 结论

有相近项目，可以省去大量资料库界面开发，但本次未确认有一个项目能不改代码同时满足：原位文件索引、完整哈希去重、多路径身份、中文语音与视觉联合分析、oMLX、画家与视频号来源区分、自动简介和代表截图，以及人工结果保护。

建议先试用现成项目，再决定是否开发。若坚持 oMLX 和严格开源，优先评估 Cove 或 Stash 作为管理底座，另接自动分析扩展；若接受项目自带推理方案及源码开放许可证，arkiv 是 AI 工作流最接近的候选。没有依据估算“满足百分之多少”或适配工期。

## 2. 对比

| 项目 | 已核实的接近能力 | 主要差距或未确认项 | 初步定位 |
| --- | --- | --- | --- |
| Cove | 文件夹扫描、人物关联、标签与分组、片段、自定义字段、批量字段编辑、Mac 应用、扩展机制；AGPLv3 | 未确认现成 oMLX 中文转写／简介扩展、完整哈希去重、多路径内容身份及中文全文搜索效果 | 优先试用的通用管理底座 |
| Stash | 文件夹扫描、人物与标签管理、预览、MD5、感知哈希重复检查；AGPLv3 | 默认面向成人媒体，术语不贴合绘画；未确认原生 oMLX 转写与画家识别；重复文件策略与多路径需求需实测 | 哈希与整理需求较匹配的备选底座 |
| arkiv | 中文语义搜索、语音转写、关键帧视觉描述与自动标签、人工标签、缩略图、Mac Apple Silicon 应用 | 当前调用层使用 Ollama 原生接口；画家／来源角色与绘画分类需适配；DIT 哈希校验不等于内容去重 | AI 功能最接近，但不满足直接使用 oMLX 的条件 |
| Video Log Manager / pacingjosh-video-manager | 递归扫描、多个缩略图、播放、分类标签、备注、转写和视觉描述、语义搜索、Apple Silicon 安装包；MIT | 文档主要配置云端 AI，未确认自定义 oMLX 地址；未确认完整哈希去重、画家身份管理 | 可试用的桌面应用，但本地 AI 适配存在缺口 |
| Edit Mind | 文件夹监控、多模态索引、语音转写、物体／文字检测、自然语言片段搜索 | 作者明确标注尚未 production-ready；自托管 Docker 方案不能直接使用 Apple GPU；当前为自定义受限许可证，未确认 oMLX | 暂不作为省心使用的首选 |

## 3. Cove

[项目仓库](https://github.com/yourcove/cove) · [功能介绍](https://yourcove.net/features/) · [扫描文档](https://yourcove.net/docs/reference/scans/)

Cove 提供人物、标签、分组和片段结构，可以用人物记录管理画家，用标签或枚举字段管理画种、技法和学习状态。它提供 macOS 原生应用及扩展机制，适合作为通用资料库候选。

[自定义字段文档](https://yourcove.net/docs/user/library/custom-fields/)明确说明字段值存于资料库，不写入原媒体文件，可批量修改。可配置来源账号、画家确认状态、画种和视频形式等字段。

重要限制：Long Text 可保存简介或转写，但文档说明该类型只提供“是否存在”过滤，不提供内容过滤和排序。不能因为能保存转写，就假定已经满足转写全文搜索；需另外验证视频搜索及扩展能力。

网站介绍的 AI 人脸功能是查找和关联人物出现，不等同于可靠识别画家姓名，更不能自动区分作品作者与转载账号。本次未确认可直接安装、接入 oMLX 的中文联合分析扩展。

试用重点：原位扫描行为、MP4／MKV／MOV 播放、画家及来源字段、中文筛选、完整哈希策略、批量编辑、备份，以及扩展能否写入分析结果且保护人工值。

## 4. Stash

[项目](https://github.com/stashapp/stash) · [官方文档](https://docs.stashapp.cc/) · [扫描任务](https://docs.stashapp.cc/in-app-manual/tasks/)

可对现有文件夹扫描并建立人物、标签和预览等资料记录；人物字段可以人工用于画家，但界面和生态主要面向成人媒体，采用前应实际评估是否适合绘画资料库。

[哈希配置](https://docs.stashapp.cc/in-app-manual/configuration/)提供 MD5 与 oshash。MD5 读取完整文件；oshash 仅读取文件头尾部分，不能把快速哈希当作完整文件唯一性保证。新安装默认关闭 MD5 计算，满足完整哈希需求时需显式启用。

[重复检查](https://docs.stashapp.cc/in-app-manual/deduplication/)提供 pHash 路线。扫描文档描述忽略相同内容的重复文件，因此不能假定它实现了需求中的“一份内容、多个文件位置”；版本行为须试用确认。

本次未确认核心应用原生支持 oMLX 中文转写、绘画分类、简介生成和画家来源区分。更现实的使用方式是保留其整理界面，通过 API／插件接入分析结果，这仍需要开发。

## 5. arkiv

[项目与 README](https://github.com/vulture-s/arkiv) · [处理流程](https://github.com/vulture-s/arkiv/blob/main/docs/pipeline.md)

具备中文语义搜索、转写、视觉描述、标签和缩略图，macOS 使用 mlx-whisper，提供 Apple Silicon 安装包。文档中的中文输出偏台湾繁体，应验证简体查询及所需输出风格。

其流程面向拍摄和剪辑素材管理，而非绘画学习资料库。[视觉提示词](https://github.com/vulture-s/arkiv/blob/main/vision.py)主要生成画面描述、标签、A-Roll／B-Roll 等剪辑 metadata；不等同于已经实现画家、绘画技法和来源账号识别。

[调用层源码](https://github.com/vulture-s/arkiv/blob/main/llm.py)使用 `/api/chat`、`/api/generate`、`/api/embeddings` 等 Ollama 接口。因此不能只把服务地址改成 oMLX 就认定兼容；文本、视觉、嵌入和转写均需分别评估接入。

许可证为 PolyForm Perimeter，项目自称 source-available，不应归入与 MIT／AGPL 相同的严格开源候选。README 说明可免费使用，包括商业用途，但限制制作竞争产品，并有单独付费的 Pro 能力。对是否采用，依据实际许可证和功能边界判断。

另外，DIT 复制与哈希校验是备份完整性能力，不能据此断言视频入库采用完整哈希去重。

## 6. Video Log Manager

[项目](https://github.com/joaoh82/pacingjosh-video-manager) · [AI 功能文档](https://github.com/joaoh82/pacingjosh-video-manager/blob/main/docs/ai-features.md)

MIT 许可，提供 Apple Silicon 桌面安装包及本地分类、标签、备注、缩略图和播放器。AI 文档包含转写、根据缩略图生成视觉描述、语义检索，功能形态接近需求。

但公开配置主要列出 OpenAI、Gemini、Anthropic、ElevenLabs 等服务。没有确认现成 oMLX 自定义地址配置，不能因其支持 OpenAI 就认定支持任意 OpenAI 兼容服务。哈希去重和画家来源区分也未确认。

## 7. Edit Mind

[README](https://github.com/IliasHad/edit-mind) · [当前许可证](https://github.com/IliasHad/edit-mind/blob/main/LICENSE.md)

可以监控文件夹并进行多模态索引与自然语言搜索。但 README 明确标注 active development、not yet production-ready，并说明自托管 Docker 方案无法利用 Apple GPU，桌面版是商业方案。

许可证说明 2026-02-21 后版本采用 Edit Mind License：个人及符合条件的小组织可以免费使用，限制竞争产品及衍生产品销售。此前 MIT 版本另行保留，不能把旧许可套用到当前版本。

考虑用户希望省去开发和运维，本次不把它列为首选；中文、oMLX 和本机实际性能均未验证。

## 8. 其他候选及不优先原因

- [Video Hub App](https://github.com/whyboris/Video-Hub-App)：MIT 源码，支持 Mac，擅长截图浏览、标签和批量整理；[用户指南](https://videohubapp.com/en/guide/)可查看具体功能。没有确认自动中文联合分析，适合管理界面需求优先的场景。
- [VideoVortex Core / vvx](https://github.com/videovortex-app/vvx)：README 明确提供原位目录索引，不下载或移动文件，支持转写与 SQLite 搜索；但主项目是 headless CLI，不能直接替代所需浏览整理界面。
- [Damselfly](https://github.com/Webreaper/Damselfly)：主要面向照片管理，未确认完整视频工作流，不作为本次首选。

## 9. 采用现成方案的建议路径

1. 用 10～20 个代表性视频建立测试库：三种格式、长短视频、无语音、背景音乐、同文件重复、移动后的文件、已知画家与转载账号。
2. 先试 Cove 的管理体验；如果完整哈希和重复处理是最重要条件，再比较 Stash。
3. 若愿意放宽 oMLX 约束，试 arkiv 自带流程，观察中文简介、标签和搜索能否满足实际使用。
4. oMLX 必须保留时，核实扩展／API 能否接入当前分析程序；仅开发自动分析与 metadata 写入，复用现成界面和资料库。
5. 若关键管理体验仍不满足，再回到自研方案；不要只凭 README 决定导入整个视频库。

验收检查：扫描是否保留原文件位置，完整哈希是否避免重复分析，多路径是否保留，画家与来源能否分开，简介和转写是否可搜索，截图能否跳转，人工修改是否不被重跑覆盖，数据库与截图能否备份恢复。

以上为候选试用与选型建议，不表示已完成安装、已兼容 oMLX 或已通过本机验收。
