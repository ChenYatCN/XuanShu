"""Force PyInstaller to resolve wizwalker from the active virtual environment."""

from pathlib import Path
import sys


def pre_find_module_path(api):
    venv_site_packages = (
        Path(sys.prefix) / "Lib" / "site-packages"
    ).resolve()
    api.search_dirs = [str(venv_site_packages)]
