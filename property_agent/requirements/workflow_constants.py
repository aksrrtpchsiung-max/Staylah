"""Stores product constants shared by the workflow and reply templates that must not vary with tone."""

# Trusted caller identity for the single-user development phase. After login is integrated, this default value will be replaced by the authentication context.
DEFAULT_USER_ID = "local-development-user"

FALCON_SCOPE_MESSAGE = (
    "I'm Falcon, your all day housing agent. I can help you to find the listing "
    "that meet you best around Singapore. If you'd like to settle down in Singapore, "
    "feel free to reach out!"
)

SAFE_ERROR_MESSAGE = "Sorry, I can't process this request right now. Please try again later."
