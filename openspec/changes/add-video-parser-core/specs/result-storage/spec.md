## ADDED Requirements

### Requirement: JSON Export
系统 SHALL 将处理后的视频结果导出为JSON格式。

#### Scenario: Export to JSON file
- **GIVEN** 10个已处理的带标签视频
- **WHEN** 结果导出为JSON
- **THEN** 创建一个包含所有视频记录的JSON文件
- **AND** 文件遵循需求中定义的结构

#### Scenario: Incremental JSON update
- **GIVEN** 一个包含50个已处理视频的现有results.json
- **AND** 10个新视频被处理
- **WHEN** 使用增量更新导出结果
- **THEN** JSON文件包含所有60个视频
- **AND** 现有视频记录被保留

### Requirement: CSV Export
系统 SHALL 将处理后的视频结果导出为CSV格式，以供电子表格应用程序使用。

#### Scenario: Export to CSV file
- **GIVEN** 已处理的带标签视频
- **WHEN** 结果导出为CSV
- **THEN** 创建一个CSV文件
- **AND** 每行代表一个视频
- **AND** 标签类别被扁平化为列

#### Scenario: Flatten nested tags for CSV
- **GIVEN** 一个tags={"type": ["素描"], "theme": ["人物", "肖像"]}的视频
- **WHEN** 导出为CSV
- **THEN** 列包括：type, theme1, theme2
- **OR** 标签在单列中以逗号分隔

### Requirement: SQLite Database Export (Optional)
系统 SHALL 支持将结果存储在SQLite数据库中以便查询。

#### Scenario: Export to SQLite
- **GIVEN** 已处理的带标签视频
- **WHEN** 选择SQLite导出
- **THEN** 创建数据库文件
- **AND** 为视频和标签创建表
- **AND** 为常见查询创建索引

### Requirement: Incremental Processing
系统 SHALL 在配置时跳过已处理的视频。

#### Scenario: Skip processed videos
- **GIVEN** 一个现有结果文件，视频A被标记为已处理
- **WHEN** 使用skip_processed=true运行处理
- **AND** 再次遇到视频A
- **THEN** 跳过视频A
- **AND** 使用缓存结果

#### Scenario: Force reprocess
- **GIVEN** 一个现有结果文件中的视频A
- **WHEN** 使用--force标志运行处理
- **AND** 遇到视频A
- **THEN** 重新处理视频A
- **AND** 结果在输出中更新

### Requirement: Output Metadata
系统 SHALL 在输出文件中包含元数据。

#### Scenario: Output includes processing metadata
- **GIVEN** 结果被导出
- **WHEN** 创建输出文件
- **THEN** 它包括：
  - 版本号
  - 处理时间戳
  - 使用的配置版本
  - 已处理的视频总数

### Requirement: Configurable Output Location
系统 SHALL 允许用户指定输出文件位置。

#### Scenario: Default output location
- **GIVEN** 未指定输出位置
- **WHEN** 处理完成
- **THEN** 结果保存到当前目录中的"video_tags.json"

#### Scenario: Custom output location
- **GIVEN** 指定--output /custom/path/results.json
- **WHEN** 处理完成
- **THEN** 结果保存到指定路径
