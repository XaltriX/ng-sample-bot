"""
In-memory session store for multi-step conversations.
Key: user_id (int)
Value: dict with arbitrary state keys
"""

_sessions: dict[int, dict] = {}


def get(user_id: int) -> dict:
    return _sessions.setdefault(user_id, {})


def set_key(user_id: int, key: str, value):
    _sessions.setdefault(user_id, {})[key] = value


def clear(user_id: int):
    _sessions.pop(user_id, None)


def get_key(user_id: int, key: str, default=None):
    return _sessions.get(user_id, {}).get(key, default)