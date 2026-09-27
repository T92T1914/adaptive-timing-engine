"""Audit retained decisions and summaries without executing the study again."""
import gzip
import hashlib
import json
from pathlib import Path
import statistics
import tempfile
import unittest
from unittest.mock import patch

from adaptive_timing.presentation import report_hash
from tools.render_causal_report import markdown, render, retained

ROOT = Path(__file__).resolve().parents[1]


class CausalEvidenceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.raw = json.loads(gzip.decompress((ROOT / "docs/causal-evidence/raw.json.gz").read_bytes()))
        cls.summary, cls.archive_hash = retained()

    def test_precommitted_identity_and_declared_matrix(self):
        self.assertEqual(self.raw["evaluated_revision"], "dba8f6fe92e6c7f7d9f5b160fe1f6031285b6a18")
        protocol = (ROOT / "docs/causal-protocol.md").read_text(encoding="utf-8").encode()
        self.assertEqual(hashlib.sha256(protocol).hexdigest(), self.raw["protocol_sha256_lf"])
        self.assertEqual(len(self.raw["streams"]), 8)
        self.assertEqual(len(self.raw["cases"]), 48)
        self.assertEqual(len(self.raw["pairs"]), 24)
        self.assertEqual(len({(row["scenario"], row["pattern"], row["seed"], row["mode"])
                              for row in self.raw["cases"]}), 48)

    def test_all_causal_decisions_use_only_delivered_event_versions(self):
        for case in self.raw["cases"]:
            if case["mode"] != "causal":
                continue
            messages = self.raw["streams"][case["scenario"] + "/" + case["pattern"]]
            for decision in case["plans"]:
                available = {}
                for message in messages:
                    if message["available_at"] > decision["at"]:
                        break
                    if message["kind"] == "cancel":
                        available.pop(message["id"], None)
                    else:
                        available[message["id"]] = message["event"]
                plan = decision["plan"]
                for row in plan["scheduled"] + plan["rejected"]:
                    self.assertEqual(row["event"], available[row["event"]["id"]])
                for row in plan["scheduled"]:
                    self.assertGreaterEqual(row["start"], decision["at"])

    def test_accounting_offsets_and_resource_occupancy_from_raw_records(self):
        for case in self.raw["cases"]:
            with self.subTest(case=(case["scenario"], case["pattern"], case["seed"], case["mode"])):
                counts = {status: list(case["terminal"].values()).count(status)
                          for status in ("rejected", "expired", "dispatched", "canceled", "unknown")}
                self.assertEqual(sum(counts.values()), 120)
                for status, count in counts.items():
                    self.assertEqual(case[status], count)
                self.assertIsNone(case["completed"])
                planned = [row for decision in case["plans"] for row in decision["plan"]["scheduled"]]
                self.assertEqual(len(planned), case["planned"])
                self.assertEqual(len({row["event"]["id"] for row in planned}), case["admitted"])
                self.assertEqual(case["event_evaluations"], sum(len(d["plan"]["scheduled"])
                    + len(d["plan"]["rejected"]) for d in case["plans"]))
                dispatched = [row for row in case["execution"] if row["status"] == "dispatched"]
                self.assertEqual(len(dispatched), case["dispatched"])
                self.assertEqual(len({row["id"] for row in case["execution"]}), len(case["execution"]))
                busy, resource_ready = 0.0, {}
                offsets = {}
                for record in dispatched:
                    decision = next(d for d in reversed(case["plans"]) if d["at"] <= record["actual_start"])
                    event = next(row["event"] for row in decision["plan"]["scheduled"] if row["event"]["id"] == record["id"])
                    self.assertGreaterEqual(record["actual_start"], event["earliest"])
                    self.assertLessEqual(record["finish"], event["deadline"] + 1e-12)
                    self.assertAlmostEqual(record["finish"] - record["actual_start"], event["duration"])
                    self.assertGreaterEqual(record["actual_start"] + 1e-12, resource_ready.get(record["resource"], 0))
                    resource_ready[record["resource"]] = record["finish"] + .002
                    busy += event["duration"]
                    offsets[record["id"]] = 1000 * abs(record["actual_start"] - event["target"])
                self.assertEqual(offsets, case["offsets_by_id_ms"])
                self.assertAlmostEqual(busy, case["busy_seconds"])
                self.assertEqual(case["modeled_service_finished"], sum(row["finish"] <= case["horizon_seconds"] for row in dispatched))

    def test_pair_comparisons_match_individual_cases(self):
        cases = {(row["scenario"], row["pattern"], row["seed"], row["mode"]): row for row in self.raw["cases"]}
        for pair in self.raw["pairs"]:
            key = (pair["scenario"], pair["pattern"], pair["seed"])
            causal, offline = cases[key + ("causal",)], cases[key + ("offline",)]
            for field in ("admitted", "dispatched", "expired", "rejected", "canceled", "unknown",
                          "busy_seconds", "event_evaluations"):
                self.assertEqual(pair[field + "_difference"], causal[field] - offline[field])
            shared = set(causal["offsets_by_id_ms"]) & set(offline["offsets_by_id_ms"])
            self.assertEqual(len(shared), pair["shared_dispatched"])
            expected = statistics.fmean(causal["offsets_by_id_ms"][key] - offline["offsets_by_id_ms"][key]
                                        for key in shared) if shared else None
            if expected is None:
                self.assertIsNone(pair["shared_mean_offset_difference_ms"])
            else:
                self.assertAlmostEqual(expected, pair["shared_mean_offset_difference_ms"])

    def test_report_renders_retained_data_without_experiment(self):
        with tempfile.TemporaryDirectory() as temp, patch('tools.run_causal_study.run', side_effect=AssertionError("No repeat study")):
            summary, identity = render(Path(temp))
            document = (Path(temp) / "causal.html").read_text(encoding="utf-8")
            embedded = document.split('<script type="application/json" id="causal-data">')[1].split('</script>')[0]
            self.assertEqual(json.loads(embedded), self.summary)
            self.assertEqual(identity["summary_sha256"], report_hash(summary))
            self.assertEqual(document.count('<tbody>'), 3)
            self.assertEqual(document.count('<tr>'), 123)
            self.assertFalse(identity["experiment_rerun"])
            self.assertEqual(identity["evaluated_revision"], self.raw["evaluated_revision"])
            self.assertEqual(identity["theme_source"]["revision"], "7a57fe750ff50205a17e1d342106a0d3f2777159")
        markdown_text = markdown(self.summary, self.archive_hash)
        self.assertEqual((ROOT / "docs/causal-evidence/results.md").read_text(encoding="utf-8"), markdown_text)
        self.assertIn("fewer tasks in 9", markdown_text)
        self.assertIn("Completed remains null", markdown_text)


if __name__ == "__main__":
    unittest.main()
