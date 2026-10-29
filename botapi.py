"""
Thin Telegram Bot API client (HTTP).

Used for messages that carry COLORED inline buttons (the `style` field), which
pyrofork 2.3.x cannot send. If Telegram rejects a styled button, the call is
retried once without colors, so a post never fails because of styling.
"""
import asyncio
import copy

import aiohttp

API_BASE = "https://api.telegram.org"

_session: aiohttp.ClientSession | None = None


class BotApiError(Exception):
    pass


def url_btn(text: str, url: str, style: str | None = None) -> dict:
    """style: 'primary' (blue) | 'success' (green) | 'danger' (red) | None"""
    btn = {"text": text, "url": url}
    if style:
        btn["style"] = style
    return btn


def markup(*rows: list[dict]) -> dict:
    return {"inline_keyboard": [list(r) for r in rows]}


def _get_session() -> aiohttp.ClientSession:
    global _session
    if _session is None or _session.closed:
        _session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=60))
    return _session


async def call(token: str, method: str, payload: dict):
    for attempt in range(2):
        async with _get_session().post(f"{API_BASE}/bot{token}/{method}", json=payload) as r:
            data = await r.json(content_type=None)
        if data.get("ok"):
            return data["result"]
        retry = (data.get("parameters") or {}).get("retry_after")
        if retry and attempt == 0:  # flood wait → wait once and retry
            await asyncio.sleep(retry + 1)
            continue
        raise BotApiError(data.get("description", "unknown error"))


def _strip_style(payload: dict) -> dict:
    p = copy.deepcopy(payload)
    for row in (p.get("reply_markup") or {}).get("inline_keyboard", []):
        for b in row:
            b.pop("style", None)
    return p


def _has_style(payload: dict) -> bool:
    rows = (payload.get("reply_markup") or {}).get("inline_keyboard", [])
    return any("style" in b for row in rows for b in row)


async def call_styled(token: str, method: str, payload: dict):
    """Call with colored buttons; fall back to plain buttons if rejected."""
    try:
        return await call(token, method, payload)
    except BotApiError:
        if not _has_style(payload):
            raise
        return await call(token, method, _strip_style(payload))
