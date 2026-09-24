"""The image judge: its prompt and the one CLI call. Standard library only, so
the cloud batch helper (scripts/cloud_vet_batch.py) imports it with no setup;
the local vetter (scripts/vet_images.py) imports it too, so both send
byte-identical calls.

A call is one bare `claude --print`: no tools, no MCP servers, no user
settings, run from a temporary directory so no CLAUDE.md is picked up. The
fixed rules are the system prompt, identical on every call, so the CLI's
prompt cache serves them after the first call; the user message is the
record block plus the image, inline as base64. Measured 2026-09-24 on 117
records: ~$0.011 per record, against $0.035 with the rules in the user
message (docs/cloud-vetting.md).
"""
from __future__ import annotations

import base64
import io
import json
import shutil
import subprocess
import tempfile
import time
from pathlib import Path

# Sonnet, not Haiku: on the 2026-09-24 calibration Haiku judged BELONGS as
# well but called every weak image "good" (0/3 vs 2/3). Sonnet is rate-limited
# server-side above ~3 parallel calls — keep workers at 3.
MODEL = "claude-sonnet-5"
MAX_EDGE = 1024

SYSTEM_PROMPT = """\
You vet images for an ethnographic collection organised by ethnic group. The user message gives one record — its claimed ethnicity and category, and the holding museum's metadata — and the image. Judge the picture first; the text is context and is sometimes wrong.

BELONGS — YES if the picture shows something the claimed people made, wore, built or used: textiles, dress, jewellery, pots, tools, weapons, instruments, furniture, masks and ritual objects, buildings and their ornament (monumental, famous or ruined ones included), court and temple art made within the culture, documentary photos of their dress, craft or daily life (in any photographic style), and archaeology of their homeland. Pattern is not required. An object plausible for the region is enough; neighbouring groups cannot be told apart.
NO only on positive evidence: the culture pictured by outsiders (European art, a named European artist, travel engravings); museums or galleries themselves; a map, diagram, flag, logo, screenshot, specimen or placeholder; a modern politician or celebrity; a snapshot whose subject is something else; the ethnonym only an incidental word in the title; the record naming a different people ("Shan cloth" filed under Bamar); or the unmistakable style of a distant tradition (a Japanese print under Kongo).
"Location on file" is usually the holding museum's country, never a reason for NO.

ART_FORM — the best fit for what the picture shows: textile (cloth, carpet, felt, not worn) · garment (clothing, incl. a sarong or longyi cloth; headwear, footwear) · jewelry (jewellery, beadwork, adornment) · ceramic (clay or porcelain vessels, loose tiles) · metalwork (metal vessels, lamps, boxes, tools) · arms (weapons, shields, armour) · masks-ritual (masks and objects used in ritual — bells, power figures, amulets — whatever the material) · sculpture (figures and carvings) · instruments (musical) · household (baskets, furniture, utensils) · architectural (buildings, their ornament and interiors) · painting-mss (paintings, drawings, manuscripts, prints) · photo (people or a scene) · unclassified.

IMAGE — good: the subject is clear · weak: small in the frame, obscured, dark or a fragment · unusable: nothing can be made out (blank, placeholder, tiny thumbnail).

ERA — traditional: handmade or traditional life, any date · modern: industrial or mass-produced (machine-printed cloth such as a kanga, factory-woven blankets), modern building, contemporary studio art · archaeological: excavated, or made centuries ago in a tradition no longer practised (Neolithic pottery, an Angkor-era or 10th-century temple bronze or ritual bell now in a museum) — even when it is well made and court or temple art. A painting or manuscript of a living tradition (a Shahnama folio, a Mughal album page) is traditional, however old; one recovered from a tomb (a Book of the Dead) is archaeological.

Reply in exactly this format:
REASON: <one or two sentences: what the picture shows, and why it does or does not fit the claimed ethnicity>
BELONGS: <YES or NO>
ART_FORM: <one category>
IMAGE: <GOOD, WEAK or UNUSABLE>
ERA: <TRADITIONAL, MODERN or ARCHAEOLOGICAL>
CONFIDENCE: <HIGH, MEDIUM or LOW>
"""

RECORD_TEMPLATE = """\
THIS RECORD CLAIMS TO BE
  ethnicity : {ethnicity}   ({country})
  category  : {current_af}

THE HOLDING MUSEUM'S METADATA
  title:            {title}
  description:      {desc}
  location on file: {place}
"""

# Sonnet answers "Server is temporarily limiting requests (not your usage
# limit)" in bursts, even at 3 workers (2026-09-24: 43 of 50 calls in one
# run). It clears within minutes, so back off and retry.
RATE_LIMIT_BACKOFF = (30, 60, 120, 240, 300)


def build_record(ethnicity: str, country: str, current_af: str,
                 title: str = "", desc: str = "", place: str = "") -> str:
    """The user-message text for one record."""
    return RECORD_TEMPLATE.format(
        ethnicity=ethnicity, country=country, current_af=current_af,
        title=(title or "(none recorded)")[:200],
        desc=(desc or "(none recorded)")[:600],
        place=(place or "(none recorded)")[:120],
    )


def downscale(data: bytes) -> bytes:
    """Shrink to MAX_EDGE on the long side: image tokens scale with pixel
    area, and 1024 px did not change a verdict (pilot 2, 2026-09-24).
    Pillow is optional; without it the original bytes are kept."""
    try:
        from PIL import Image
    except ImportError:
        return data
    try:
        im = Image.open(io.BytesIO(data))
        if max(im.size) <= MAX_EDGE:
            return data
        im.thumbnail((MAX_EDGE, MAX_EDGE))
        out = io.BytesIO()
        im.convert("RGB").save(out, "JPEG", quality=85)
        return out.getvalue()
    except Exception:  # an image Pillow cannot decode is sent as it is
        return data


def _media_type(data: bytes) -> str:
    if data.startswith(b"\x89PNG"):
        return "image/png"
    if data.startswith(b"GIF8"):
        return "image/gif"
    if data.startswith(b"RIFF") and data[8:12] == b"WEBP":
        return "image/webp"
    return "image/jpeg"


def _is_rate_limited(text: str) -> bool:
    t = text.lower()
    return any(k in t for k in ("rate limit", "limiting requests", "overloaded", "529"))


_CALL_DIR: Path | None = None


def _call_dir() -> Path:
    """A directory outside any repo holding the system prompt file and an
    empty MCP config; calls run from it so no CLAUDE.md is discovered."""
    global _CALL_DIR
    if _CALL_DIR is None:
        d = Path(tempfile.mkdtemp(prefix="vet-judge-"))
        (d / "system.txt").write_text(SYSTEM_PROMPT, encoding="utf-8")
        (d / "empty_mcp.json").write_text('{"mcpServers":{}}', encoding="utf-8")
        _CALL_DIR = d
    return _CALL_DIR


def judge(record_text: str, image: bytes, timeout: int = 180,
          on_attempt=None) -> tuple[str, str]:
    """Judge one record. Returns (reply, error): the reply text when the call
    produced a verdict, else "" and the reason. `image` is downscaled here.
    `on_attempt(attempt, seconds, result_event_or_None, stderr)` is called
    after every attempt, for raw logging."""
    data = downscale(image)
    msg = json.dumps({"type": "user", "message": {"role": "user", "content": [
        {"type": "text", "text": record_text},
        {"type": "image", "source": {"type": "base64", "media_type": _media_type(data),
                                     "data": base64.b64encode(data).decode()}}]}}) + "\n"
    d = _call_dir()
    cmd = [shutil.which("claude") or "claude", "--print", "--verbose",
           "--no-session-persistence", "--setting-sources", "local", "--model", MODEL,
           "--input-format", "stream-json", "--output-format", "stream-json",
           "--system-prompt-file", str(d / "system.txt"), "--tools", "",
           "--strict-mcp-config", "--mcp-config", str(d / "empty_mcp.json")]
    for attempt in range(len(RATE_LIMIT_BACKOFF) + 1):
        t0 = time.time()
        try:
            res = subprocess.run(cmd, input=msg, capture_output=True, text=True,
                                 encoding="utf-8", timeout=timeout, cwd=d)
            out, err = res.stdout or "", res.stderr or ""
        except subprocess.TimeoutExpired:
            out, err = "", f"timeout after {timeout} s"
        result = None
        for line in out.splitlines():
            try:
                ev = json.loads(line)
            except json.JSONDecodeError:
                continue
            if ev.get("type") == "result":
                result = ev
        text = (result or {}).get("result") or ""
        if on_attempt:
            on_attempt(attempt, round(time.time() - t0, 1), result, err)
        if result and not result.get("is_error") and "BELONGS:" in text:
            return text, ""
        if attempt < len(RATE_LIMIT_BACKOFF) and _is_rate_limited(text + err):
            time.sleep(RATE_LIMIT_BACKOFF[attempt])
            continue
        return "", " ".join((text or err).split())[:200] or "no result event"
    return "", "rate limited through every retry"
