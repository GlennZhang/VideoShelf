## ADDED Requirements

### Requirement: Two-Stage Video Classification
系统 SHALL 在详细分析之前将视频分类到顶层类别。

#### Scenario: Stage 1 - Category determination
- **GIVEN** 来自绘画视频的前3帧
- **WHEN** 使用通用分类提示词分析帧
- **THEN** 系统返回类别（例如"drawing"）
- **AND** 提供简短理由

#### Scenario: Supported categories
- **GIVEN** 任何视频输入
- **WHEN** 执行分类
- **THEN** 结果为以下之一：drawing、tutorial、vlog、entertainment、sports、other

### Requirement: Ollama Model Integration
系统 SHALL 使用本地qwen3-VL:8B模型进行分类。

#### Scenario: Successful model call
- **GIVEN** Ollama服务运行在http://localhost:11434
- **AND** qwen3-vl:8b模型可用
- **WHEN** 帧被发送进行分类
- **THEN** 模型返回分类结果
- **AND** 在配置的超时时间内收到响应

#### Scenario: Model service unavailable
- **GIVEN** Ollama服务未运行
- **WHEN** 尝试分类
- **THEN** 记录错误，指示服务不可用
- **AND** 视频被标记为失败
- **AND** 尝试重试，最多达到配置的最大重试次数

#### Scenario: Model timeout
- **GIVEN** 模型耗时超过配置的超时时间
- **WHEN** 发生超时
- **THEN** 重试请求
- **AND** 如果所有重试都失败，视频被标记为失败

### Requirement: Classification Prompt Management
系统 SHALL 使用通用提示词进行初始分类。

#### Scenario: Generic classification prompt
- **GIVEN** 来自未知视频类型的帧
- **WHEN** 请求分类
- **THEN** 系统使用提示词要求模型选择：
  - A. 绘画艺术 (Drawing/Art)
  - B. 教程教学 (Tutorial)
  - C. 生活记录 (Vlog/Life)
  - D. 娱乐内容 (Entertainment)
  - E. 运动体育 (Sports)
  - F. 其他 (Other)

#### Scenario: Parse classification response
- **GIVEN** 格式为"分类: A\n理由: ..."的模型响应
- **WHEN** 解析响应
- **THEN** 提取类别"drawing"
- **AND** 保留理由用于日志记录

### Requirement: Classification Result Caching
系统 SHALL 缓存分类结果以提高效率。

#### Scenario: Cache hit
- **GIVEN** 视频之前被分类为"drawing"
- **WHEN** 启用skip_processed再次处理同一视频
- **THEN** 使用缓存的分类
- **AND** 不进行模型API调用

#### Scenario: Force reclassification
- **GIVEN** 视频之前已被分类
- **WHEN** 使用--force标志运行处理
- **THEN** 忽略缓存结果
- **AND** 再次执行分类
