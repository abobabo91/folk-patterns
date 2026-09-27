"""Subscription-backed Codex CLI calls for temporary Claude-limit fallback.

The caller supplies the whole prompt (and optionally a strict JSON schema).
Calls run in an empty temporary directory with read-only tools, no user MCP
config, and no saved session. This keeps a per-record judge call isolated.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
from pathlib import Path


MODEL = "gpt-5.6-luna"  # lowest-cost model currently available to this ChatGPT CLI account


def ask(prompt: str, *, image: bytes | None = None, schema: dict | None = None,
        timeout: int = 900, model: str = MODEL) -> str:
    with tempfile.TemporaryDirectory(prefix="folk-codex-") as tmp:
        d = Path(tmp)
        out = d / "answer.txt"
        cmd = [shutil.which("codex") or "codex", "exec", "-m", model,
               "-c", 'model_reasoning_effort="low"', "-s", "read-only",
               "--ephemeral", "--skip-git-repo-check", "--ignore-user-config",
               "--ignore-rules", "-C", str(d), "-o", str(out)]
        if image is not None:
            ext = (".png" if image.startswith(b"\x89PNG") else
                   ".webp" if image.startswith(b"RIFF") and image[8:12] == b"WEBP" else
                   ".gif" if image.startswith(b"GIF8") else ".jpg")
            path = d / ("image" + ext)
            path.write_bytes(image)
            cmd += ["-i", str(path)]
        if schema is not None:
            path = d / "schema.json"
            path.write_text(json.dumps(schema), encoding="utf-8")
            cmd += ["--output-schema", str(path)]
        cmd.append("-")
        result = subprocess.run(cmd, input=prompt, capture_output=True,
                                text=True, encoding="utf-8", timeout=timeout)
        if result.returncode or not out.exists():
            raise RuntimeError("codex exec failed: " +
                               (result.stderr or result.stdout)[-1200:])
        return out.read_text(encoding="utf-8").strip()
