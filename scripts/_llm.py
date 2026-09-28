"""Shared subscription CLI wrapper: Claude Code, or the Codex CLI once Claude's
usage nears its limit (folk_patterns.backend; FOLK_LLM_BACKEND=codex|claude
forces one). No paid inference API is used."""
from __future__ import annotations
import subprocess
import json
import os
import sys
from pathlib import Path


def ask(prompt: str, model: str = "claude-opus-5", timeout: int = 600) -> str:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
    from folk_patterns.backend import is_limit_error, mark_claude_limited, use_codex
    if use_codex():
        from folk_patterns.codex_cli import ask as codex_ask
        return codex_ask(prompt, timeout=timeout)
    proc = subprocess.run(
        f"claude --print --model {model}",
        shell=True,
        input=prompt.encode("utf-8"),
        capture_output=True,
        timeout=timeout,
    )
    if proc.returncode != 0:
        out = (proc.stdout + proc.stderr).decode("utf-8", errors="replace")
        if is_limit_error(out):
            mark_claude_limited(out)
            return ask(prompt, model, timeout)
        raise RuntimeError(
            "claude CLI failed: "
            + proc.stderr.decode("utf-8", errors="replace")
        )
    return proc.stdout.decode("utf-8", errors="replace").strip()


def ask_json(prompt: str, model: str = "claude-opus-5", timeout: int = 300) -> dict | list:
    """Ask claude and parse the first JSON block from the reply.

    We ask claude to emit only JSON, but strip common markdown fences just in
    case."""
    raw = ask(prompt + "\n\nReturn ONLY valid JSON. No prose, no code fences.", model, timeout)
    text = raw.strip()
    # strip ``` or ```json fences if present
    if text.startswith("```"):
        parts = text.split("```", 2)
        if len(parts) >= 2:
            text = parts[1]
            if text.lower().startswith("json"):
                text = text[4:]
            text = text.strip("` \n")
    # Some models emit prose before the JSON — find the first { or [
    for i, ch in enumerate(text):
        if ch in "{[":
            text = text[i:]
            break
    return json.loads(text)
