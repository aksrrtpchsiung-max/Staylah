"""四个真实搜索用例，直接运行本文件即可依次调用并打印返回值。"""

import asyncio
from datetime import datetime, timedelta, timezone
import json
from uuid import uuid4

from api import search as api_search
from contracts_v0 import SearchPlan


# 完整的 SearchPlan 示例：字段与 contracts_v0.py 一致，可直接复制修改。
# page_limit 是整个 search 的页数上限，candidate_limit 是整个调用的候选上限。
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
    "reason": "在 Tampines 找整套出租，月租不超过 SGD 4000，至少两个卧室。",
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
    "reason": "在 Clementi 找单间出租，月租不超过 SGD 1500，不限制整套房屋的卧室总数。",
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
    "reason": "在 Punggol 找适合家庭的整套出租，月租不超过 SGD 4500，至少三个卧室。",
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
    "reason": "在 Bishan 找出售房源，总价不超过 SGD 1200000，至少两个卧室；租赁范围不适用。",
}


def search(plan):
    # api.search 是异步函数且契约要求 ctx；这里只自动补齐，main 只需传计划。
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
