"""上游运行控制层可实现的持久化接口。

这些 Protocol 不改变冻结的公共函数契约。onboarding / 搜索团队可以按同样形状
读写会话消息、档案和 run，不必依赖追问内部状态。
"""
from __future__ import annotations

from typing import Protocol

from property_agent.contracts import ChatMessage, RunContext, ConversationProfile
from property_agent.favorites import ConversationFavorite


class ChatRepository(Protocol):
    def ensure_conversation(self, conversation_id: str, *, user_id: str) -> None:
        """幂等创建 conversation，并拒绝用户归属冲突。"""
        ...

    def append_message(
        self,
        conversation_id: str,
        *,
        role: str,
        content: str,
        message_id: str,
        client_message_id: str | None = None,
    ) -> ChatMessage:
        """按 (conversation_id, client_message_id) 或 message_id 幂等写入。"""
        ...

    def list_messages(
        self,
        conversation_id: str,
        *,
        after: str | None = None,
        limit: int = 50,
    ) -> list[ChatMessage]:
        """按时间顺序返回有限历史，供 onboard(messages=...) 使用。"""
        ...


class RunBootstrap(Protocol):
    def prepare_run(self, *, ctx: RunContext, profile: ConversationProfile) -> None:
        """幂等创建 conversation、profile 与 agent_run；graph_thread_id 固定为 run_id。"""
        ...


class ConversationFavoriteRepository(Protocol):
    def counts_by_conversation(
        self, conversation_ids: list[str], *, user_id: str
    ) -> dict[str, int]:
        """批量返回当前用户各会话的收藏数量。"""
        ...

    def add(
        self, conversation_id: str, *, user_id: str, listing_key: str
    ) -> ConversationFavorite:
        """幂等收藏当前用户会话中的房源。"""
        ...

    def remove(
        self, conversation_id: str, *, user_id: str, listing_key: str
    ) -> bool:
        """取消收藏；不存在时返回 False。"""
        ...

    def list(
        self, conversation_id: str, *, user_id: str
    ) -> list[ConversationFavorite]:
        """按收藏时间返回当前会话的收藏。"""
        ...
