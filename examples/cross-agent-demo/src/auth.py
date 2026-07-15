"""Small fixture used by the Mnemex cross-agent continuity walkthrough."""


def authenticate(token: str) -> bool:
    """Authenticate a request using the shared token validator."""
    return validate_token(token)


def validate_token(token: str) -> bool:
    """Return whether a supplied token is acceptable."""
    return bool(token)
