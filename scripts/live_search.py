"""Four real search use cases; running this file directly will call them in order and print the return values."""

import asyncio
from datetime import datetime, timedelta, timezone
import json
from uuid import uuid4

from property_agent.search.api import search as api_search
from property_agent.contracts import SearchPlan


# Complete SearchPlan example: fields match property_agent/contracts.py and can be copied and modified directly.
# page_limit is the page count limit for the entire search, and candidate_limit is the candidate limit for the entire call.
search_plan_1: SearchPlan = {
    "plan_id": "plan-tampines-whole",
    "profile_version": 1,
    "attempt_id": "attempt-tampines-whole",
    "intent": "rent",
    "required_filters": {
        "currency": "SGD",
        "max_price": 4000,
        "price_period": "month",
        "rental_scope": "whole_unit",
        "locations": ["TAMPINES"],
        "min_bedrooms": 2,
    },
    "queries": [
        {"query_id": "q-tampines", "source": "propertyguru", "text": "Tampines", "cursor": None},
    ],
    "page_limit": 4,
    "candidate_limit": 12,
    "source_mode": "live",
    "reason": "Find a whole-unit rental in Tampines, monthly rent no more than SGD 4000, at least two bedrooms.",
}

search_plan_2: SearchPlan = {
    "plan_id": "plan-clementi-room",
    "profile_version": 1,
    "attempt_id": "attempt-clementi-room",
    "intent": "rent",
    "required_filters": {
        "currency": "SGD",
        "max_price": 1500,
        "price_period": "month",
        "rental_scope": "room",
        "locations": ["CLEMENTI"],
        "min_bedrooms": None,
    },
    "queries": [
        {"query_id": "q-clementi", "source": "propertyguru", "text": "Clementi", "cursor": None},
    ],
    "page_limit": 4,
    "candidate_limit": 12,
    "source_mode": "live",
    "reason": "Find a room rental in Clementi, monthly rent no more than SGD 1500, with no restriction on the total number of bedrooms in the whole unit.",
}

search_plan_3: SearchPlan = {
    "plan_id": "plan-punggol-whole",
    "profile_version": 1,
    "attempt_id": "attempt-punggol-whole",
    "intent": "rent",
    "required_filters": {
        "currency": "SGD",
        "max_price": 4500,
        "price_period": "month",
        "rental_scope": "whole_unit",
        "locations": ["PUNGGOL"],
        "min_bedrooms": 3,
    },
    "queries": [
        {"query_id": "q-punggol", "source": "propertyguru", "text": "Punggol", "cursor": None},
    ],
    "page_limit": 4,
    "candidate_limit": 12,
    "source_mode": "live",
    "reason": "Find a family-friendly whole-unit rental in Punggol, monthly rent no more than SGD 4500, at least three bedrooms.",
}

search_plan_4: SearchPlan = {
    "plan_id": "plan-bishan-buy",
    "profile_version": 1,
    "attempt_id": "attempt-bishan-buy",
    "intent": "buy",
    "required_filters": {
        "currency": "SGD",
        "max_price": 1200000,
        "price_period": "total",
        "rental_scope": None,
        "locations": ["BISHAN"],
        "min_bedrooms": 2,
    },
    "queries": [
        {"query_id": "q-bishan", "source": "propertyguru", "text": "Bishan", "cursor": None},
    ],
    "page_limit": 4,
    "candidate_limit": 12,
    "source_mode": "live",
    "reason": "Find properties for sale in Bishan, total price no more than SGD 1200000, at least two bedrooms; rental scope does not apply.",
}


def search(plan):
    # api.search is an asynchronous function and the contract requires ctx; here it is only filled in automatically, and main only needs to pass the plan.
    identity = uuid4().hex
    ctx = {
        "user_id": "test-search",
        "run_id": identity,
        "conversation_id": identity,
        "attempt_id": plan["attempt_id"],
        "trace_id": identity,
        "call_id": identity,
        "deadline_at": (datetime.now(timezone.utc) + timedelta(minutes=5)).isoformat(),
        "source_mode": "live",
    }
    print(f"\n{plan['reason']}", flush=True)
    result = asyncio.run(api_search(plan, ctx=ctx))
    print(json.dumps(result, ensure_ascii=False, indent=2), flush=True)
    return result


if __name__ == "__main__":
    search(search_plan_1)
    #search(search_plan_2)
    #search(search_plan_3)
    #search(search_plan_4)
