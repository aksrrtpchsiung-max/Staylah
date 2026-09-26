"""保存 workflow 与回复模板共享、不可随语气变化的产品常量。"""

# 单用户开发阶段的可信调用身份。接入登录后由认证上下文替换此默认值。
DEFAULT_USER_ID = "local-development-user"

FALCON_SCOPE_MESSAGE = (
    "I'm Falcon, your all day housing agent. I can help you to find the listing "
    "that meet you best around Singapore. If you'd like to settle down in Singapore, "
    "feel free to reach out!"
)

SAFE_ERROR_MESSAGE = "Sorry, I can't process this request right now. Please try again later."
