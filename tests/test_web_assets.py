"""Keep the existing browser boot order and ship every referenced static asset."""
from html.parser import HTMLParser
import hashlib
import json
from pathlib import Path
import unittest


class ScriptParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.scripts = []

    def handle_starttag(self, tag, attrs):
        if tag == "script":
            self.scripts.append(dict(attrs))


class WebAssetTests(unittest.TestCase):
    def test_extracted_javascript_preserves_original_statements(self):
        root = Path(__file__).resolve().parents[1]
        expected = json.loads((root / "tests/fixtures/refactor/frontend_fingerprints.json").read_text())
        for filename, digest in expected.items():
            with self.subTest(script=filename):
                self.assertEqual(hashlib.sha256((root / "web/public" / filename).read_bytes()).hexdigest(), digest)

    def test_all_classic_scripts_exist_and_boot_after_definitions(self):
        root = Path(__file__).resolve().parents[1] / "web/public"
        parser = ScriptParser()
        parser.feed((root / "index.html").read_text())
        sources = [entry["src"] for entry in parser.scripts]
        self.assertEqual(sources[0], "/js/core.js")
        self.assertEqual(sources[-2:], ["/app.js", "/shortcuts.js"])
        self.assertEqual(len(set(sources)), len(sources))
        for entry in parser.scripts:
            with self.subTest(script=entry["src"]):
                self.assertIn("defer", entry)
                self.assertNotIn("async", entry)
                self.assertNotIn("type", entry)
                self.assertTrue((root / entry["src"].lstrip("/")).is_file())


if __name__ == "__main__":
    unittest.main()
