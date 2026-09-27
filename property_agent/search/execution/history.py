"""Task identity and valid page reuse within a single search; data is never shared across users."""
from copy import deepcopy
import hashlib
import json


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode()).hexdigest()[:24]


def query_fingerprint(plan, query):
    """Reconstructable cross-turn query identity, for B to internally write into AttemptSummary.query_fingerprints.

    The v0 historical summary does not carry the old SearchPlan, so the query and conditions needed to resume pagination are also stored here.
    Does not contain user identity, keys, or listings; a random query_id cannot be used to bypass the duplicate query check.
    """
    return 'search:v1:' + json.dumps(dict(
        profile_version=plan['profile_version'], intent=plan['intent'],
        required_filters=plan['required_filters'], source_mode=plan['source_mode'],
        query=query), ensure_ascii=False, sort_keys=True, separators=(',', ':'))


def task(kind, *, query_id=None, cursor=None, listing_key=None, requirement_id=None):
    data = dict(kind=kind, query_id=query_id, cursor=cursor, listing_key=listing_key,
                requirement_id=requirement_id)
    return dict(task_id=kind + ':' + fingerprint(data), **data)


class ExecutionHistory:
    def __init__(self):
        self._pages = {}

    def key(self, plan, query_id, cursor):
        query = next(q for q in plan['queries'] if q['query_id'] == query_id)
        return fingerprint([plan['source_mode'], plan['intent'], plan['required_filters'],
                            query['source'], query['text'].strip(), cursor])

    def get_page(self, plan, query_id, cursor):
        result = self._pages.get(self.key(plan, query_id, cursor))
        if result is None:
            return None
        result = deepcopy(result)
        result['data']['query_id'] = query_id
        return result

    def save_page(self, plan, query_id, cursor, result):
        # Failed and gap pages are not cached as successful responses for subsequent queries.
        if result['status'] == 'success':
            self._pages[self.key(plan, query_id, cursor)] = deepcopy(result)
