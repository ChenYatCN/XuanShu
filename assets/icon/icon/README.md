# 当前应用图标

- `simple.png`：用户提供的 `缩小.png`（新版任务栏款）。
- `full.png`：用户提供的正常款，同时用于项目根目录 `XuanShu-logo.png`。
- `icon_16/24/32/48/64.png`：由 `缩小.png` 等比缩放并居中补透明边生成。
- `icon_128/256.png`：由正常款等比缩放并居中补透明边生成。
- 根目录 `XuanShu-logo.ico`：ImageMagick 7.1.2-31 合并上述 7 帧，各帧保留 32 位 RGBA。
- 替换前资源：`../previous-logo-20261020/`，含校验清单。

Windows 按请求的像素尺寸和系统缩放选取 ICO 帧，不按“任务栏/其他窗口”名称区分。

- 上一版任务栏资源备份：`../previous-taskbar-20261020/`。
