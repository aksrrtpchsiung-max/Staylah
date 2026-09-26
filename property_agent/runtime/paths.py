"""Resource locations independent of the module importing them.

Source and editable installs retain the original repository-relative behavior.
An installed wheel carries runtime.toml as package data; the existing settings
loader still supports explicit paths and its original built-in fallback.
"""
from pathlib import Path

PACKAGE_ROOT = Path(__file__).resolve().parents[1]
PROJECT_ROOT = PACKAGE_ROOT.parent
