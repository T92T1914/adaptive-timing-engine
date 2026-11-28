"""The README's installed runner claim matches its saved record."""
import json
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
RECORD = "docs/evidence/revision-file-install.json"


class RevisionFileRecordTests(unittest.TestCase):
    def setUp(self):
        self.record = json.loads((ROOT / RECORD).read_text(encoding="utf-8"))

    def test_readme_names_the_recorded_run(self):
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        sentence = next(line for line in readme.splitlines()
                        if "The runner for caller manifests" in line)
        self.assertIn(f"]({RECORD})", sentence)
        self.assertIn(f"Windows CPython {self.record['python']}", sentence)
        self.assertEqual(self.record["recorded_at"][:10], "2026-11-27")
        self.assertIn("November 27, 2026", sentence)

    def test_record_holds_a_complete_installed_run(self):
        record = self.record
        self.assertTrue(record["platform"].startswith("Windows"))
        self.assertEqual(record["implementation"], "CPython")
        self.assertTrue(record["imported_from_new_environment"])
        self.assertTrue(record["receipt"]["complete"])
        current = record["receipt"]["session"]["current"]
        self.assertEqual((current["count"], current["total"]), (4, 260))
        self.assertEqual(record["passive_verdict"]["consistency"], "consistent")
        self.assertEqual(record["exit_codes"], {"complete_run": 0, "passive_check": 0,
                                                "existing_output": 2, "refused_input": 2})
        self.assertEqual(record["receipt"]["session"]["source_identity"]["package_directory"],
                         "Lib/site-packages/adaptive_timing")


if __name__ == "__main__":
    unittest.main()
