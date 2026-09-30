"""Offline checks for writeup batch export and import."""
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
import generate_writeups as gen  # noqa: E402
import cloud_writeup_batch as cloud  # noqa: E402
from folk_patterns.writeup import generate_writeup  # noqa: E402


class WriteupBatchTest(unittest.TestCase):
    def test_cloud_retries_and_resumes(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            batch = root / "data" / "writeup_batches" / "test_w.jsonl"
            batch.parent.mkdir(parents=True)
            row = {"id": "east_asia|Japan|Ainu", "country": "Japan", "ethnicity": "Ainu",
                   "region": "east-asia", "mode": "grounded", "prompt": "first prompt",
                   "sources": "known"}
            batch.write_text(json.dumps(row) + "\n", encoding="utf-8")
            with patch.object(cloud, "ROOT", root), \
                 patch.object(cloud, "run_claude", side_effect=["*invented*", "known"]) as model, \
                 patch.object(cloud, "restructure_writeup",
                              side_effect=[("", {}), ("short", {})]) as rewrite, \
                 patch.object(cloud, "audit", side_effect=[["bad rewrite"], []]):
                cloud.run("test_w", 1)
                cloud.run("test_w", 1)
            self.assertEqual(model.call_count, 2)
            self.assertTrue(model.call_args_list[1].args[0].startswith("first prompt\n\nA previous attempt"))
            self.assertEqual(rewrite.call_args_list[1].kwargs["feedback"], ["bad rewrite"])
            result = json.loads((root / "data" / "writeup_results" / "test_w.jsonl").read_text(encoding="utf-8"))
            self.assertEqual(result["short_markdown"], "short")
            self.assertEqual(len(result["restructure_attempts"]), 2)

    def test_export_prompt_and_import(self) -> None:
        batch_file = gen.BATCH_DIR / "test_w.jsonl"
        self.assertFalse(batch_file.exists(), "test_w batch already exists")
        self.addCleanup(batch_file.unlink, missing_ok=True)
        with patch.object(gen, "_extra_articles", return_value=[]), \
             patch.object(sys, "argv", ["generate_writeups.py", "east_asia", "--only", "Ainu",
                                        "--force", "--export-batch", "test_w"]):
            gen.main()
        rows = [json.loads(line) for line in batch_file.read_text(encoding="utf-8").splitlines()]
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row["id"], "east_asia|Japan|Ainu")
        seed = gen.load_seed("east_asia")
        eth = next(e for c in seed["countries"] if c["country"] == "Japan"
                   for e in c["ethnicities"] if e["name"] == "Ainu")
        wiki, ich = gen._load_grounding(row["region"], row["country"], row["ethnicity"])
        museum = gen.museum_records_text(row["region"], row["country"], row["ethnicity"])
        with patch("folk_patterns.writeup.run_claude", return_value="draft") as model:
            generate_writeup(row["country"], row["ethnicity"], row["region"], eth["traditions"],
                             wiki, ich, [], museum)
        self.assertEqual(row["prompt"], model.call_args.args[0])

        with tempfile.TemporaryDirectory() as temp:
            temp_dir = Path(temp)
            results = temp_dir / "results.jsonl"
            long_md = '---\ntitle: "Ainu"\nsubtitle: "Japan"\n---\n\n## Overview\n\nLong text.\n'
            short_md = '---\ntitle: "Ainu"\nsubtitle: "Japan"\n---\n\n## At a glance\n'
            result = {"id": row["id"], "long_markdown": long_md, "short_markdown": short_md,
                      "unsupported": [], "restructure_problems": [],
                      "restructure_attempts": [{"attempt": 1, "chars_before": len(long_md),
                                                "chars_after": len(short_md), "cost_usd": 0.01,
                                                "seconds": 2.0, "problems": []}]}
            results.write_text(json.dumps(result) + "\n", encoding="utf-8")
            with patch.object(gen, "REPO_ROOT", temp_dir), \
                 patch.object(gen, "CONTENT_DIR", temp_dir / "content"), \
                 patch.object(gen, "AUDIT_LOG", temp_dir / "audit.jsonl"), \
                 patch.object(gen, "RESTRUCTURE_LOG", temp_dir / "restructure.jsonl"), \
                 patch.object(gen, "_prune_traditions") as prune:
                gen.import_batch("test_w", results_path=results)
                target = gen.writeup_path(row["region"], row["country"], row["ethnicity"])
                self.assertEqual(target.read_text(encoding="utf-8"), short_md + "\n")
                self.assertEqual(target.with_suffix(".long.md").read_text(encoding="utf-8"), long_md)
                self.assertEqual(json.loads(gen.AUDIT_LOG.read_text(encoding="utf-8"))["articles"], row["articles"])
                self.assertEqual(json.loads(gen.RESTRUCTURE_LOG.read_text(encoding="utf-8"))["attempt"], 1)
                prune.assert_called_once()
                gen.import_batch("test_w", results_path=results)
                self.assertEqual(len(gen.AUDIT_LOG.read_text(encoding="utf-8").splitlines()), 1)
                result["short_markdown"] = None
                result["restructure_problems"] = ["no usable reply"]
                result["restructure_attempts"] = [
                    {"attempt": n, "chars_before": len(long_md), "chars_after": 0,
                     "cost_usd": 0.01, "seconds": 2.0, "problems": ["no usable reply"]}
                    for n in (1, 2)]
                results.write_text(json.dumps(result) + "\n", encoding="utf-8")
                gen.import_batch("test_w", force=True, results_path=results)
                self.assertEqual(target.read_text(encoding="utf-8"), long_md)
                self.assertFalse(target.with_suffix(".long.md").exists())


if __name__ == "__main__":
    unittest.main()
