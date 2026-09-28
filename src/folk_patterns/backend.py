"""Which subscription CLI answers a model call: Claude Code or Codex.

`use_codex()` is the single switch every judge, writer and helper consults.
Standard library only, so the stdlib-only judge (scripts/vet_judge.py) can
import it.

- FOLK_LLM_BACKEND=codex or =claude forces a backend.
- Otherwise it is automatic: Claude until its subscription usage reaches
  FOLK_CLAUDE_MAX_PCT (default 90) percent of the 5-hour session or the weekly
  limit, then Codex. The usage comes from the same OAuth endpoint Claude Code's
  own /usage reads (api.anthropic.com/api/oauth/usage, with the token in
  ~/.claude/.credentials.json), cached for five minutes in
  work/claude_usage.json. If it cannot be read, Claude is used.
- A Claude reply that says the limit is hit calls `mark_claude_limited()`,
  which forces Codex until the reported reset (or for an hour when unknown).
"""
from __future__ import annotations

import json
import os
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
STATE = ROOT / "work" / "claude_usage.json"
CACHE_S = 300
LIMIT_WORDS = ("hit your limit", "usage limit", "limit reached", "out of extra usage")


def _now() -> float:
    return time.time()


def _read_state() -> dict:
    try:
        return json.loads(STATE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _write_state(state: dict) -> None:
    try:
        STATE.parent.mkdir(parents=True, exist_ok=True)
        STATE.write_text(json.dumps(state), encoding="utf-8")
    except OSError:
        pass


def _ts(iso: str | None) -> float:
    try:
        return datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp()
    except (AttributeError, ValueError):
        return 0.0


def claude_usage() -> dict | None:
    """{'session': pct, 'weekly': pct, 'session_reset': ts, 'weekly_reset': ts},
    or None when the endpoint cannot be read."""
    try:
        cred = json.loads((Path.home() / ".claude" / ".credentials.json").read_text(encoding="utf-8"))
        token = cred["claudeAiOauth"]["accessToken"]
        req = urllib.request.Request(
            "https://api.anthropic.com/api/oauth/usage",
            headers={"Authorization": f"Bearer {token}", "anthropic-beta": "oauth-2025-04-20",
                     "User-Agent": "folk-patterns/0.1"})
        data = json.load(urllib.request.urlopen(req, timeout=15))
    except Exception:
        return None
    out = {}
    for key, name in (("five_hour", "session"), ("seven_day", "weekly")):
        block = data.get(key) or {}
        out[name] = float(block.get("utilization") or 0)
        out[name + "_reset"] = _ts(block.get("resets_at"))
    return out


def mark_claude_limited(message: str = "") -> None:
    state = _read_state()
    usage = claude_usage() or {}
    resets = [t for t in (usage.get("session_reset"), usage.get("weekly_reset")) if t and t > _now()]
    state["limited_until"] = min(resets) if resets else _now() + 3600
    state["limited_reason"] = message[:200]
    _write_state(state)
    print(f"  [backend] Claude limit reached, switching to Codex until "
          f"{datetime.fromtimestamp(state['limited_until'], timezone.utc):%Y-%m-%d %H:%M} UTC", flush=True)


def is_limit_error(text: str) -> bool:
    low = (text or "").lower()
    return any(word in low for word in LIMIT_WORDS)


def use_codex() -> bool:
    forced = os.getenv("FOLK_LLM_BACKEND", "").lower()
    if forced in ("codex", "claude"):
        return forced == "codex"
    state = _read_state()
    if state.get("limited_until", 0) > _now():
        return True
    if _now() - state.get("checked_at", 0) > CACHE_S:
        usage = claude_usage()
        state["checked_at"] = _now()
        if usage:
            state["usage"] = usage
        _write_state(state)
    usage = state.get("usage")
    if not usage:
        return False
    cap = float(os.getenv("FOLK_CLAUDE_MAX_PCT", "90"))
    codex = usage["session"] >= cap or usage["weekly"] >= cap
    if codex and not state.get("announced"):
        state["announced"] = True
        _write_state(state)
        print(f"  [backend] Claude usage {usage['session']:.0f}% session / {usage['weekly']:.0f}% weekly "
              f">= {cap:.0f}%: using Codex", flush=True)
    elif not codex and state.get("announced"):
        state["announced"] = False
        _write_state(state)
    return codex


if __name__ == "__main__":
    u = claude_usage()
    print(u and f"Claude usage: {u['session']:.0f}% session, {u['weekly']:.0f}% weekly")
    print("backend:", "codex" if use_codex() else "claude")
