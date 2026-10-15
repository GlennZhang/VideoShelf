## ADDED Requirements

### Requirement: Tag Extraction from Analysis
系统 SHALL 从AI生成的内容描述中提取结构化标签。

#### Scenario: Extract drawing tags
- **GIVEN** 一个描述"a realistic pencil portrait of a young woman"的绘画视频分析
- **WHEN** 提取标签
- **THEN** 标签包括：type=["素描"], theme=["人物", "肖像"], style=["写实"]

#### Scenario: Extract tutorial tags
- **GIVEN** 一个描述"Python programming for beginners"的教程分析
- **WHEN** 提取标签
- **THEN** 标签包括：field=["编程"], type=["课程讲解"], level=["入门"]

### Requirement: Tag Validation
系统 SHALL 根据配置的分类法验证提取的标签。

#### Scenario: Valid tag passes validation
- **GIVEN** tags_config.yaml定义drawing.type包含"素描"
- **AND** 分析建议类型为"素描"
- **WHEN** 执行验证
- **THEN** 标签被接受

#### Scenario: Invalid tag rejected
- **GIVEN** tags_config.yaml定义drawing.type不包含"油画"
- **AND** 分析建议类型为"油画"
- **WHEN** 执行验证
- **THEN** 标签被拒绝
- **AND** 记录警告

#### Scenario: Free-text tag category
- **GIVEN** tags_config.yaml定义drawing.content设置free_text: true
- **WHEN** 分析建议任何内容描述
- **THEN** 无论预定义值如何，标签都被接受

### Requirement: Tag Configuration Management
系统 SHALL 从YAML配置管理标签定义。

#### Scenario: Load tag taxonomy
- **GIVEN** 一个包含类别和子类别定义的tags_config.yaml文件
- **WHEN** TagManager被初始化
- **THEN** 所有类别、子类别和允许的标签都被加载

#### Scenario: Get allowed tags for subcategory
- **GIVEN** 定义了drawing.theme的tags_config.yaml
- **WHEN** 调用get_allowed_tags("drawing", "theme")
- **THEN** 返回允许的主题标签列表

### Requirement: Structured Tag Output
系统 SHALL 生成JSON格式的标签对象。

#### Scenario: Complete tag object
- **GIVEN** 一个完全分析的视频
- **WHEN** 生成标签
- **THEN** 输出包括：
  ```json
  {
    "video_path": "/path/to/video.mp4",
    "category": "drawing",
    "tags": {
      "type": ["素描"],
      "theme": ["人物"],
      "style": ["写实"],
      "stage": ["完成"],
      "content": ["人物头像", "女青年"]
    },
    "description": "...",
    "confidence": 0.85,
    "timestamp": "2025-01-16T10:30:00Z"
  }
  ```

### Requirement: Custom Tag Support
系统 SHALL 支持自由文本类别的自定义标签。

#### Scenario: High-frequency custom tag
- **GIVEN** 多个标记为content="女青年"的视频
- **WHEN** 分析标签统计
- **THEN** "女青年"被识别为高频自定义标签
- **AND** 可以建议将其添加到配置中

#### Scenario: Multi-value custom tags
- **GIVEN** tags_config.yaml定义other.custom设置multi_value: true
- **WHEN** 生成自定义标签
- **THEN** 可以分配多个自定义标签
