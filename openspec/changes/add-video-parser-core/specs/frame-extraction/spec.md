## ADDED Requirements

### Requirement: Frame Extraction Strategies
系统 SHALL 支持多种策略从视频中提取关键帧。

#### Scenario: Interval-based extraction
- **GIVEN** 一个时长60秒的视频文件
- **AND** 间隔策略设置为5秒
- **WHEN** 提取帧
- **THEN** 大约12帧以规则间隔被捕获

#### Scenario: Fixed-count extraction
- **GIVEN** 任意时长的视频文件
- **AND** 固定数量策略设置为10帧
- **WHEN** 提取帧
- **THEN** 正好10帧均匀分布在整个视频中

#### Scenario: Maximum frames limit
- **GIVEN** 一个长视频（例如1小时）
- **AND** 间隔策略设置为1秒
- **AND** max_frames设置为10
- **WHEN** 提取帧
- **THEN** 最多提取10帧

### Requirement: Frame Data Handling
系统 SHALL 高效提取和管理帧数据。

#### Scenario: Extract frame with timestamp
- **GIVEN** 在15.5秒处提取的一帧
- **WHEN** 处理该帧
- **THEN** 该帧包含其时间戳（15.5s）供参考

#### Scenario: Return frame as image data
- **GIVEN** 成功的帧提取
- **WHEN** 返回帧
- **THEN** 每帧作为适合AI模型输入的图像数据提供
- **AND** 帧可以选择性保存为临时图像文件

### Requirement: Frame Extraction Error Handling
系统 SHALL 优雅地处理视频格式问题。

#### Scenario: Unsupported video codec
- **GIVEN** 一个包含不支持编解码器的视频文件
- **WHEN** 帧提取失败
- **THEN** 记录描述性错误
- **AND** 视频被标记为失败，应用程序不会崩溃

#### Scenario: Empty or corrupted video
- **GIVEN** 一个没有帧的视频文件
- **WHEN** 尝试提取
- **THEN** 记录错误
- **AND** 返回空帧列表

### Requirement: Configurable Extraction Parameters
系统 SHALL 允许配置提取行为。

#### Scenario: Default parameters
- **GIVEN** 未提供自定义提取参数
- **WHEN** 提取帧
- **THEN** 默认间隔为5秒
- **AND** 默认max_frames为10

#### Scenario: Custom parameters via config
- **GIVEN** 一个指定interval=10和max_frames=20的配置文件
- **WHEN** 使用配置提取帧
- **THEN** 每10秒提取一帧
- **AND** 最多可提取20帧
