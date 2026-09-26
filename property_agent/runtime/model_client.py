"""搜索执行参数、DeepSeek 配置及 B 模型工厂。

本模块读取配置并构造模型依赖；不修改共享契约，不负责生成搜索计划。
运行本文件执行三组真实 DeepSeek 输入输出，不注入预设响应。
"""
from __future__ import annotations

from property_agent.runtime.paths import PROJECT_ROOT

import json
import math
import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit

import httpx
from dotenv import dotenv_values
from langchain_core.messages import AIMessage

from property_agent.runtime.settings import load_runtime_settings

DEFAULT_DEEPSEEK_URL = "https://api.deepseek.com"
DEFAULT_MODEL = "deepseek-v4-flash"
DEFAULT_ENV_FILE = PROJECT_ROOT / ".env"


@dataclass(frozen=True)
class SearchPlanSettings:
    """整次 search 的额度，不是每个地区各自的额度。"""

    sources: tuple[str, ...] = ('propertyguru',)
    page_limit: int = 4
    candidate_limit: int = 12

    def __post_init__(self):
        if not self.sources or len(set(self.sources)) != len(self.sources) or any(
                not isinstance(s, str) or not s.strip() for s in self.sources):
            raise ValueError('sources 必须包含不重复的已注册来源名称')
        for name in ('page_limit', 'candidate_limit'):
            if type(getattr(self, name)) is not int or getattr(self, name) <= 0:
                raise ValueError(name + ' 必须是正整数')


def load_search_plan_settings(env_file=DEFAULT_ENV_FILE) -> SearchPlanSettings:
    search = load_runtime_settings(env_file=env_file).search
    values = dict(dotenv_values(env_file, interpolate=False)) if env_file else {}
    values.update(os.environ)
    try:
        return SearchPlanSettings(
            page_limit=int(values.get('SEARCH_PAGE_LIMIT') or search.page_limit),
            candidate_limit=int(values.get('SEARCH_CANDIDATE_LIMIT') or search.candidate_limit))
    except (ValueError, TypeError):
        raise ModelConfigurationError('SEARCH_PAGE_LIMIT 和 SEARCH_CANDIDATE_LIMIT 必须是正整数') from None


class ModelConfigurationError(ValueError):
    """本地模型配置缺失或不可用；错误信息不包含密钥。"""


@dataclass(frozen=True)
class SearchExecutionSettings:
    """B 内部执行参数，不增加共享 SearchPlan/RunContext 字段。"""

    page_result_limit: int = 6
    provider_timeout_seconds: float = 30.0
    max_retries: int = 1
    planner_timeout_seconds: float = 20.0
    supervisor_timeout_seconds: float = 8.0
    supervisor_max_calls: int = 3
    finalize_reserve_seconds: float = 10.0

    def __post_init__(self):
        for name in ('page_result_limit', 'supervisor_max_calls'):
            if type(getattr(self, name)) is not int or getattr(self, name) <= 0:
                raise ValueError(name + ' 必须是正整数')
        if type(self.max_retries) is not int or not 0 <= self.max_retries <= 3:
            raise ValueError('max_retries 必须是 0–3')
        for name in ('provider_timeout_seconds', 'planner_timeout_seconds',
                     'supervisor_timeout_seconds', 'finalize_reserve_seconds'):
            value = getattr(self, name)
            if not math.isfinite(value) or value <= 0:
                raise ValueError(name + ' 必须是有限正数')
        if self.supervisor_timeout_seconds > 60:
            raise ValueError('supervisor_timeout_seconds 不能超过 60 秒')


def load_search_execution_settings(env_file=DEFAULT_ENV_FILE) -> SearchExecutionSettings:
    values = dict(dotenv_values(env_file, interpolate=False)) if env_file else {}
    values.update(os.environ)
    defaults = load_runtime_settings(env_file=env_file).search
    fields = {
        'page_result_limit': ('SEARCH_PAGE_RESULT_LIMIT', int),
        'provider_timeout_seconds': ('SEARCH_PROVIDER_TIMEOUT_SECONDS', float),
        'max_retries': ('SEARCH_MAX_RETRIES', int),
        'planner_timeout_seconds': ('SEARCH_PLANNER_TIMEOUT_SECONDS', float),
        'supervisor_timeout_seconds': ('SEARCH_SUPERVISOR_TIMEOUT_SECONDS', float),
        'supervisor_max_calls': ('SEARCH_SUPERVISOR_MAX_CALLS', int),
        'finalize_reserve_seconds': ('SEARCH_FINALIZE_RESERVE_SECONDS', float),
    }
    parsed = {}
    for name, (env_name, parse) in fields.items():
        try:
            parsed[name] = parse(values.get(env_name) or getattr(defaults, name))
        except (ValueError, TypeError):
            raise ModelConfigurationError(env_name + ' 的数值格式不正确') from None
    try:
        return SearchExecutionSettings(**parsed)
    except ValueError as exc:
        raise ModelConfigurationError('搜索执行配置无效：' + str(exc)) from None


@dataclass(frozen=True)
class ModelSettings:
    api_key: str = field(repr=False)
    base_url: str = DEFAULT_DEEPSEEK_URL
    model: str = DEFAULT_MODEL
    timeout_seconds: float = 45.0
    temperature: float = 0.0
    max_tokens: int = 2600

    def __post_init__(self) -> None:
        if not self.api_key.strip():
            raise ModelConfigurationError(
                "缺少 DEEPSEEK_API_KEY，请在项目 .env 中填写 DeepSeek 密钥。"
            )
        if not self.model.strip():
            raise ModelConfigurationError("DEEPSEEK_MODEL 不能为空。")
        try:
            url = urlsplit(self.base_url)
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
            raise ModelConfigurationError("DEEPSEEK_API_BASE 必须是有效的 HTTP(S) 地址。")
        if not math.isfinite(self.timeout_seconds) or self.timeout_seconds <= 0:
            raise ModelConfigurationError("DEEPSEEK_TIMEOUT_SECONDS 必须是有限正数。")
        if not math.isfinite(self.temperature) or not 0 <= self.temperature <= 1:
            raise ModelConfigurationError("DEEPSEEK_TEMPERATURE 必须在 0 到 1 之间。")
        if type(self.max_tokens) is not int or self.max_tokens <= 0:
            raise ModelConfigurationError("DEEPSEEK_MAX_TOKENS 必须是正整数。")


def load_model_settings(
    env_file: str | Path | None = DEFAULT_ENV_FILE,
    *,
    environ: Mapping[str, str] | None = None,
) -> ModelSettings:
    """读取项目 .env；进程环境变量优先，不改变 os.environ。

    离线测试可传入 env_file=None 和独立 environ，完全隔离真实配置。
    """
    deepseek = load_runtime_settings(env_file=env_file, environ=environ).deepseek
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
        api_key=value(deepseek.api_key_env) or deepseek.api_key(values),
        base_url=value("DEEPSEEK_API_BASE", deepseek.base_url).rstrip("/"),
        model=value("DEEPSEEK_MODEL", deepseek.model),
        timeout_seconds=number("DEEPSEEK_TIMEOUT_SECONDS", str(deepseek.timeout_seconds), float),
        temperature=number("DEEPSEEK_TEMPERATURE", "0", float),
        max_tokens=number("DEEPSEEK_MAX_TOKENS", str(deepseek.max_tokens), int),
    )


class DeepSeekChatError(RuntimeError):
    """DeepSeek 请求或响应错误；只保留安全的 HTTP 状态信息。"""

    def __init__(self, message: str, *, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


class DeepSeekChatClient:
    """B/C 共用的 DeepSeek HTTP 客户端。"""

    def __init__(
        self,
        settings: ModelSettings,
        *,
        async_transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._settings = settings
        self._transport = async_transport
        url = settings.base_url.rstrip("/")
        if url.endswith("/chat/completions"):
            self._endpoint = url
        elif url.endswith("/v1"):
            self._endpoint = f"{url}/chat/completions"
        else:
            self._endpoint = f"{url}/chat/completions"

    @staticmethod
    def _message_payload(message: object) -> dict[str, str]:
        if isinstance(message, dict):
            role = message.get("role")
            content = message.get("content")
        else:
            role = getattr(message, "type", None) or getattr(message, "role", None)
            content = getattr(message, "content", None)
        role = {"human": "user", "ai": "assistant"}.get(role, role)
        if role not in {"system", "user", "assistant"} or not isinstance(content, str):
            raise DeepSeekChatError("DeepSeek message must contain a supported role and string content")
        return {"role": role, "content": content}

    async def complete(
        self,
        messages: list[object],
        *,
        max_tokens: int | None = None,
        temperature: float | None = None,
        stop: object = None,
        response_format: dict[str, str] | None = None,
    ) -> tuple[str, dict[str, object]]:
        payload: dict[str, object] = {
            "model": self._settings.model,
            "messages": [self._message_payload(message) for message in messages],
            "temperature": self._settings.temperature if temperature is None else temperature,
            "max_tokens": self._settings.max_tokens if max_tokens is None else max_tokens,
            "stream": False,
        }
        if stop:
            payload["stop"] = stop
        if response_format is not None:
            payload["response_format"] = response_format
        try:
            async with httpx.AsyncClient(
                timeout=self._settings.timeout_seconds,
                follow_redirects=False,
                transport=self._transport,
            ) as client:
                response = await client.post(
                    self._endpoint,
                    headers={
                        "Authorization": f"Bearer {self._settings.api_key}",
                        "Content-Type": "application/json",
                    },
                    json=payload,
                )
        except httpx.HTTPError as exc:
            raise DeepSeekChatError("DeepSeek request failed") from exc
        if response.status_code >= 400:
            raise DeepSeekChatError(
                f"DeepSeek returned HTTP {response.status_code}",
                status_code=response.status_code,
            )
        if len(response.content) > 2_000_000:
            raise DeepSeekChatError("DeepSeek response is too large")
        try:
            data = response.json()
            choice = data["choices"][0]
            content = choice["message"]["content"]
            if not isinstance(content, str) or not content.strip():
                finish_reason = choice.get("finish_reason")
                safe_reason = finish_reason if isinstance(finish_reason, str) else "unknown"
                raise DeepSeekChatError(
                    f"DeepSeek returned empty message content (finish_reason={safe_reason})"
                )
        except DeepSeekChatError:
            raise
        except (ValueError, KeyError, IndexError, TypeError) as exc:
            raise DeepSeekChatError("DeepSeek returned an invalid chat-completion response") from exc
        return (
            content,
            {
                "model": data.get("model", self._settings.model),
                "request_id": data.get("id"),
            },
        )


class DeepSeekChatModel:
    """把共享 DeepSeek 客户端适配成 B 现有的 ``ainvoke`` 接口。"""

    def __init__(self, client: DeepSeekChatClient) -> None:
        self._client = client

    async def ainvoke(self, messages: list[object], **kwargs: object) -> AIMessage:
        if kwargs.get("stream") not in {None, False}:
            raise DeepSeekChatError("Streaming is not supported by the DeepSeek B adapter")
        content, metadata = await self._client.complete(
            messages,
            stop=kwargs.get("stop"),
        )
        return AIMessage(content=content, response_metadata=metadata)


def create_deepseek_client(
    settings: ModelSettings | None = None,
    *,
    async_transport: httpx.AsyncBaseTransport | None = None,
) -> DeepSeekChatClient:
    """构造供 B/C 共用的 DeepSeek HTTP 客户端。"""

    settings = settings if settings is not None else load_model_settings()
    return DeepSeekChatClient(settings, async_transport=async_transport)


def create_chat_model(
    settings: ModelSettings | None = None,
    *,
    transport: httpx.BaseTransport | None = None,
    async_transport: httpx.AsyncBaseTransport | None = None,
) -> DeepSeekChatModel:
    """构造 B 使用的 DeepSeek ``ainvoke`` 模型。

    构造时不请求外部服务；业务层继续按 ``ctx.deadline_at`` 控制总时限。
    ``transport`` 仅为旧调用签名保留；B 当前只使用异步请求。
    """
    del transport
    return DeepSeekChatModel(
        create_deepseek_client(settings, async_transport=async_transport)
    )


if __name__ == '__main__':
    import argparse
    import asyncio
    import json
    from dataclasses import replace
    from langchain_core.messages import HumanMessage
    from langgraph.graph import END, START, MessagesState, StateGraph

    parser=argparse.ArgumentParser(description='真实 DeepSeek 输入输出检查；读取本地 .env')
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
                    raise ValueError('DeepSeek 返回空回复')
                passed+=1
                print(json.dumps(dict(input=prompt, actual_output=answer, model=settings.model),ensure_ascii=False),flush=True)
            except DeepSeekChatError as exc:
                print(json.dumps(dict(input=prompt,error='DeepSeek 请求失败',http_status=exc.status_code),ensure_ascii=False),flush=True)
            except (TimeoutError, httpx.TimeoutException):
                print(json.dumps(dict(input=prompt,error='真实模型调用超时'),ensure_ascii=False),flush=True)
            except (ConnectionError, httpx.TransportError):
                print(json.dumps(dict(input=prompt,error='无法连接 DeepSeek'),ensure_ascii=False),flush=True)
        print(f'真实模型调用通过 {passed}/3')
        return 0 if passed==3 else 1
    try:
        raise SystemExit(asyncio.run(main()))
    except ModelConfigurationError as exc:
        parser.exit(2,f'配置未完成：{exc}\n')
