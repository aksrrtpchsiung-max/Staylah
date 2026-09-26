"""一次搜索中的任务身份与有效页面复用；不会跨用户共享数据。"""
from copy import deepcopy
import hashlib
import json


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode()).hexdigest()[:24]


def query_fingerprint(plan, query):
    """可还原的跨轮查询身份，供 B 内部写入 AttemptSummary.query_fingerprints。

    v0 的历史摘要不携带旧 SearchPlan，因此这里同时保存恢复续页所需的查询和条件。
    不包含用户身份、密钥或房源；不能用随机 query_id 绕过重复查询检查。
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
        # 失败与缺口页面不缓存成后续查询的成功响应。
        if result['status'] == 'success':
            self._pages[self.key(plan, query_id, cursor)] = deepcopy(result)
