"""Resource locations independent of the module importing them.

Source and editable installs retain the original repository-relative behavior.
Installed wheels use the settings loader's original built-in defaults when no
runtime.toml is present. Explicit configuration paths remain supported.
"""
from pathlib import Path

PACKAGE_ROOT = Path(__file__).resolve().parents[1]
PROJECT_ROOT = PACKAGE_ROOT.parent
