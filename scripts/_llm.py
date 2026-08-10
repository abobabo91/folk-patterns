"""Shared Claude CLI wrapper. Uses the subscription (per global user rules —
never the paid API without asking). One tiny wrapper so every script that
calls the LLM uses the same command, model, and error handling."""
from __future__ import annotations
import subprocess
import json


def ask(prompt: str, model: str = "claude-opus-5", timeout: int = 600) -> str:
    proc = subprocess.run(
        f"claude --print --model {model}",
        shell=True,
        input=prompt.encode("utf-8"),
        capture_output=True,
        timeout=timeout,
    )
    if proc.returncode != 0:
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
