# Modified 2026-09-09: XuanShu branding and path compatibility; see NOTICE.md.
"""XuanShu branding and non-destructive legacy settings migration (2026-09-09)."""

import os
import shutil
import sys
from pathlib import Path

APP_NAME = "XuanShu"
DISPLAY_NAME = "玄枢 · XuanShu"
REPOSITORY_URL = "https://github.com/ChenYatCN/Deimos-Wizard101-main"
UPSTREAM_URL = "https://github.com/Deimos-Wizard101/Deimos-Wizard101"


def runtime_dir() -> Path:
    """The EXE's folder, or the source checkout when launched from Python."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[1]


def runtime_data_dir() -> Path:
    """Regenerable caches and temporary output beside the running program."""
    target = runtime_dir() / f"{APP_NAME}Data"
    target.mkdir(parents=True, exist_ok=True)
    return target


def appdata_dir() -> Path:
    base = Path(os.environ.get("APPDATA") or Path.home())
    target = base / APP_NAME
    target.mkdir(parents=True, exist_ok=True)
    marker = target / ".legacy-migrated"
    if not marker.exists():
        # Copy user settings only, never cached executables or credentials.
        # Existing XuanShu choices win; keep all originals for rollback.
        for legacy in ("DeimosCN", "Deimos-CN", "Deimos"):
            for name in ("settings.json", "default_theme.json", "custom_icon.ico"):
                source, dest = base / legacy / name, target / name
                if source.is_file() and not dest.exists():
                    shutil.copy2(source, dest)
        marker.write_text("2026-09-09", encoding="utf-8")
    return target
