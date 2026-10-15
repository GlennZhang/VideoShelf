## ADDED Requirements

### Requirement: Video File Discovery
系统 SHALL 扫描指定目录并识别所有支持的视频文件。

#### Scenario: Successful scan finds videos
- **GIVEN** 一个包含视频文件的目录（.mp4, .avi, .mov, .mkv, .webm）
- **WHEN** 扫描器处理该目录
- **THEN** 所有视频文件都被发现并返回到列表中

#### Scenario: Scan filters non-video files
- **GIVEN** 一个包含混合文件类型的目录（视频、图片、文档）
- **WHEN** 扫描器处理该目录
- **THEN** 结果中仅包含支持的视频格式

#### Scenario: Recursive directory scanning
- **GIVEN** 一个子目录中包含视频的目录
- **WHEN** 启用递归扫描
- **THEN** 所有子目录中的视频都被发现

#### Scenario: Non-recursive scanning
- **GIVEN** 一个子目录中包含视频的目录
- **WHEN** 禁用递归扫描（默认）
- **THEN** 仅发现顶级目录中的视频

### Requirement: Video Metadata Extraction
系统 SHALL 从发现的视频文件中提取元数据。

#### Scenario: Extract duration
- **GIVEN** 一个有效的视频文件
- **WHEN** 提取元数据
- **THEN** 捕获视频时长（秒）

#### Scenario: Extract file size
- **GIVEN** 一个有效的视频文件
- **WHEN** 提取元数据
- **THEN** 捕获文件大小（字节）

#### Scenario: Handle corrupted video file
- **GIVEN** 一个损坏或无效的视频文件
- **WHEN** 元数据提取失败
- **THEN** 记录错误并跳过该文件，而不终止扫描

### Requirement: Scan Progress Reporting
系统 SHALL 向用户报告扫描进度。

#### Scenario: Display scan statistics
- **GIVEN** 正在进行的扫描操作
- **WHEN** 扫描完成
- **THEN** 显示找到的视频总数
- **AND** 显示所有视频的总大小
