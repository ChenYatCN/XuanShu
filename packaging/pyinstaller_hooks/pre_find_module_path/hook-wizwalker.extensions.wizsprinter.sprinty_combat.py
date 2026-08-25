"""Bundle DeimosCN's combat-compatible SprintyCombat implementation.

The rest of wizsprinter comes from the active .venv.  Only this module carries
the local target-selection and Willcast fixes needed by DeimosCN.
"""

from pathlib import Path


def pre_find_module_path(api):
    module_dir = (
        Path.cwd()
        / "libs"
        / "wizsprinter"
        / "wizwalker"
        / "extensions"
        / "wizsprinter"
    ).resolve()
    module_file = module_dir / "sprinty_combat.py"
    if not module_file.exists():
        raise FileNotFoundError(
            f"Combat-compatible sprinty_combat.py not found: {module_file}"
        )
    api.search_dirs = [str(module_dir)]
