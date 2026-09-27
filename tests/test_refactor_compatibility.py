"""Public contracts and behavior stay stable after retiring old source paths."""
import ast
import importlib
import inspect
import json
from pathlib import Path
import typing
import unittest

ROOT = Path(__file__).resolve().parents[1]
MODULE_MAP = json.loads((ROOT / 'tests/fixtures/refactor/module-map.json').read_text())


class CompatibilityTests(unittest.TestCase):
    def test_retired_paths_are_absent_and_canonical_modules_import(self):
        for old, new in {**MODULE_MAP, 'contracts_v0': 'property_agent.contracts'}.items():
            with self.subTest(module=old):
                self.assertFalse((ROOT / (old.replace('.', '/') + '.py')).exists())
                self.assertTrue(importlib.import_module(new).__file__)

    def test_public_signatures_use_the_single_shared_contract(self):
        from property_agent import contracts
        from property_agent.search import api
        from property_agent.domain import validation
        self.assertIs(typing.get_type_hints(api.fulfill_requirements)['request'], contracts.RequirementRequest)
        self.assertIs(api.ContractViolation, contracts.ContractViolation)
        with self.assertRaises(contracts.ContractViolation):
            raise validation.ContractViolation('INVALID_INPUT', 'request', 'bad input')

    def test_evaluation_entry_points_share_model_configuration(self):
        from property_agent.evaluation import service, configuration
        from property_agent.decision import module_c
        original = configuration._keyword_matcher
        marker = object()
        try:
            service.configure_keyword_matcher(marker)
            self.assertIs(configuration._resolve_keyword_matcher()[0], marker)
            self.assertIs(service, module_c.part_c)
        finally:
            service.configure_keyword_matcher(original)

    def test_runtime_paths_still_point_to_original_resources(self):
        from property_agent.runtime import model_client, settings
        from property_agent.runtime.paths import PROJECT_ROOT
        self.assertEqual(PROJECT_ROOT, ROOT)
        self.assertEqual(model_client.DEFAULT_ENV_FILE, ROOT / '.env')
        self.assertEqual(settings.DEFAULT_ENV_FILE, ROOT / '.env')
        self.assertEqual(settings.DEFAULT_RUNTIME_FILE, ROOT / 'runtime.toml')

    def test_public_b_signature_is_unchanged(self):
        from property_agent.search import api
        parameters = inspect.signature(api.fulfill_requirements).parameters
        self.assertEqual(list(parameters), ['request', 'ctx'])
        self.assertEqual(parameters['ctx'].kind, inspect.Parameter.KEYWORD_ONLY)

    def test_application_and_tools_do_not_import_retired_modules(self):
        retired_roots = {name.split('.')[0] for name in MODULE_MAP} | {'contracts_v0', 'test_all', 'test_search', 'deepseek_agent'}
        files = []
        for directory in ['property_agent', 'web', 'scripts', 'evaluation_suite']:
            files.extend((ROOT / directory).rglob('*.py'))
        for file in files:
            for node in ast.walk(ast.parse(file.read_text())):
                names = ([node.module] if isinstance(node, ast.ImportFrom) and not node.level else
                         [item.name for item in node.names] if isinstance(node, ast.Import) else [])
                for name in names:
                    with self.subTest(file=str(file.relative_to(ROOT)), line=node.lineno, module=name):
                        self.assertNotIn(name.split('.')[0], retired_roots)


if __name__ == '__main__':
    unittest.main()
