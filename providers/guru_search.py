"""通过 OpenCLI 调用仓库内的 guru_search 适配器（JSON page/structured 模式）。"""
import asyncio
import json
from pathlib import Path
import re
import shutil
import sys
from collections.abc import Sequence

# 支持编辑器直接运行本文件中的真实调用检查；包导入时不修改搜索路径。
if __name__ == "__main__" and __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from contracts_v0 import ContractViolation, HardConstraints, Listing, RunContext, SearchQuery
from execution.budget import remaining_seconds
from execution.tasks import ListingDetail, ListingPage
from part1.validation import timestamp, validate_listing, validate_type
from providers.base import ProviderError, issue


class GuruSearchProvider:
    source = "propertyguru"
    source_mode = "live"

    def __init__(self, command: Sequence[str] | None = None, *, timeout_seconds: float = 30):
        if command is None:
            executable = shutil.which('opencli')
            user_install = Path.home() / '.npm-global/bin/opencli'
            command = (executable or (str(user_install) if user_install.is_file() else 'opencli'),)
        if not command or isinstance(command, str) or timeout_seconds <= 0:
            raise ValueError("command 必须是参数数组，timeout_seconds 必须大于零")
        self.command = tuple(command)
        self.timeout_seconds = timeout_seconds

    async def _call(self, args: list[str], ctx: RunContext):
        timeout = min(self.timeout_seconds, remaining_seconds(ctx))
        try:
            process = await asyncio.create_subprocess_exec(
                *self.command, "propertyguru", *args, "-f", "json",
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        except OSError as exc:
            raise ProviderError(issue("SOURCE_UNAVAILABLE", "无法启动 OpenCLI；请安装并注册 guru_search 适配器")) from exc
        try:
            stdout, stderr = await asyncio.wait_for(process.communicate(), timeout)
        except (TimeoutError, asyncio.CancelledError) as exc:
            if process.returncode is None:
                try:
                    process.kill()
                except ProcessLookupError:
                    pass
            await process.communicate()
            if isinstance(exc, asyncio.CancelledError):
                raise
            raise ProviderError(issue("TIMEOUT", "PropertyGuru 调用超时", retryable=True)) from exc
        if process.returncode:
            # 兼容 OpenCLI 文本/JSON 错误；不把整段浏览器日志作为公开输出。
            message = (stderr + stdout).decode("utf-8", errors="replace").upper()
            codes = (("AUTH_REQUIRED", ("AUTH_REQUIRED", "LOGIN_WALL", "CAPTCHA")),
                     ("RATE_LIMITED", ("RATE_LIMIT", "429")),
                     ("TIMEOUT", ("TIMEOUT", "TIMED OUT")),
                     ("TEMPORARY_UNAVAILABLE", ("SESSION_BUSY", "TEMPORARY_UNAVAILABLE")),
                     ("PARSE_ERROR", ("PARSE_ERROR", "COULD NOT FIND LISTING", "PAGE DID NOT LOAD")),
                     ("INVALID_INPUT", ("CODE: ARGUMENT", '"CODE": "ARGUMENT"', "INVALID ARGUMENT")),
                     ("SOURCE_UNAVAILABLE", ("EXTENSION NOT CONNECTED", "BROWSER BRIDGE", "EXTENSION_NOT_CONNECTED")))
            code = next((code for code, words in codes if any(word in message for word in words)),
                        "SOURCE_UNAVAILABLE")
            description = ('OpenCLI Browser Bridge 未连接，请在 Chrome 安装或启用扩展'
                           if 'EXTENSION' in message and ('NOT CONNECTED' in message or 'CONNECT' in message)
                           else f"PropertyGuru 命令失败：{code}")
            if code == "PARSE_ERROR":
                # 只映射适配器中已知的错误文本，保留原因而不泄露整段浏览器日志。
                parse_reasons = (
                    ("NO __NEXT_DATA__", "PropertyGuru 搜索页未提供 __NEXT_DATA__，无法读取页面数据"),
                    ("COULD NOT FIND LISTING DATA ON PAGE", "PropertyGuru 页面数据缺少 listingsData 房源字段"),
                    ("EMPTY LISTING PAYLOAD WITHOUT AN EXPLICIT NO-RESULTS STATE",
                     "PropertyGuru 未解析到房源，页面也未明确表示无匹配，不能当作空结果"),
                    ("PAGE CHANGED; CONTINUATION OFFSET IS NO LONGER VALID",
                     "PropertyGuru 页面内容已变化，续页游标的页内偏移失效"),
                    ("DETAIL PAGE DOES NOT IDENTIFY A PROPERTYGURU LISTING",
                     "PropertyGuru 详情页未提供有效的房源链接和 ID"),
                    ("DETAIL REDIRECTED TO A DIFFERENT LISTING",
                     "PropertyGuru 详情页跳转到了另一套房源"),
                    ("PAGE DID NOT LOAD", "PropertyGuru 页面未提供预期数据，无法解析"),
                )
                description = next((reason for marker, reason in parse_reasons if marker in message), description)
            elif code == "TEMPORARY_UNAVAILABLE":
                if "SEARCH PAGE DATA NOT READY" in message:
                    description = "PropertyGuru 搜索页在等待时限内未提供房源数据，可在剩余额度内重试"
                elif "DETAIL PAGE DATA NOT READY" in message:
                    description = "PropertyGuru 详情页在等待时限内未提供房源数据，可在剩余额度内重试"
            raise ProviderError(issue(code, description,
                                      retryable=code in {"TIMEOUT", "RATE_LIMITED", "SOURCE_UNAVAILABLE", "TEMPORARY_UNAVAILABLE"}))
        try:
            payload = json.loads(stdout)
        except (ValueError, UnicodeError) as exc:
            raise ProviderError(issue("PARSE_ERROR", "OpenCLI 未返回有效 JSON")) from exc
        # OpenCLI 的表格命令以一行数组封装结构化输出。
        if isinstance(payload, list) and len(payload) == 1 and isinstance(payload[0], dict):
            return payload[0]
        if isinstance(payload, dict):
            return payload
        raise ProviderError(issue("PARSE_ERROR", "需要新版 guru_search 的结构化输出"))

    async def search_page(self, query: SearchQuery, *, intent: str,
                          filters: HardConstraints, limit: int,
                          ctx: RunContext) -> ListingPage:
        page, offset = 1, 0
        if query["cursor"] is not None:
            match = re.fullmatch(r"pg:v1:([1-9][0-9]*):([0-9]+)", query["cursor"])
            if not match:
                raise ProviderError(issue("INVALID_INPUT", "无效的 PropertyGuru 分页游标", field_path="query.cursor"))
            page, offset = map(int, match.groups())
            if max(page, offset) > 2**53 - 1:
                raise ProviderError(issue("INVALID_INPUT", "分页游标超出来源支持范围", field_path="query.cursor"))
        args = ["search", query["text"], "--listing", "sale" if intent == "buy" else "rent",
                "--page", str(page), "--offset", str(offset), "--limit", str(limit),
                "--output-mode", "page"]
        applied = ["transaction_type"]
        unsupported = ["price.currency"]
        if filters["price_period"] is not None:
            unsupported.append("price.period")
        expected_period = "total" if intent == "buy" else "month"
        if filters["max_price"] is not None:
            if (filters["currency"] == "SGD" and filters["price_period"] == expected_period
                    and filters["max_price"] > 0):
                args += ["--max", str(filters["max_price"])]
                applied.append("price.amount")
            else:
                unsupported.append("price.amount")
        for key, field in (("min_bedrooms", "bedrooms"), ("rental_scope", "attributes.listing_scope")):
            if filters[key] is not None:
                unsupported.append(field)
        if filters["locations"]:
            unsupported.append("location_id")  # 自由文本检索不等于规范地点 ID 筛选。
        payload = await self._call(args, ctx)
        try:
            if set(payload) != {"items", "next_cursor", "pagination_known", "truncated"}:
                raise ValueError("搜索输出字段不符")
            if type(payload["items"]) is not list:
                raise ValueError("items 不是数组")
            items, problems = [], []
            for raw in payload["items"]:
                try:
                    validate_listing(raw)
                    if raw["source"] != self.source or raw["source_mode"] != self.source_mode:
                        raise ValueError("房源来源与 Provider 不一致")
                    items.append(raw)
                except (ContractViolation, ValueError) as exc:
                    problems.append(issue("INVALID_OUTPUT", f"已跳过无法校验的房源：{exc}"))
            result = dict(query_id=query["query_id"], items=items, next_cursor=payload["next_cursor"],
                          pagination_known=payload["pagination_known"], truncated=payload["truncated"],
                          applied_filters=applied, unsupported_filters=unsupported, issues=problems)
            validate_type(ListingPage, result)
            if result["next_cursor"] is not None and not re.fullmatch(r"pg:v1:[1-9][0-9]*:[0-9]+", result["next_cursor"]):
                raise ValueError("返回了无效分页游标")
            if len(items) > limit:
                raise ValueError("来源未遵守候选额度")
            if not result["pagination_known"]:
                problems.append(issue("RETRIEVAL_DEGRADED", "页面未提供可靠的分页终点；不能认定搜索已完成"))
            if payload["items"] and not items:
                raise ValueError("全部房源均未通过契约校验")
            return result
        except (ContractViolation, ValueError, TypeError, KeyError) as exc:
            raise ProviderError(issue("PARSE_ERROR", f"搜索结果无法解析：{exc}")) from exc

    async def read_detail(self, listing: Listing, *, ctx: RunContext) -> ListingDetail:
        # 搜索已给出真实详情链接时直接使用，避免再次依赖无 slug 的数字 ID 跳转。
        url = listing['source_url'] or ''
        identifier = (url if url.startswith('https://www.propertyguru.com.sg/listing/')
                      else listing['source_listing_id'])
        if not identifier:
            raise ProviderError(issue("INVALID_INPUT", "房源缺少来源 ID 和链接"))
        payload = await self._call(["detail", identifier, "--output-mode", "structured"], ctx)
        try:
            validate_type(ListingDetail, payload)
            timestamp(payload["fetched_at"], "detail.fetched_at")
        except ContractViolation as exc:
            raise ProviderError(issue("PARSE_ERROR", f"详情不符合内部接口：{exc}")) from exc
        return payload


if __name__ == '__main__':
    from datetime import datetime, timedelta, timezone
    from uuid import uuid4

    async def main():
        provider=GuruSearchProvider()
        passed=0
        for area in ('Tampines', 'Clementi', 'Punggol'):
            identity=str(uuid4())
            ctx=dict(user_id='live-check', run_id=identity, conversation_id=identity, attempt_id=identity,
                trace_id=identity, call_id=identity, source_mode='live',
                deadline_at=(datetime.now(timezone.utc)+timedelta(minutes=2)).isoformat())
            query=dict(query_id='q-'+area.lower(), source='propertyguru', text=area, cursor=None)
            filters=dict(currency='SGD', max_price=4500, price_period='month', rental_scope='whole_unit',
                         locations=[area.upper()], min_bedrooms=2)
            try:
                output=await provider.search_page(query, intent='rent', filters=filters, limit=1, ctx=ctx)
                details=[await provider.read_detail(item, ctx=ctx) for item in output['items']]
                passed+=bool(output['items'] and details)
                print(json.dumps(dict(input=dict(query=query, filters=filters, ctx=ctx), output=output, details=details), ensure_ascii=False), flush=True)
            except ProviderError as exc:
                print(json.dumps(dict(input=dict(query=query, filters=filters, ctx=ctx), error=exc.issue), ensure_ascii=False), flush=True)
        print(f'真实 guru_search 调用通过 {passed}/3')
        return 0 if passed==3 else 1

    raise SystemExit(asyncio.run(main()))
