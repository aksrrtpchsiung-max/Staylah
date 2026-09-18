"""Starter Kit 的 AWS LLM Gateway 配置及 LangGraph 可用的模型工厂。

本模块只构造模型依赖；不修改共享契约，不负责生成搜索计划。
运行本文件执行三组真实网关输入输出，不注入预设响应。
"""
from __future__ import annotations

import math
import os
from collections.abc import Generator, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit

import httpx
from dotenv import dotenv_values
from langchain_ollama import ChatOllama

DEFAULT_GATEWAY_URL = "https://api.softwaresystems.app"
DEFAULT_MODEL = "global.anthropic.claude-sonnet-4-5-20250929-v1:0"
DEFAULT_ENV_FILE = Path(__file__).resolve().with_name(".env")


@dataclass(frozen=True)
class SearchPlanSettings:
    """整次 search 的额度；默认和现有真实搜索示例一致。"""

    sources: tuple[str, ...] = ('propertyguru',)
    page_limit: int = 1
    candidate_limit: int = 2

    def __post_init__(self):
        if not self.sources or len(set(self.sources)) != len(self.sources) or any(
                not isinstance(s, str) or not s.strip() for s in self.sources):
            raise ValueError('sources 必须包含不重复的已注册来源名称')
        for name in ('page_limit', 'candidate_limit'):
            if type(getattr(self, name)) is not int or getattr(self, name) <= 0:
                raise ValueError(name + ' 必须是正整数')


def load_search_plan_settings(env_file=DEFAULT_ENV_FILE) -> SearchPlanSettings:
    values = dict(dotenv_values(env_file, interpolate=False)) if env_file else {}
    values.update(os.environ)
    try:
        return SearchPlanSettings(
            page_limit=int(values.get('SEARCH_PAGE_LIMIT') or '1'),
            candidate_limit=int(values.get('SEARCH_CANDIDATE_LIMIT') or '2'))
    except (ValueError, TypeError):
        raise ModelConfigurationError('SEARCH_PAGE_LIMIT 和 SEARCH_CANDIDATE_LIMIT 必须是正整数') from None


class ModelConfigurationError(ValueError):
    """本地模型配置缺失或不可用；错误信息不包含密钥。"""


@dataclass(frozen=True)
class _GatewayAuth(httpx.Auth):
    """仅在发送请求时加入网关鉴权，模型表示和追踪信息不含密钥。"""

    _api_key: str = field(repr=False)

    def auth_flow(self, request: httpx.Request) -> Generator[httpx.Request, httpx.Response, None]:
        # Ollama SDK 可能从 OLLAMA_API_KEY 注入 Authorization；此网关只使用 X-API-Key。
        request.headers.pop("Authorization", None)
        request.headers["X-API-Key"] = self._api_key
        yield request


@dataclass(frozen=True)
class ModelSettings:
    api_key: str = field(repr=False)
    gateway_url: str = DEFAULT_GATEWAY_URL
    model: str = DEFAULT_MODEL
    timeout_seconds: float = 60.0
    temperature: float = 0.0
    max_tokens: int = 2000

    def __post_init__(self) -> None:
        if not self.api_key.strip():
            raise ModelConfigurationError(
                "缺少 LLM_GATEWAY_API_KEY，请在项目 .env 中填写主办方提供的密钥。"
            )
        if not self.model.strip():
            raise ModelConfigurationError("LLM_MODEL 不能为空。")
        try:
            url = urlsplit(self.gateway_url)
            valid_url = (
                url.scheme in {"http", "https"}
                and bool(url.hostname)
                and not url.username
                and not url.password
                and not url.query
                and not url.fragment
            )
            _ = url.port
        except ValueError:
            valid_url = False
        if not valid_url:
            raise ModelConfigurationError("LLM_GATEWAY_URL 必须是有效的 HTTP(S) 网关地址。")
        if not math.isfinite(self.timeout_seconds) or self.timeout_seconds <= 0:
            raise ModelConfigurationError("LLM_TIMEOUT_SECONDS 必须是有限正数。")
        if not math.isfinite(self.temperature) or not 0 <= self.temperature <= 1:
            raise ModelConfigurationError("LLM_TEMPERATURE 必须在 0 到 1 之间。")
        if type(self.max_tokens) is not int or self.max_tokens <= 0:
            raise ModelConfigurationError("LLM_MAX_TOKENS 必须是正整数。")


def load_model_settings(
    env_file: str | Path | None = DEFAULT_ENV_FILE,
    *,
    environ: Mapping[str, str] | None = None,
) -> ModelSettings:
    """读取项目 .env；进程环境变量优先，不改变 os.environ。

    离线测试可传入 env_file=None 和独立 environ，完全隔离真实配置。
    """
    values = dict(dotenv_values(env_file, interpolate=False)) if env_file else {}
    values.update(os.environ if environ is None else environ)

    def value(name: str, default: str = "") -> str:
        return (values.get(name) or "").strip() if name in values else default

    def number(name: str, default: str, parse):
        try:
            return parse(value(name, default))
        except (ValueError, TypeError):
            raise ModelConfigurationError(f"{name} 的数值格式不正确。") from None

    return ModelSettings(
        api_key=value("LLM_GATEWAY_API_KEY"),
        gateway_url=value("LLM_GATEWAY_URL", DEFAULT_GATEWAY_URL).rstrip("/"),
        model=value("LLM_MODEL", DEFAULT_MODEL),
        timeout_seconds=number("LLM_TIMEOUT_SECONDS", "60", float),
        temperature=number("LLM_TEMPERATURE", "0", float),
        max_tokens=number("LLM_MAX_TOKENS", "2000", int),
    )


def create_chat_model(
    settings: ModelSettings | None = None,
    *,
    transport: httpx.BaseTransport | None = None,
    async_transport: httpx.AsyncBaseTransport | None = None,
) -> ChatOllama:
    """构造支持 invoke/ainvoke 的模型，由服务构造时注入 LangGraph 节点。

    构造时不请求网关；transport 参数用于本地模拟 HTTP 响应。
    不自动重试，避免鉴权失败反复请求；业务层后续按 ctx.deadline_at 控制总时限。
    公共网关的原生工具调用未验证，本轮只接入消息请求与回复。
    """
    settings = settings if settings is not None else load_model_settings()
    return ChatOllama(
        model=settings.model,
        base_url=settings.gateway_url,
        temperature=settings.temperature,
        num_predict=settings.max_tokens,
        validate_model_on_init=False,
        client_kwargs={
            "auth": _GatewayAuth(settings.api_key),
            "timeout": settings.timeout_seconds,
            "follow_redirects": False,
        },
        sync_client_kwargs={"transport": transport} if transport is not None else {},
        async_client_kwargs=(
            {"transport": async_transport} if async_transport is not None else {}
        ),
    )


if __name__ == '__main__':
    import argparse
    import asyncio
    import json
    from dataclasses import replace
    from langchain_core.messages import HumanMessage
    from langgraph.graph import END, START, MessagesState, StateGraph
    from ollama import ResponseError

    parser=argparse.ArgumentParser(description='真实模型网关输入输出检查；读取本地 .env')
    parser.add_argument('--live', action='store_true', help='兼容旧命令；现在始终使用真实模型')
    args=parser.parse_args()

    async def main():
        settings=replace(load_model_settings(), max_tokens=256, temperature=0)
        model=create_chat_model(settings)
        async def chat(state):
            return {'messages':[await model.ainvoke(state['messages'], stream=False)]}
        builder=StateGraph(MessagesState)
        builder.add_node('model', chat)
        builder.add_edge(START, 'model')
        builder.add_edge('model', END)
        graph=builder.compile()
        requests=[
            '请复述搜索需求：Tampines 整套出租，月租不超过 SGD 4000，至少两个卧室。不要添加房源事实。',
            '请复述搜索需求：Clementi 整套出租，月租不超过 SGD 4500，至少两个卧室。不要添加房源事实。',
            '请复述搜索需求：Punggol 整套出租，月租不超过 SGD 4000，至少两个卧室。不要添加房源事实。',
        ]
        passed=0
        for prompt in requests:
            try:
                state=await asyncio.wait_for(graph.ainvoke({'messages':[HumanMessage(content=prompt)]}), settings.timeout_seconds)
                answer=state['messages'][-1].content
                if not answer:
                    raise ValueError('网关返回空回复')
                passed+=1
                print(json.dumps(dict(input=prompt, actual_output=answer, model=settings.model),ensure_ascii=False),flush=True)
            except ResponseError as exc:
                print(json.dumps(dict(input=prompt,error='网关请求失败',http_status=exc.status_code),ensure_ascii=False),flush=True)
            except (TimeoutError, httpx.TimeoutException):
                print(json.dumps(dict(input=prompt,error='真实模型调用超时'),ensure_ascii=False),flush=True)
            except (ConnectionError, httpx.TransportError):
                print(json.dumps(dict(input=prompt,error='无法连接真实模型网关'),ensure_ascii=False),flush=True)
        print(f'真实模型调用通过 {passed}/3')
        return 0 if passed==3 else 1
    try:
        raise SystemExit(asyncio.run(main()))
    except ModelConfigurationError as exc:
        parser.exit(2,f'配置未完成：{exc}\n')
