"""Check canonical contracts against the schema captured before retiring aliases."""
import json
from pathlib import Path
import typing
import unittest

from property_agent import contracts


class ContractsSyncTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.reference = json.loads((Path(__file__).parent / 'fixtures/refactor/contract-schema.json').read_text())

    def test_no_type_is_missing_from_the_baseline(self):
        names = {name for name, value in vars(contracts).items()
                 if isinstance(value, type) and value.__module__ == contracts.__name__
                 and typing.is_typeddict(value)}
        self.assertEqual(names, set(self.reference))

    def test_annotations_match(self):
        for name, expected in self.reference.items():
            value = getattr(contracts, name)
            with self.subTest(type=name):
                self.assertEqual({key: str(item).replace('property_agent.contracts.', '')
                    for key, item in value.__annotations__.items()}, expected['annotations'])
                self.assertEqual(sorted(value.__required_keys__), expected['required_keys'])

    def test_amounts_are_integers_not_strings(self):
        self.assertEqual(str(contracts.Price.__annotations__['amount']), 'int | None')
        self.assertEqual(str(contracts.HardConstraints.__annotations__['max_price']), 'int | None')

    def test_price_scope_was_replaced_by_listing_scope(self):
        self.assertNotIn('scope', contracts.Price.__annotations__)
        self.assertIn('listing_scope', contracts.ListingAttributes.__annotations__)

    def test_run_context_carries_user_id(self):
        self.assertIn('user_id', contracts.RunContext.__annotations__)


if __name__ == '__main__':
    unittest.main()
