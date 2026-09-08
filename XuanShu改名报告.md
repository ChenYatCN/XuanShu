# 玄枢 XuanShu 本地改名结果

日期：2026-09-09

代码、资源引用、构建和运行产物已实际修改并验证。**外层目录改名尚未完成**：已按授权结束 12 个占用仓库的 Codex Node/REPL 辅助进程，工作目录占用检查清空后，Windows 仍返回“Access to the path is denied”。没有修改目录 ACL 或 Git 历史。

当前仓库：`D:\SuabataGodTool\DeimosCN-Project\DeimosCN`
目标仓库：`D:\SuabataGodTool\XuanShu-Project\XuanShu`
当前成品：`D:\SuabataGodTool\DeimosCN-Project\DeimosCN\dist\XuanShu.exe`

## 已完成

- 入口 XuanShu.py、XuanShu.spec、XuanShu打包.bat、XuanShu.code-workspace、packaging/XuanShu_entry.py；旧 GUI 文件改为 src/legacy_gui.py。
- 主窗口、任务栏应用标识、Qt 应用名、日志品牌及中英文语言资源使用 XuanShu / 玄枢。
- XuanShu-logo.ico/png 文件及资源引用；保留现有图像内容，本次没有新绘制“星罗”图标。
- XuanShuSettings 为主类，设置/主题/自定义图标统一使用 AppData/XuanShu；首次复制旧设置，已有新设置优先，原设置不删除。采集和碰撞缓存使用新目录重建。
- 桌面快捷方式生成逻辑使用 XuanShu.lnk；默认图标指向永久 EXE，避免指向运行结束即消失的解包目录。本次没有直接改用户桌面已有快捷方式。
- 修复跨工作目录的语言、图标资源加载；新增源码启动脚本。
- CI、开发构建、发布工作流、版本脚本、PyInstaller 验证器及发布资产统一名称；修正原本不存在的 spec 引用。版本元数据与入口统一为 2.1.3。
- 源码/发布链接使用现有 origin 地址，不猜测改名后的远程地址；上游 Wiki 与归属保留。
- README、NOTICE.md、BRANDING_CHANGES.md 记录来源及修改日期。GPL LICENSE 与 HEAD 原文逐字节一致。
- 新产物 dist/XuanShu.exe 及 SHA256 文件。旧 DeimosCN.exe 已移入 backups/pre-xuanshu-branding，避免混淆。
- 虚拟环境旧 editable .pth 引用已修复，并改名为 _editable_impl_xuanshu.pth。
- 原本未提交的脚本弹窗逻辑和测试完整保留，没有提交、推送或重写 Git 历史。

## 保留未改及原因

|位置/名称|原因|
|---|---|
|两个外层目录|Windows 在释放已定位进程后仍拒绝改名；这是未完成项，不是兼容性保留。|
|src/deimoslang、deimos_call、###deimos_expertmode 及相关测试|现有脚本语言、运行时协议及第三方脚本兼容。|
|DeimosSettings 别名|外部代码仍可导入旧公开类名，新代码使用 XuanShuSettings。|
|旧 AppData 名称及迁移测试|识别和迁移用户已有设置，不能删除这些匹配字符串。|
|_deimos_collect_search|现有客户端对象上的内部运行状态标记，保持内部兼容。|
|libs/updater 的 deimos-updater.exe 与 Cargo 包|沿用现有 Rust helper 合约；CI 名称修正为该真实名称。本机没有编译此可选助手，因此本地成品未包含它；在线自更新未端到端验证。|
|libs、wizwalker 中的上游包名、链接、版权|第三方依赖与来源归属，不作为本项目品牌改写。|
|origin/upstream URL、Deimos-Wizard101.wiki.git|真实远程地址和原项目归属；没有擅自重命名 GitHub 仓库。|
|.git、旧 build、backups、logs、myenv、安装元数据|提交历史、恢复数据、历史构建与第三方安装记录；不篡改旧文件来伪装新构建。|
|少量内部方向说明注释中的 deimos|不参与品牌显示或路径解析，详见残留清单。|

## 验证结果与边界

- 172 项 unittest 测试通过（包含新增 4 项品牌/配置/资源/更新资产回归测试）。
- 修改后的 Python 文件语法检查通过；主入口 import 成功。
- 源码 GUI 实际创建、显示及关闭；检查窗口标题、非空图标、语言资源。该项未启动游戏后台。离屏截图受测试环境字体影响，不作为字体视觉验收。
- 最终 PyInstaller 构建成功；verify_bundle.py 验证通过，包括 Qt、wizlaunch、wizpatch、采集数据、战斗兼容模块，未混入 Codex 运行库。
- 最终 EXE 实际启动并检测到可见的“玄枢 XuanShu v2.1.3”窗口；日志到达等待 Wizard101 客户端状态；发送正常关闭消息后退出码 0。
- 未测试真实游戏操作、在线发布、在线自更新及移动到目标目录后的启动。源码启动 BAT 的命令结构已检查，未将这项等同于 EXE 启动验证。

## 剩余目录操作

关闭 Codex 后，在本报告同目录运行 `完成目录改名.ps1`。它只重命名指定两层目录，修复 editable 路径和激活脚本，再运行语法检查。若仍拒绝访问，请保留报错，不要强制删除或更改 .git。完成后需在 Codex 中重新选择新项目目录；本次没有修改 Codex 的项目登记。

## 文件级修改清单

- `.github/workflows/ci.yml` → `.github/workflows/ci.yml`
- `.github/workflows/develop.yml` → `.github/workflows/develop.yml`
- `.github/workflows/release.yml` → `.github/workflows/release.yml`
- `.gitignore` → `.gitignore`
- `Deimos-logo.ico` → `XuanShu-logo.ico`
- `Deimos-logo.png` → `XuanShu-logo.png`
- `DeimosCN.code-workspace` → `XuanShu.code-workspace`
- `DeimosCN.py` → `XuanShu.py`
- `DeimosCN.spec` → `XuanShu.spec`
- `DeimosCN打包.bat` → `XuanShu打包.bat`
- `README.md` → `README.md`
- `Up/第一次使用运行一次！.bat` → `Up/第一次使用运行一次！.bat`
- `_github/workflows/build.yml` → `_github/workflows/build.yml`
- `locale/base.pot` → `locale/base.pot`
- `locale/en.lang` → `locale/en.lang`
- `locale/zh-cn/LC_MESSAGES/messages.po` → `locale/zh-cn/LC_MESSAGES/messages.po`
- `locale/zh.lang` → `locale/zh.lang`
- `packaging/Deimos_entry.py` → `packaging/XuanShu_entry.py`
- `packaging/pyinstaller_hooks/pre_find_module_path/hook-wizwalker.extensions.wizsprinter.sprinty_combat.py` → `packaging/pyinstaller_hooks/pre_find_module_path/hook-wizwalker.extensions.wizsprinter.sprinty_combat.py`
- `packaging/verify_bundle.py` → `packaging/verify_bundle.py`
- `pyproject.toml` → `pyproject.toml`
- `scripts/release.ps1` → `scripts/release.ps1`
- `src/auto_fish_original_adapter.py` → `src/auto_fish_original_adapter.py`
- `src/auto_pet.py` → `src/auto_pet.py`
- `src/bot_registry.py` → `src/bot_registry.py`
- `src/client_resizing.py` → `src/client_resizing.py`
- `src/collect_catalog.py` → `src/collect_catalog.py`
- `src/deimosgui.py` → `src/legacy_gui.py`
- `src/entity_collision.py` → `src/entity_collision.py`
- `src/gui/helpers.py` → `src/gui/helpers.py`
- `src/gui/icon_manager.py` → `src/gui/icon_manager.py`
- `src/gui/popups.py` → `src/gui/popups.py`
- `src/gui/settings_dialog.py` → `src/gui/settings_dialog.py`
- `src/settings_manager.py` → `src/settings_manager.py`
- `src/updater.py` → `src/updater.py`
- `src/wizpatch_runner.py` → `src/wizpatch_runner.py`
- `tests/test_quest_task_lifecycle.py` → `tests/test_quest_task_lifecycle.py`
- `version_info.txt` → `version_info.txt`
- `.spec` → `.spec`
- `(新增)` → `src/branding.py`
- `src/lang.py` → `src/lang.py`
- `src/gui/main.py` → `src/gui/main.py`
- `src/gui/tab_hotkeys.py` → `src/gui/tab_hotkeys.py`
- `(新增)` → `NOTICE.md`
- `(新增)` → `启动XuanShu.bat`

后续修正/新增：scripts/release_notify.ps1、tests/test_branding.py、BRANDING_CHANGES.md、.venv/Lib/site-packages/_editable_impl_xuanshu.pth、dist/XuanShu.exe.sha256。

文本残留的文件及行号见同目录 `Deimos残留位置.txt`（包括归属、真实 URL、内部兼容名及修改记录中的旧名称）。生成数据和安装环境不做逐字扫描。

LICENSE SHA256: `3972dc9744f6499f0f9b2dbf76696f2ae7ad8af9b23dde66d6af86c9dfb36986`

XuanShu.exe SHA256: `cca2597b91679662bd79b4c6e5e8a9cc323ef519eced7ed8ac898a1882688bf9`
