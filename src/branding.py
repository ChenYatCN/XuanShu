# Modified 2026-09-09: XuanShu branding and path compatibility; see NOTICE.md.
"""XuanShu branding and non-destructive legacy settings migration (2026-09-09)."""
import os
import shutil
from pathlib import Path

APP_NAME = "XuanShu"
DISPLAY_NAME = "玄枢 XuanShu"
REPOSITORY_URL = "https://github.com/ChenYatCN/Deimos-Wizard101-main"
UPSTREAM_URL = "https://github.com/Deimos-Wizard101/Deimos-Wizard101"

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
