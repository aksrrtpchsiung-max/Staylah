"""Legacy imports must share implementations, configuration and exception identity."""
import importlib
import inspect
import json
from pathlib import Path
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]


class CompatibilityTests(unittest.TestCase):
    def test_legacy_modules_are_aliases_not_copies(self):
        mapping = json.loads((ROOT / "docs/refactoring/module-map.json").read_text())
        for old, new in mapping.items():
            with self.subTest(module=old):
                self.assertIs(importlib.import_module(old), importlib.import_module(new))

    def test_root_contract_types_are_canonical(self):
        import contracts_v0 as legacy
        from property_agent import contracts
        for name, value in vars(contracts).items():
            if isinstance(value, type) and value.__module__ == contracts.__name__:
                self.assertIs(getattr(legacy, name), value)
        with self.assertRaises(contracts.ContractViolation):
            raise legacy.ContractViolation("INVALID_INPUT", "request", "bad input")

    def test_monkeypatching_legacy_factory_reaches_canonical_api(self):
        import api
        from property_agent.search import api as canonical
        sentinel = object()
        with patch.object(api, "create_live_fulfillment_service", return_value=sentinel):
            self.assertIs(canonical.create_live_fulfillment_service(), sentinel)

    def test_runtime_paths_still_point_to_original_resources(self):
        import config
        import runtime_settings
        from property_agent.runtime.paths import PROJECT_ROOT
        self.assertEqual(PROJECT_ROOT, ROOT)
        self.assertEqual(config.DEFAULT_ENV_FILE, ROOT / ".env")
        self.assertEqual(runtime_settings.DEFAULT_ENV_FILE, ROOT / ".env")
        self.assertEqual(runtime_settings.DEFAULT_RUNTIME_FILE, ROOT / "runtime.toml")

    def test_public_b_signature_is_unchanged(self):
        import api
        parameters = inspect.signature(api.fulfill_requirements).parameters
        self.assertEqual(list(parameters), ["request", "ctx"])
        self.assertEqual(parameters["ctx"].kind, inspect.Parameter.KEYWORD_ONLY)


if __name__ == "__main__":
    unittest.main()
