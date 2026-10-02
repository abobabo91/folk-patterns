"""Offline checks for the Commons editorial reviewer."""
from __future__ import annotations

import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))
import commons_editorial as editorial  # noqa: E402


class CommonsEditorialTest(unittest.TestCase):
    def test_writes_verdicts_and_leaves_missing_reply_tile_unreviewed(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            sidecar = root / "content" / "media" / "east-asia" / "ainu.json"
            sidecar.parent.mkdir(parents=True)
            urls = ["https://example.org/one.jpg", "https://example.org/two.jpg"]
            photos = [
                {"title": "Robe", "description": "Ainu festival dress",
                 "source_category": "Ainu culture", "thumb_url": urls[0], "vetted": True},
                {"title": "Drum", "description": "Ainu music", "source_category": "Ainu",
                 "thumb_url": urls[1], "vetted": True},
            ]
            sidecar.write_text(json.dumps({"ethnicity": "Ainu", "country": "Japan",
                                           "sources": {"commons": photos}}), encoding="utf-8")

            review_dir = root / "work" / "commons-review" / sidecar.stem
            review_dir.mkdir(parents=True)
            for idx, url in enumerate(urls):
                path = review_dir / f"{idx:02}-{hashlib.sha1(url.encode()).hexdigest()[:10]}.jpg"
                Image.new("RGB", (80, 80), (30 + idx * 20, 80, 120)).save(path, format="JPEG")

            def fake_ask(prompt: str, *, image: bytes, schema: dict) -> str:
                self.assertIn("Ainu", prompt)
                self.assertIn("Japan", prompt)
                self.assertIn("#0", prompt)
                self.assertIn("#1", prompt)
                self.assertTrue(image)
                self.assertEqual(schema, editorial.REVIEW_SCHEMA)
                return json.dumps({"reviews": [{"idx": 0, "pass": True,
                                                  "reason": "The caption and visible dress are specifically Ainu."}]})

            transcript = root / "work" / "commons-editorial" / "transcript.jsonl"
            with patch.object(editorial, "COMMONS_REVIEW_DIR", root / "work" / "commons-review"), \
                    patch.object(editorial, "EDITORIAL_DIR", root / "work" / "commons-editorial"), \
                    patch.object(editorial, "TRANSCRIPT", transcript), \
                    patch.object(editorial, "ask", side_effect=fake_ask):
                tally = editorial.process_sidecar(sidecar)

            result = json.loads(sidecar.read_text(encoding="utf-8"))["sources"]["commons"]
            self.assertTrue(result[0]["editorial_reviewed"])
            self.assertTrue(result[0]["editorial_reviewer"].startswith("codex-editorial-"))
            self.assertEqual(result[0]["editorial_reason"],
                             "The caption and visible dress are specifically Ainu.")
            self.assertNotIn("editorial_reviewed", result[1])
            self.assertEqual(tally["passed"], 1)
            self.assertEqual(tally["failed"], 0)
            self.assertEqual(tally["unreviewed"], 1)
            log = json.loads(transcript.read_text(encoding="utf-8").splitlines()[0])
            self.assertEqual(log["indices"], [0, 1])
            self.assertIn("raw_reply", log)


if __name__ == "__main__":
    unittest.main()
