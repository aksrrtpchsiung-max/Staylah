"""Bilingual place names from A must keep the same confirmed search area."""
import copy
from datetime import datetime, timedelta, timezone
import unittest

from property_agent.domain.requirements import location_entity, normalize_requirements
from property_agent.search.planning.query import prepare_request_query
from property_agent.search.planning.planner import _location_options
from test_all import request_1
from tests.support import build_ctx


class LocationAliasTests(unittest.IsolatedAsyncioTestCase):
    def test_two_names_of_the_same_known_area_are_recognized(self):
        for text, canonical in (
            ('淡滨尼（Tampines）', 'TAMPINES'),
            ('Clementi (金文泰)', 'CLEMENTI'),
            ('榜鹅 (Punggol)', 'PUNGGOL'),
        ):
            with self.subTest(text=text):
                entity = location_entity(text)
                self.assertEqual(entity['canonical_id'], canonical)
                self.assertEqual(entity['raw_text'], text)

    def test_conflicting_names_or_extra_conditions_are_not_collapsed(self):
        for text in ('淡滨尼（Clementi）', 'Tampines (near MRT)',
                     'Tampines / Clementi', 'Tampines (not Bedok)', 'Unknown (Tampines)'):
            with self.subTest(text=text):
                entity = location_entity(text)
                self.assertIsNone(entity['canonical_id'])
                self.assertEqual(entity['raw_text'], text)

    async def test_observed_a_target_reaches_english_search_without_changing_request(self):
        request = copy.deepcopy(request_1)
        area = next(item for item in request['derived_data_requirements']
                    if item['metric'] == 'residential_area')
        area['target'] = '淡滨尼（Tampines）'  # Actual failing A output from the live smoke.
        before = copy.deepcopy(request)
        ctx = build_ctx('bilingual-area', conversation_id=request['conversation_id'], source_mode='live')
        ctx['deadline_at'] = (datetime.now(timezone.utc) + timedelta(minutes=1)).isoformat()
        result = await prepare_request_query(request, ctx=ctx)
        self.assertEqual(result['status'], 'success', result['issues'])
        normalized = normalize_requirements(request)
        self.assertEqual(normalized['required_filters']['locations'], ['TAMPINES'])
        options = _location_options(normalized, result['data'], 'propertyguru')
        self.assertEqual(options['TAMPINES'][0]['text'], 'Tampines')
        self.assertEqual(request, before)
        self.assertEqual(normalized['listing_constraints'], before['listing_constraints'])
        self.assertEqual(normalized['derived_data_requirements'], before['derived_data_requirements'])


if __name__ == '__main__':
    unittest.main()
