"""Compatibility entry point for property_agent.search.capabilities.amenities."""
from pathlib import Path
import sys
if __name__ == "__main__" and __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import importlib as _importlib
import sys as _sys

if __name__ == "__main__":
    import runpy
    runpy.run_module("property_agent.search.capabilities.amenities", run_name="__main__")
else:
    _sys.modules[__name__] = _importlib.import_module("property_agent.search.capabilities.amenities")
