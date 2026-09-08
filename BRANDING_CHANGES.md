# XuanShu 修改文件记录

修改日期：2026-09-09。保留原作者版权与 GPL-3.0-only。

- .github/workflows/ci.yml → .github/workflows/ci.yml
- .github/workflows/develop.yml → .github/workflows/develop.yml
- .github/workflows/release.yml → .github/workflows/release.yml
- .gitignore → .gitignore
- Deimos-logo.ico → XuanShu-logo.ico
- Deimos-logo.png → XuanShu-logo.png
- DeimosCN.code-workspace → XuanShu.code-workspace
- DeimosCN.py → XuanShu.py
- DeimosCN.spec → XuanShu.spec
- DeimosCN打包.bat → XuanShu打包.bat
- README.md → README.md
- Up/第一次使用运行一次！.bat → Up/第一次使用运行一次！.bat
- _github/workflows/build.yml → _github/workflows/build.yml
- locale/base.pot → locale/base.pot
- locale/en.lang → locale/en.lang
- locale/zh-cn/LC_MESSAGES/messages.po → locale/zh-cn/LC_MESSAGES/messages.po
- locale/zh.lang → locale/zh.lang
- packaging/Deimos_entry.py → packaging/XuanShu_entry.py
- packaging/pyinstaller_hooks/pre_find_module_path/hook-wizwalker.extensions.wizsprinter.sprinty_combat.py → packaging/pyinstaller_hooks/pre_find_module_path/hook-wizwalker.extensions.wizsprinter.sprinty_combat.py
- packaging/verify_bundle.py → packaging/verify_bundle.py
- pyproject.toml → pyproject.toml
- scripts/release.ps1 → scripts/release.ps1
- src/auto_fish_original_adapter.py → src/auto_fish_original_adapter.py
- src/auto_pet.py → src/auto_pet.py
- src/bot_registry.py → src/bot_registry.py
- src/client_resizing.py → src/client_resizing.py
- src/collect_catalog.py → src/collect_catalog.py
- src/deimosgui.py → src/legacy_gui.py
- src/entity_collision.py → src/entity_collision.py
- src/gui/helpers.py → src/gui/helpers.py
- src/gui/icon_manager.py → src/gui/icon_manager.py
- src/gui/popups.py → src/gui/popups.py
- src/gui/settings_dialog.py → src/gui/settings_dialog.py
- src/settings_manager.py → src/settings_manager.py
- src/updater.py → src/updater.py
- src/wizpatch_runner.py → src/wizpatch_runner.py
- tests/test_quest_task_lifecycle.py → tests/test_quest_task_lifecycle.py
- version_info.txt → version_info.txt
- .spec → .spec
- (新增) → src/branding.py
- src/lang.py → src/lang.py
- src/gui/main.py → src/gui/main.py
- src/gui/tab_hotkeys.py → src/gui/tab_hotkeys.py
- (新增) → NOTICE.md
- (新增) → 启动XuanShu.bat

额外修复：src/gui/main.py 的任务栏标识，src/gui/icon_manager.py 的永久快捷方式图标引用；新增 tests/test_branding.py。原有脚本弹窗未提交改动保留。
