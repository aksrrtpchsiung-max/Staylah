"""Compatibility entry point for property_agent.runtime.settings."""
import importlib as _importlib
import sys as _sys

if __name__ == "__main__":
    import runpy
    runpy.run_module("property_agent.runtime.settings", run_name="__main__")
else:
    _sys.modules[__name__] = _importlib.import_module("property_agent.runtime.settings")
