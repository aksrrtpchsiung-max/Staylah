"""Compatibility entry point for property_agent.search.providers.osm."""
from pathlib import Path
import sys
if __name__ == "__main__" and __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[0]))
import importlib as _importlib
import sys as _sys

if __name__ == "__main__":
    import runpy
    runpy.run_module("property_agent.search.providers.osm", run_name="__main__")
else:
    _sys.modules[__name__] = _importlib.import_module("property_agent.search.providers.osm")
