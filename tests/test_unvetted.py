import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts import unvetted


class UnvettedTests(unittest.TestCase):
    def _write_jsonl(self, path: Path, rows):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")

    def _paths(self, root: Path):
        world = root / "world"
        data = root / "data"
        ethnicities = data / "ethnicities"
        objects = data / "objects"
        unvetted = data / "unvetted"
        for path in (world, ethnicities, objects, unvetted):
            path.mkdir(parents=True, exist_ok=True)
        return world, data, ethnicities, objects, unvetted

    def test_resolve_filters_and_resumes_from_a_truncated_cache(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            world, data, ethnicities, objects, unvetted_dir = self._paths(root)
            candidate = {
                "key": "Q1", "label": "Test people", "country": "Testland",
                "objects": {
                    "textile": [
                        {"source": "bm", "id": "good", "name": "cloth"},
                        {"source": "bm", "id": "excluded", "name": "cloth"},
                        {"source": "met", "id": "skull", "name": "skull"},
                        {"source": "cleveland", "id": "kept", "name": "cloth"},
                        {"source": "met", "id": "already", "name": "cloth"},
                    ],
                },
            }
            self._write_jsonl(world / "candidates.jsonl", [candidate])
            self._write_jsonl(world / "pick_coverage.jsonl", [
                {"key": "Q1", "source": "bm", "id": "good", "status": "awaiting_judge"},
                {"key": "Q1", "source": "bm", "id": "excluded", "status": "not_reached"},
                {"key": "Q1", "source": "met", "id": "skull", "status": "unclassified"},
                {"key": "Q1", "source": "cleveland", "id": "kept", "status": "kept"},
                {"key": "Q1", "source": "met", "id": "already", "status": "fetch_failed"},
            ])
            (world / "pick_exclusions.json").write_text(
                json.dumps([{"key": "Q1", "source": "bm", "id": "excluded"}]), encoding="utf-8"
            )
            (world / "picks").mkdir()
            (world / "picks" / "Q1.json").write_text(json.dumps({"name": "Test"}), encoding="utf-8")
            (world / "unvetted_details.jsonl").write_text(
                '{"source":"bm","id":"cached","ok":true}\n{"source":"bm",', encoding="utf-8"
            )
            objects.joinpath("met-already.json").write_text("{}", encoding="utf-8")

            def detail(obj, bm_client, http):
                return {"title": f"Title {obj['id']}", "image_url": f"https://img/{obj['id']}.jpg"}

            with patch.object(unvetted, "WORLD_DIR", world), \
                 patch.object(unvetted, "DATA_DIR", data), \
                 patch.object(unvetted, "ETHNICITIES_DIR", ethnicities), \
                 patch.object(unvetted, "OBJECTS_DIR", objects), \
                 patch.object(unvetted, "UNVETTED_DIR", unvetted_dir), \
                 patch.object(unvetted, "_detail", detail), \
                 patch.object(unvetted, "_in_library", lambda oid: False):
                unvetted.cmd_resolve([])

            rows = unvetted._jsonl(world / "unvetted_details.jsonl")
            self.assertEqual({(r["source"], r["id"]) for r in rows}, {("bm", "good"), ("bm", "cached")})
            self.assertTrue(next(r for r in rows if r["id"] == "good")["ok"])
            self.assertNotIn("skull", {r["id"] for r in rows})

    def test_build_country_tiebreak_other_and_stale_cleanup(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            world, data, ethnicities, objects, unvetted_dir = self._paths(root)
            candidate = {
                "key": "Q2", "label": "Example people", "country": "Canada",
                "objects": {
                    "textile": [{"source": "met", "id": "1", "name": "Textile"}],
                    "unclassified": [{"source": "europeana", "id": "/x/2", "name": "Record"}],
                },
            }
            self._write_jsonl(world / "candidates.jsonl", [candidate])
            self._write_jsonl(world / "pick_coverage.jsonl", [
                {"key": "Q2", "source": "met", "id": "1", "status": "not_reached"},
                {"key": "Q2", "source": "europeana", "id": "/x/2", "status": "unclassified"},
            ])
            (world / "pick_exclusions.json").write_text("[]", encoding="utf-8")
            (world / "picks").mkdir()
            (world / "picks" / "Q2.json").write_text(json.dumps({"name": "Example"}), encoding="utf-8")
            for key, country in (("region__usa__example", "United States"), ("region__canada__example", "Canada")):
                (ethnicities / f"{key}.json").write_text(json.dumps({
                    "key": key, "region": "region", "country": country, "ethnicity": "Example",
                }), encoding="utf-8")
            (unvetted_dir / "stale.json").write_text("{}", encoding="utf-8")
            self._write_jsonl(world / "unvetted_details.jsonl", [
                {"source": "met", "id": "1", "title": "A textile", "image_url": "https://img/1", "ok": True},
                {"source": "europeana", "id": "/x/2", "title": "Other", "image_url": "https://img/2", "ok": True},
            ])

            with patch.object(unvetted, "WORLD_DIR", world), \
                 patch.object(unvetted, "DATA_DIR", data), \
                 patch.object(unvetted, "ETHNICITIES_DIR", ethnicities), \
                 patch.object(unvetted, "OBJECTS_DIR", objects), \
                 patch.object(unvetted, "UNVETTED_DIR", unvetted_dir):
                unvetted.cmd_build([])

            shard_path = unvetted_dir / "region__canada__example.json"
            self.assertTrue(shard_path.exists())
            self.assertFalse((unvetted_dir / "region__usa__example.json").exists())
            shard = json.loads(shard_path.read_text(encoding="utf-8"))
            self.assertEqual(shard["count"], 2)
            self.assertEqual([x["id"] for x in shard["buckets"]["textile"]], ["1"])
            self.assertEqual([x["id"] for x in shard["other"]], ["/x/2"])
            self.assertEqual(json.loads((unvetted_dir / "index.json").read_text(encoding="utf-8")), {"region__canada__example": 2})
            self.assertFalse((unvetted_dir / "stale.json").exists())

    def test_no_pick_candidates_are_unvetted(self):
        with tempfile.TemporaryDirectory() as td:
            world = Path(td) / "world"
            world.mkdir()
            with patch.object(unvetted, "WORLD_DIR", world):
                self.assertEqual(unvetted._candidate_status("Q-no-pick", ("Q-no-pick", "met", "1"), {}), "no_pick")


if __name__ == "__main__":
    unittest.main()
