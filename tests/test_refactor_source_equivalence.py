"""Structural moves preserve original function bodies, including every prompt.

Only import paths are normalized. This complements behavioral tests for branches
whose live providers cannot be exhaustively exercised on every commit.
"""
import ast
import hashlib
import importlib
import inspect
import json
from pathlib import Path
import textwrap
import unittest

ROOT = Path(__file__).resolve().parents[1]
MODULE_MAP = json.loads((ROOT / "docs/refactoring/module-map.json").read_text())
MODULE_MAP["contracts_v0"] = "property_agent.contracts"


class CanonicalImports(ast.NodeTransformer):
    def visit_ImportFrom(self, node):
        node.module = MODULE_MAP.get(node.module, node.module)
        return node

    def visit_Import(self, node):
        if len(node.names) == 1 and node.names[0].name in MODULE_MAP:
            entry = node.names[0]
            parent, name = MODULE_MAP[entry.name].rsplit(".", 1)
            return ast.ImportFrom(module=parent, names=[ast.alias(name=name,
                asname=entry.asname or entry.name)], level=0)
        return node


def fingerprint(source):
    tree = CanonicalImports().visit(ast.parse(textwrap.dedent(source)))
    return hashlib.sha256(ast.dump(tree.body[0], include_attributes=False).encode()).hexdigest()


class SourceEquivalenceTests(unittest.TestCase):
    def test_extracted_functions_and_methods_preserve_original_bodies(self):
        records = json.loads((ROOT / "tests/fixtures/refactor/source_fingerprints.json").read_text())
        for target, expected in records.items():
            with self.subTest(symbol=target):
                module, symbol = target.split(":")
                value = importlib.import_module(module)
                for component in symbol.split("."):
                    value = getattr(value, component)
                self.assertEqual(fingerprint(inspect.getsource(value)), expected)


if __name__ == "__main__":
    unittest.main()
