"""测试和本地无库调试使用的会话/聊天内存实现。"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from property_agent.contracts import ChatMessage


@dataclass
class InMemoryChatRepository:
    """按 conversation 隔离的内存聊天记录，形状与 SqlChatRepository 一致。"""

    conversations: dict[str, str] = field(default_factory=dict)
    messages: dict[str, list[ChatMessage]] = field(default_factory=dict)
    by_id: dict[str, ChatMessage] = field(default_factory=dict)

    def ensure_conversation(self, conversation_id: str, *, user_id: str) -> None:
        existing = self.conversations.get(conversation_id)
        if existing is None:
            self.conversations[conversation_id] = user_id
            self.messages.setdefault(conversation_id, [])
            return
        if existing != user_id:
            raise PermissionError("conversation 不属于当前用户")

    def append_message(
        self,
        conversation_id: str,
        *,
        role: str,
        content: str,
        message_id: str,
        client_message_id: str | None = None,
    ) -> ChatMessage:
        if conversation_id not in self.conversations:
            raise KeyError(conversation_id)
        stored = self.by_id.get(message_id)
        message: ChatMessage = {"message_id": message_id, "role": role, "text": content}  # type: ignore[typeddict-item]
        if stored is not None:
            if stored != message:
                raise ValueError("相同 message_id 对应了不同内容")
            return stored
        self.by_id[message_id] = message
        self.messages.setdefault(conversation_id, []).append(message)
        return message

    def list_messages(
        self,
        conversation_id: str,
        *,
        after: str | None = None,
        limit: int = 50,
    ) -> list[ChatMessage]:
        items = list(self.messages.get(conversation_id, []))
        if after:
            seen = False
            filtered: list[ChatMessage] = []
            for item in items:
                if seen:
                    filtered.append(item)
                elif item["message_id"] == after:
                    seen = True
            items = filtered
        return items[:limit]
