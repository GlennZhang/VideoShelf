## ADDED Requirements

### Requirement: Category-Specific Content Analysis
系统 SHALL 使用类别特定的提示词分析视频内容。

#### Scenario: Drawing video analysis
- **GIVEN** 一个被分类为"drawing"的视频
- **AND** 从视频中提取10个关键帧
- **WHEN** 执行内容分析
- **THEN** 使用绘画特定的提示词
- **AND** 模型识别：绘画类型、主题、风格、阶段和具体内容

#### Scenario: Tutorial video analysis
- **GIVEN** 一个被分类为"tutorial"的视频
- **AND** 来自视频的关键帧
- **WHEN** 执行内容分析
- **THEN** 使用教程特定的提示词
- **AND** 模型识别：领域、类型、等级和主题

#### Scenario: Unknown category analysis
- **GIVEN** 一个被分类为"other"的视频
- **WHEN** 执行内容分析
- **THEN** 使用通用分析提示词
- **AND** 模型提供一般性描述

### Requirement: Multi-Frame Aggregation
系统 SHALL 分析多帧并聚合结果。

#### Scenario: Analyze all frames
- **GIVEN** 从视频中提取的10帧
- **WHEN** 执行批量分析
- **THEN** 每帧都被单独分析
- **AND** 返回所有帧的描述

#### Scenario: Aggregate consistent themes
- **GIVEN** 多个显示相似内容的帧（例如肖像绘画）
- **WHEN** 聚合结果
- **THEN** 识别跨帧的共同主题
- **AND** 生成综合摘要

### Requirement: Category-Specific Prompts from Config
系统 SHALL 从标签配置文件加载分析提示词。

#### Scenario: Load drawing prompt
- **GIVEN** 一个定义了category_prompts.drawing的tags_config.yaml
- **WHEN** 请求绘画分析
- **THEN** 配置的绘画提示词用于模型推理

#### Scenario: Load tutorial prompt
- **GIVEN** 一个定义了category_prompts.tutorial的tags_config.yaml
- **WHEN** 请求教程分析
- **THEN** 配置的教程提示词用于模型推理

### Requirement: Confidence Scoring
系统 SHALL 为分析结果分配置信度分数。

#### Scenario: High confidence analysis
- **GIVEN** 具有清晰、一致内容的帧
- **WHEN** 执行分析
- **THEN** 分配置信度分数 > 0.8

#### Scenario: Low confidence analysis
- **GIVEN** 具有模糊或不一致内容的帧
- **WHEN** 执行分析
- **THEN** 分配置信度分数 < 0.6
- **AND** 结果被标记以供可能的人工审查

### Requirement: Structured Output
系统 SHALL 生成结构化分析输出。

#### Scenario: Return structured description
- **GIVEN** 完成的内容分析
- **WHEN** 返回结果
- **THEN** 输出包括：
  - 每帧描述
  - 整体视频摘要
  - 建议的标签
  - 置信度分数
