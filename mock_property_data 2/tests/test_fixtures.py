"""Behavioral and integrity regressions; no production agent calls or network."""
import copy
import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'))
from contract_validation import ValidationError,validate_listing,check_type,contracts
from generate import generate
from validate import validate,expected_screen_bucket

class FixtureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.listings=json.loads((ROOT/'output/listings.json').read_text())
        cls.records=[json.loads(s) for s in (ROOT/'output/records54.jsonl').read_text().splitlines()]
        cls.bycase={r['fields']['extra_attributes']['scenario']:l for r,l in zip(cls.records,cls.listings)}
        cls.profile=json.loads((ROOT/'output/fixtures/profile.json').read_text())

    def test_full_deliverable(self):
        result=validate(write_report=False)
        self.assertTrue(result['passed'])
        self.assertEqual(result['listing_count'],1000)

    def test_all_attribute_boolean_states_are_represented(self):
        for field in ['ensuite_bathroom','owner_stays','utilities_included','wifi_included','visitors_allowed','pets_allowed']:
            self.assertEqual({l['attributes'][field] for l in self.listings},{None,False,True},field)

    def test_whole_unit_missing_rules_are_more_common_than_room(self):
        def rate(scope):
            rows=[r for r in self.records if r['fields']['listing_scope']==scope and r['fields']['extra_attributes']['scenario']=='ordinary']
            return sum(r['fields']['utilities_included'] is None for r in rows)/len(rows)
        self.assertGreater(rate('whole_unit'),rate('room'))

    def test_rules_preserve_correlations(self):
        rows=[l for l,r in zip(self.listings,self.records) if r['fields']['extra_attributes']['scenario']=='ordinary']
        self.assertTrue(all(l['attributes']['wifi_included']==l['attributes']['utilities_included'] for l in rows))

    def test_exact_budget_boundary(self):
        self.assertEqual(expected_screen_bucket(self.bycase['budget_at_limit'],self.profile),'eligible')
        self.assertEqual(expected_screen_bucket(self.bycase['budget_over_by_one'],self.profile),'rejected')

    def test_unknown_hard_facts_do_not_pass(self):
        for case in ['unknown_price','conflicting_price','unknown_scope','unknown_location','unknown_bedrooms','unknown_status']:
            self.assertEqual(expected_screen_bucket(self.bycase[case],self.profile),'needs_verification')

    def test_null_id_does_not_collapse_identity(self):
        rows=[l for l in self.listings if l['source_listing_id'] is None]
        self.assertGreater(len(rows),1)
        self.assertEqual(len(rows),len({l['listing_key'] for l in rows}))

    def test_cannot_silently_flip_unknown_boolean(self):
        l=copy.deepcopy(self.bycase['budget_at_limit']);self.assertIsNone(l['attributes']['pets_allowed'])
        l['attributes']['pets_allowed']=False
        with self.assertRaises(ValidationError):validate_listing(l)

    def test_price_conflict_has_two_amounts(self):
        l=self.bycase['conflicting_price']
        self.assertIsNone(l['price']['amount'])
        self.assertEqual({e['value'] for e in l['evidence'] if e['field']=='price.amount'},{3200,3800})

    def test_mock_injection_remains_in_data(self):
        l=self.bycase['prompt_injection']
        self.assertIn('Ignore previous instructions',l['raw_description'])
        self.assertEqual(l['price']['amount'],3200)
        self.assertEqual(self.profile['hard_constraints']['max_price'],3500)

    def test_unverified_is_not_assumed_active(self):
        for l in self.listings:
            if l['last_verified_at'] is None:self.assertNotEqual(l['listing_status'],'active')
        self.assertEqual(expected_screen_bucket(self.bycase['never_verified'],self.profile),'needs_verification')

    def test_studio_and_study_not_extra_bedrooms(self):
        l=self.bycase['studio_zero_bedrooms']
        self.assertEqual((l['attributes']['property_type'],l['attributes']['unit_layout'],l['bedrooms']),('condo','studio',0))
        self.assertEqual(expected_screen_bucket(self.bycase['one_plus_study'],self.profile),'rejected')

    def test_input_contract_is_latest_uploaded_file(self):
        # Do not import workspace docs/contracts_v0.py: that file is still the old interface.
        check_type(contracts.RunContext,json.loads((ROOT/'output/fixtures/context.json').read_text()))
        self.assertIn('attributes',contracts.Listing.__annotations__)
        self.assertNotIn('scope',contracts.Price.__annotations__)

    def test_deterministic_generation_and_validation_on_another_seed(self):
        with tempfile.TemporaryDirectory() as tmp:
            a,b=Path(tmp)/'a',Path(tmp)/'b'
            generate(count=100,seed=91,out=a);generate(count=100,seed=91,out=b)
            for p in a.rglob('*.json*'):
                self.assertEqual(hashlib.sha256(p.read_bytes()).hexdigest(),hashlib.sha256((b/p.relative_to(a)).read_bytes()).hexdigest(),str(p.relative_to(a)))
            validate(a,write_report=False)
            old=json.loads((b/'listings.json').read_text())
            generate(count=100,seed=92,out=b)
            new=json.loads((b/'listings.json').read_text())
            self.assertNotEqual(old,new)
            self.assertEqual([r['listing_key'] for r in old],[r['listing_key'] for r in new])
            validate(b,write_report=False)

if __name__=='__main__':unittest.main()
