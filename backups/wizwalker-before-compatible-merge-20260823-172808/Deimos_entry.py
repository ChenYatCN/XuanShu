"""PyInstaller-only entry point.

Keeping the entry point outside the repository root prevents PyInstaller from
placing the legacy root-level ``wizwalker`` ahead of ``libs/wizwalker``.
"""

from Deimos import run


if __name__ == "__main__":
    run()
