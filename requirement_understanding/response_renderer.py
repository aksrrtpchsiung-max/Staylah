"""将结构化 workflow 结果渲染为可切换语气的用户回复。"""

from typing import Literal

from .workflow_constants import FALCON_SCOPE_MESSAGE, SAFE_ERROR_MESSAGE


ToneName = Literal["direct", "warm", "concise"]


class ResponseRenderer:
    """集中管理 Falcon 回复模板，确保语气变化不修改业务状态。"""

    def __init__(self, tone: ToneName = "warm") -> None:
        """选择直接、温和专业或极简三种本地调试语气。"""

        self.tone = tone

    def out_of_scope(self) -> str:
        """返回产品要求不可改写的固定范围提示。"""

        return FALCON_SCOPE_MESSAGE

    def error(self) -> str:
        """返回不泄露内部异常的安全错误提示。"""

        if self.tone == "concise":
            return "I can't process that right now. Please try again later."
        if self.tone == "direct":
            return SAFE_ERROR_MESSAGE
        return (
            "Sorry, I ran into a problem while processing your requirements. "
            "Please try again later, and I'll continue helping you refine your housing needs."
        )

    def cancelled(self) -> str:
        """返回用户取消当前找房流程时的提示。"""

        if self.tone == "concise":
            return "This housing request has been cancelled."
        if self.tone == "direct":
            return "Your housing request has been cancelled."
        return "Your housing request has been cancelled. Feel free to return whenever you need help."

    def clarification(self, questions: list[dict[str, str]]) -> str:
        """把结构化问题渲染为不超过三项的澄清回复。"""

        body = "\n".join(f"{index}. {item['text']}" for index, item in enumerate(questions, 1))
        if self.tone == "concise":
            return body
        if self.tone == "direct":
            return f"Please provide the following information:\n{body}"
        return (
            "I've noted your requirements so far. To narrow down the search more accurately, "
            f"please provide:\n{body}"
        )

    def confirmation(self, summary: str) -> str:
        """渲染与结构化 profile 严格一致的需求确认回复。"""

        if self.tone == "concise":
            return f"Requirements: {summary}\nIs this correct?"
        if self.tone == "direct":
            return f"I understand your requirements as follows: {summary}\nIs this correct?"
        return (
            f"Here is my understanding of your requirements:\n{summary}\n"
            "Please confirm whether this is correct, or tell me what you would like to change."
        )

    def ready_for_b(self) -> str:
        """返回 confirmed profile 已准备交给 B 时的提示。"""

        if self.tone == "concise":
            return "Requirements confirmed."
        if self.tone == "direct":
            return "Requirements confirmed and ready for the listing retrieval module."
        return "Your requirements are confirmed. I'll continue searching for suitable listings."
