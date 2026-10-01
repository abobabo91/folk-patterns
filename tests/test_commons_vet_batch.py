"""Offline Commons batch export and verdict import checks."""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))
import apply_vet_verdicts as apply  # noqa: E402
import cloud_vet_batch as cloud  # noqa: E402
import export_vet_batch as export  # noqa: E402
import vet_images as v  # noqa: E402


class CommonsVetBatchTest(unittest.TestCase):
    def test_export_collect_apply_and_stale_url(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            sidecar = root / "content" / "media" / "asia" / "ainu.json"
            sidecar.parent.mkdir(parents=True)
            data = {"ethnicity": "Ainu", "country": "Japan", "sources": {"commons": [
                {"title": "Robe", "description": "Worn at festival",
                 "thumb_url": "https://example.org/robe.jpg", "full_url": "https://example.org/full.jpg",
                 "editorial_reviewed": True, "editorial_reviewer": "human"},
                {"title": "Other", "full_url": "https://example.org/other.jpg", "vetted": False},
            ]}}
            sidecar.write_text(json.dumps(data), encoding="utf-8")
            with patch.object(export, "ROOT", root), patch.object(export, "OUT_DIR", root / "data" / "vet_batches"), \
                 patch.object(v, "MEDIA_DIR", root / "content" / "media"), \
                 patch.object(sys, "argv", ["export_vet_batch.py", "--name", "commons-test", "--commons", "--only", "Ainu"]):
                export.main()
            batch = root / "data" / "vet_batches" / "commons-test.jsonl"
            rows = [json.loads(line) for line in batch.read_text(encoding="utf-8").splitlines()]
            self.assertEqual(len(rows), 1)
            row = rows[0]
            self.assertEqual(row["id"], export.commons_id("content/media/asia/ainu.json", 0,
                                                            "https://example.org/robe.jpg"))
            self.assertEqual(row["urls"], ["https://example.org/robe.jpg"])
            self.assertEqual(row["prompt"], v.build_prompt("Ainu", "Japan", "photo",
                                                              title="Robe", desc="Worn at festival"))
            self.assertEqual(row["image_path"], f"work/img/{row['key']}.jpg")

            reply_dir = root / "work" / "replies"
            reply_dir.mkdir(parents=True)
            (reply_dir / f"{row['key']}.txt").write_text(
                "BELONGS: YES\nART_FORM: garment\nIMAGE: good\nERA: traditional\nREASON: robe",
                encoding="utf-8")
            with patch.object(cloud, "ROOT", root), patch.object(cloud, "WORK", root / "work"):
                cloud.collect("commons-test")
            verdicts = root / "data" / "vet_verdicts" / "commons-test.jsonl"
            with patch.object(apply, "ROOT", root), \
                 patch.object(sys, "argv", ["apply_vet_verdicts.py", str(verdicts), "--dry-run"]):
                before = sidecar.read_bytes()
                apply.main()
                self.assertEqual(sidecar.read_bytes(), before)
            with patch.object(apply, "ROOT", root), \
                 patch.object(sys, "argv", ["apply_vet_verdicts.py", str(verdicts)]):
                apply.main()
            photos = json.loads(sidecar.read_text(encoding="utf-8"))["sources"]["commons"]
            self.assertEqual({k: photos[0][k] for k in ("vetted", "vetted_art_form", "vetted_by",
                                                       "vetted_image", "vetted_era")},
                             {"vetted": True, "vetted_art_form": "garment", "vetted_by": apply.BY,
                              "vetted_image": "good", "vetted_era": "traditional"})
            self.assertTrue(photos[0]["editorial_reviewed"])
            self.assertFalse(photos[1]["vetted"])

            photos[0]["thumb_url"] = "https://example.org/changed.jpg"
            sidecar.write_text(json.dumps({**data, "sources": {"commons": photos}}), encoding="utf-8")
            before = sidecar.read_bytes()
            with patch.object(apply, "ROOT", root), \
                 patch.object(sys, "argv", ["apply_vet_verdicts.py", str(verdicts)]):
                apply.main()
            self.assertEqual(sidecar.read_bytes(), before)

    def test_force_and_download_failure(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            sidecar = root / "content" / "media" / "ainu.json"
            sidecar.parent.mkdir(parents=True)
            sidecar.write_text(json.dumps({"ethnicity": "Ainu", "country": "Japan", "sources": {
                "commons": [{"full_url": "https://example.org/1.jpg", "vetted": True,
                             "editorial_reviewed": True, "editorial_reviewer": "human"},
                            {"full_url": "https://example.org/2.jpg", "vetted": False}]} }), encoding="utf-8")
            with patch.object(export, "ROOT", root), patch.object(export, "OUT_DIR", root / "data" / "vet_batches"), \
                 patch.object(v, "MEDIA_DIR", root / "content" / "media"), \
                 patch.object(sys, "argv", ["export_vet_batch.py", "--name", "force-test", "--commons", "--force"]):
                export.main()
            rows = [json.loads(line) for line in (root / "data" / "vet_batches" / "force-test.jsonl").read_text(encoding="utf-8").splitlines()]
            self.assertEqual(len(rows), 2)
            work = root / "work"
            work.mkdir()
            (work / "fetch_force-test.json").write_text(
                json.dumps({r["key"]: "download-failed" for r in rows}), encoding="utf-8")
            with patch.object(cloud, "ROOT", root), patch.object(cloud, "WORK", work):
                cloud.collect("force-test")
            verdicts = root / "data" / "vet_verdicts" / "force-test.jsonl"
            self.assertTrue(all(json.loads(line)["force"] for line in verdicts.read_text(encoding="utf-8").splitlines()))
            with patch.object(apply, "ROOT", root), \
                 patch.object(sys, "argv", ["apply_vet_verdicts.py", str(verdicts)]):
                apply.main()
            photos = json.loads(sidecar.read_text(encoding="utf-8"))["sources"]["commons"]
            self.assertTrue(all("vetted" not in p and p["vetted_note"] == "download-failed: download-failed"
                                for p in photos))
            self.assertNotIn("editorial_reviewed", photos[0])
            self.assertNotIn("editorial_reviewer", photos[0])


if __name__ == "__main__":
    unittest.main()
