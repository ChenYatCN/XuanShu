# Modified 2026-09-09: XuanShu branding and path compatibility; see NOTICE.md.
import os
import shutil
import subprocess
import sys
from pathlib import Path
from src.branding import appdata_dir

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QIcon
from PyQt6.QtWidgets import QFileDialog, QMessageBox

from src.gui.helpers import resource_path

APP_NAME = "XuanShu"
CUSTOM_ICON_NAME = "custom_icon.ico"
DEFAULT_ICON_NAME = "XuanShu-logo.ico"


def get_appdata_dir() -> Path:
    return appdata_dir()


def get_custom_icon_path() -> Path:
    return get_appdata_dir() / CUSTOM_ICON_NAME


def get_default_icon_path() -> str:
    return resource_path(DEFAULT_ICON_NAME)


def get_current_icon_path() -> str:
    """
    优先使用用户自定义图标。
    如果用户没有设置，就使用默认 XuanShu-logo.ico。
    """
    custom_icon = get_custom_icon_path()

    if custom_icon.exists() and custom_icon.suffix.lower() == ".ico":
        icon = QIcon(str(custom_icon))
        if not icon.isNull():
            return str(custom_icon)

    return get_default_icon_path()


def apply_app_icon(app=None, window=None):
    """
    设置软件窗口图标、任务栏图标。
    """
    icon_path = get_current_icon_path()

    if not icon_path or not os.path.exists(icon_path):
        return

    icon = QIcon(icon_path)

    if icon.isNull():
        return

    if app is not None:
        app.setWindowIcon(icon)

    if window is not None:
        window.setWindowIcon(icon)


def refresh_titlebar_icon(ctx):
    """
    刷新软件内部标题栏显示的小图标。
    """
    if not hasattr(ctx, "app_icon_label"):
        return

    icon_path = get_current_icon_path()

    if not icon_path or not os.path.exists(icon_path):
        return

    icon = QIcon(icon_path)

    if icon.isNull():
        return

    ctx.app_icon_label.setPixmap(icon.pixmap(20, 20))


def choose_custom_icon(ctx, parent=None):
    """
    用户选择自己的 ico 图标。
    """
    parent = parent or ctx.window

    file_path, _ = QFileDialog.getOpenFileName(
        parent, "选择软件图标", "", "ICO 图标文件 (*.ico)"
    )

    if not file_path:
        return

    if not file_path.lower().endswith(".ico"):
        QMessageBox.warning(parent, "格式错误", "请选择 .ico 格式的图标文件。")
        return

    test_icon = QIcon(file_path)

    if test_icon.isNull():
        QMessageBox.warning(
            parent, "图标无效", "这个 ico 文件无法识别，请换一个标准 ico 图标。"
        )
        return

    target_path = get_custom_icon_path()
    shutil.copy2(file_path, target_path)

    refresh_all_app_icons(ctx)

    QMessageBox.information(
        parent,
        "图标已更换",
        "软件图标已更换。\n\n软件内部标题栏会立即刷新，任务栏图标建议重启软件后查看。",
    )


def reset_custom_icon(ctx, parent=None):
    """
    恢复默认图标。
    """
    parent = parent or ctx.window

    custom_icon = get_custom_icon_path()

    if custom_icon.exists():
        custom_icon.unlink()

    refresh_all_app_icons(ctx)

    QMessageBox.information(
        parent, "已恢复默认", "已恢复默认图标。\n\n任务栏图标建议重启软件后查看。"
    )


def get_current_icon_pixmap(size: int = 80):
    """
    把当前软件图标转换成 QPixmap。
    可以用于 QLabel 显示。
    """
    icon_path = get_current_icon_path()

    if not icon_path or not os.path.exists(icon_path):
        return None

    icon = QIcon(icon_path)

    if icon.isNull():
        return None

    return icon.pixmap(size, size)


def set_label_icon(label, size: int = 80):
    """
    给 QLabel 设置当前软件图标。
    """
    pixmap = get_current_icon_pixmap(size)

    if pixmap is None or pixmap.isNull():
        return

    label.setPixmap(pixmap)
    label.setAlignment(Qt.AlignmentFlag.AlignCenter)
    label.update()


def refresh_all_app_icons(ctx):
    """
    刷新所有软件图标位置：
    1. 窗口 / 任务栏图标
    2. 顶部标题栏小图标
    3. 快捷键页右侧大 Logo
    """
    apply_app_icon(ctx.app, ctx.window)

    if hasattr(ctx, "app_icon_label"):
        set_label_icon(ctx.app_icon_label, 20)

    if hasattr(ctx, "tool_info_logo_label"):
        set_label_icon(ctx.tool_info_logo_label, 80)


def _ps_escape(value: str) -> str:
    return value.replace("'", "''")


def update_desktop_shortcut(ctx, shortcut_name: str = "XuanShu.lnk", parent=None):
    """
    创建或更新桌面快捷方式图标。
    注意：改的是 .lnk 快捷方式图标，不是 exe 本体图标。
    """
    parent = parent or ctx.window

    if not getattr(sys, "frozen", False):
        QMessageBox.warning(
            parent, "仅打包后可用", "更新桌面快捷方式图标需要在打包后的 exe 中使用。"
        )
        return

    exe_path = Path(sys.executable)
    icon_path = get_current_icon_path()

    if not icon_path or not os.path.exists(icon_path):
        QMessageBox.warning(parent, "图标不存在", "没有找到可用的软件图标。")
        return

    # The default icon lives in a temporary extraction directory; shortcuts
    # must use the permanent EXE icon instead. Custom icons live in AppData.
    if Path(icon_path) != get_custom_icon_path():
        icon_path = str(exe_path)

    ps_script = f"""
$WshShell = New-Object -ComObject WScript.Shell
$Desktop = $WshShell.SpecialFolders("Desktop")
$ShortcutPath = Join-Path $Desktop '{_ps_escape(shortcut_name)}'
$Shortcut = $WshShell.CreateShortcut($ShortcutPath)
$Shortcut.TargetPath = '{_ps_escape(str(exe_path))}'
$Shortcut.WorkingDirectory = '{_ps_escape(str(exe_path.parent))}'
$Shortcut.IconLocation = '{_ps_escape(str(icon_path))},0'
$Shortcut.Save()
"""

    try:
        subprocess.run(
            [
                "powershell",
                "-NoProfile",
                "-ExecutionPolicy",
                "Bypass",
                "-Command",
                ps_script,
            ],
            check=True,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )

        QMessageBox.information(
            parent,
            "快捷方式已更新",
            "桌面快捷方式图标已更新。\n\n如果桌面图标没有马上变化，重命名快捷方式或刷新桌面后再看。",
        )

    except Exception as e:
        QMessageBox.warning(parent, "快捷方式更新失败", f"更新桌面快捷方式失败：\n{e}")
