"""Profile repository responsibilities extracted without changing behavior."""
from __future__ import annotations
import copy
from typing import Any, Protocol



class ProfileRepository(Protocol):
    """定义 confirmed ConversationProfile 的持久化接口。"""

    def save(self, profile: dict[str, Any], *, user_id: str) -> None:
        """以当前调用用户身份原子保存一版 confirmed profile。"""


class InMemoryProfileRepository:
    """提供仅供本地开发和测试使用的非持久化 profile repository。"""

    def __init__(self) -> None:
        """初始化以 profile_id 为键的隔离内存存储。"""

        self._profiles: dict[str, dict[str, Any]] = {}

    def save(self, profile: dict[str, Any], *, user_id: str) -> None:
        """校验用户归属后深拷贝保存，避免后续 state 修改污染已确认版本。"""

        if profile.get("user_id") != user_id:
            raise PermissionError("profile.user_id does not match the current user")
        self._profiles[profile["profile_id"]] = copy.deepcopy(profile)

    def get(self, profile_id: str) -> dict[str, Any] | None:
        """读取指定 profile 的副本，缺失时返回 None。"""

        value = self._profiles.get(profile_id)
        return copy.deepcopy(value) if value is not None else None
