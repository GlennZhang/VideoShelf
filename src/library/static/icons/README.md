# 绘画资料库图标

设计：灰蓝底色与暖白色的画笔播放符号，呼应绘画视频资料库及现有界面。

- `app-icon.png`：生成原图。
- `app-icon-512.png`：512 × 512 应用图标，侧栏使用。
- `favicon-16.png` / `favicon-32.png`：浏览器标签页图标。
- `favicon.ico`：包含 16 / 32 像素 PNG 帧的 ICO。
- `apple-touch-icon.png`：180 × 180 触屏图标。

制作方式：内置 image_gen 生成并清理，sips 缩放，Python 标准库封装 ICO；无新增运行依赖。

最终生成提示词：

> Clean up this app icon into a perfectly flat two-color icon. Preserve the exact ivory play-triangle paintbrush silhouette. Replace ALL background inside the rounded square with uniform opaque solid slate blue #4a6b8a, eliminating every dark patch, transparency hole, texture and gradient. Outer canvas solid slate blue too, full bleed square, no exterior margins. Symbol warm ivory #f7f6f3. No shadows or text. Single square production app icon.
