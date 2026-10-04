import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts import build_index, unvetted, world_peoples


class WorldPeoplesTests(unittest.TestCase):
    def test_multilingual_variants_keep_original_scripts_and_dedupe(self):
        with tempfile.TemporaryDirectory() as td:
            out = Path(td)
            (out / "labels.json").write_text(json.dumps({
                "Q1": {"de": ["Testvolk", "test people"], "ru": ["тест"], "fr": ["ABC"]},
            }), encoding="utf-8")
            with patch.object(world_peoples, "OUT", out):
                values = world_peoples._ml_variants("Q1", "Test people")
            self.assertEqual(values[:2], ["Test", "Testvolk"])
            self.assertIn("тест", values)
            self.assertNotIn("ABC", values)
            self.assertEqual(len({value.casefold() for value in values}), len(values))

    def test_multilingual_count_aggregates_all_variants(self):
        self.assertEqual(
            world_peoples._europeana_count(
                "Q1", {"Q1": {"hits": {"English": 2}}},
                {"Q1": {"hits": {"Deutsch": 4, "Русский": 5}}},
            ),
            9,
        )
        self.assertEqual(
            world_peoples._europeana_count(
                "Q1", {"Q1": {"hits": {"English": 12}}},
                {"Q1": {"hits": {"Deutsch": 4, "Русский": 5}}},
            ),
            12,
        )

    def test_labels_resume_uses_batched_sparql_result(self):
        with tempfile.TemporaryDirectory() as td:
            out = Path(td)
            (out / "wikidata.json").write_text(json.dumps([{"qid": "Q1"}, {"qid": "Q2"}]), encoding="utf-8")
            (out / "labels.json").write_text(json.dumps({"Q1": {"en": ["Already"]}}), encoding="utf-8")
            queries = []

            def sparql(query):
                queries.append(query)
                return [{"g": "http://www.wikidata.org/entity/Q2", "lang": "hu", "value": "Minta nép"}]

            with patch.object(world_peoples, "OUT", out), patch.object(world_peoples, "_sparql", sparql):
                world_peoples.cmd_labels()
            self.assertEqual(len(queries), 1)
            labels = json.loads((out / "labels.json").read_text(encoding="utf-8"))
            self.assertEqual(labels["Q1"]["en"], ["Already"])
            self.assertEqual(labels["Q2"]["hu"], ["Minta nép"])
            self.assertEqual(len(labels["Q2"]), len(world_peoples.LABEL_LANGS))

    def test_unvetted_only_allows_nations_but_not_umbrella_skips(self):
        row = {"key": "Q1", "listed": False, "people": True, "bm": 3, "local": 1, "europeana": 2}
        self.assertTrue(world_peoples._unvetted_only(row, {"Q1": "nation"}))
        self.assertFalse(world_peoples._unvetted_only(row, {"Q1": "umbrella"}))
        self.assertFalse(world_peoples._unvetted_only({**row, "people": False}, {}))
        self.assertFalse(world_peoples._unvetted_only({**row, "bm": 2}, {}))

    def test_stub_region_centroid_and_jitter_are_deterministic(self):
        with tempfile.TemporaryDirectory() as td:
            geojson = Path(td) / "countries.geojson"
            geojson.write_text(json.dumps({"features": [{
                "type": "Feature", "properties": {"name": "Testland"},
                "geometry": {"type": "Polygon", "coordinates": [[[0, 0], [2, 0], [2, 2], [0, 2], [0, 0]]]}
            }]}), encoding="utf-8")
            self.assertEqual(unvetted._country_centroid("Testland", geojson), (1.0, 1.0))
            jitter = unvetted._stub_jitter("Q1")
            self.assertEqual(jitter, unvetted._stub_jitter("Q1"))
            self.assertTrue(all(-1.5 <= value <= 1.5 for value in jitter))
            self.assertEqual(unvetted._site_region("Asia", "Central Asia"), "central-asia")

    def test_codex_classification_is_cached_without_network(self):
        with tempfile.TemporaryDirectory() as td:
            out = Path(td)
            (out / "wikidata.json").write_text(json.dumps([{
                "qid": "Q1", "label": "Test people", "article": "https://en.wikipedia.org/wiki/Test",
                "sitelinks": 25, "country": "Testland",
            }]), encoding="utf-8")
            reply = json.dumps([{"key": "Q1", "people": True, "continent": "Europe",
                                 "region": "Europe", "country": "Testland"}])
            with patch.object(world_peoples, "OUT", out), \
                 patch.object(world_peoples, "_summary", return_value="A people with crafts."), \
                 patch("folk_patterns.codex_cli.ask", return_value=reply) as ask:
                world_peoples.cmd_classify(30, backend="codex", all_min_sitelinks=20)
            ask.assert_called_once()
            cached = json.loads((out / "classified.json").read_text(encoding="utf-8"))
            self.assertTrue(cached["Q1"]["people"])
            raw = json.loads((out / "classify_raw.jsonl").read_text(encoding="utf-8").splitlines()[0])
            self.assertEqual(raw["backend"], "codex")

    def test_build_index_stub_does_not_change_object_total(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            data = root / "data"
            (data / "seed").mkdir(parents=True)
            (data / "unvetted").mkdir(parents=True)
            (root / "content").mkdir()
            (root / "library").mkdir()
            (data / "seed" / "europe.json").write_text(json.dumps({
                "region": "europe", "countries": [{"country": "Canada", "ethnicities": [{
                    "name": "Example", "traditions": [], "homeland": {"lat": 1, "lon": 2}
                }]}]
            }), encoding="utf-8")
            stub = {"ethnicity_key": "central-asia__testland__test", "people_key": "Q1",
                    "region": "central-asia", "country": "Testland", "ethnicity": "Test",
                    "lat": 10, "lon": 20}
            (data / "unvetted" / "stubs.json").write_text(json.dumps([stub]), encoding="utf-8")
            (data / "unvetted" / "index.json").write_text(json.dumps({stub["ethnicity_key"]: 3}), encoding="utf-8")
            with patch.object(build_index, "REPO_ROOT", root), \
                 patch.object(build_index, "DATA_DIR", data), \
                 patch.object(build_index, "CONTENT_DIR", root / "content"), \
                 patch.object(build_index, "LIBRARY_DIR", root / "library"), \
                 patch.object(build_index, "_HASH_CACHE_PATH", root / "cache" / "hashes.json"):
                build_index.build()
            index = json.loads((data / "index.json").read_text(encoding="utf-8"))
            self.assertEqual(index["all_objects_count"], 0)
            self.assertEqual(index["unvetted_cultures_count"], 1)
            shard = json.loads((data / "ethnicities" / f"{stub['ethnicity_key']}.json").read_text(encoding="utf-8"))
            self.assertTrue(shard["unvetted_only"])
            self.assertEqual(shard["object_count"], 0)
            self.assertEqual(shard["unvetted_count"], 3)
            globe = json.loads((data / "globe.json").read_text(encoding="utf-8"))
            self.assertTrue(next(p for p in globe["points"] if p["key"] == stub["ethnicity_key"])["unvetted_only"])

    def test_gap_report_includes_site_only_countries_and_visible_gaps(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            out = root / "data" / "world"
            ethnicities = root / "data" / "ethnicities"
            out.mkdir(parents=True)
            ethnicities.mkdir(parents=True)
            (out / "wikidata.json").write_text(json.dumps([
                {"qid": "Q1", "label": "Known people", "sitelinks": 25, "country": "Testland"},
                {"qid": "Q2", "label": "Gap people", "sitelinks": 30, "country": "Gapland"},
            ]), encoding="utf-8")
            (out / "classified.json").write_text(json.dumps({
                "Q1": {"people": True, "continent": "Europe", "region": "Europe", "country": "Testland"},
                "Q2": {"people": True, "continent": "Asia", "region": "Central Asia", "country": "Gapland"},
            }), encoding="utf-8")
            (out / "peoples.json").write_text(json.dumps([{"key": "Q1", "evidence": 6}]), encoding="utf-8")
            (ethnicities / "europe__testland__known.json").write_text(json.dumps({
                "region": "europe", "country": "Testland", "unvetted_only": False
            }), encoding="utf-8")
            (ethnicities / "europe__site-only__culture.json").write_text(json.dumps({
                "region": "europe", "country": "Site-only", "unvetted_only": True
            }), encoding="utf-8")
            with patch.object(world_peoples, "REPO", root), patch.object(world_peoples, "OUT", out):
                world_peoples.cmd_gaps()
            report = (root / "docs" / "gaps.md").read_text(encoding="utf-8")
            self.assertIn("Gap people (Q2, 30 sitelinks)", report)
            self.assertIn("Site-only", report)
            self.assertIn("unreviewed-only cultures", report)


if __name__ == "__main__":
    unittest.main()
