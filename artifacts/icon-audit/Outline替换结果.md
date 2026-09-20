# Outline 线性图标

此前替换的 10 个图标已统一为 Outline：停止、书、卡牌、消息、链节、时间、复制、用户组、用户、击中。复制日志继续复用复制图标。

新增 assets/icon/outline/ 下的 10 个 SVG，为项目内制作的线性版本，不冒充原图标库提供的官方变体。原始实心图标库保留不动。统一 fill="none"、stroke="currentColor"、stroke-width="2"、圆角线端与连接。

src/gui/helpers.py 切换资源目录；XuanShu.spec 同步打包目录；tests/test_library_icons.py 增加线性样式断言。保留按钮尺寸、间距、主题着色、原有功能与独立最小化。

14 项针对性测试通过，已查看整组 16／32 像素图标及底部按钮渲染预览。未重新打包或验证已安装 EXE。
