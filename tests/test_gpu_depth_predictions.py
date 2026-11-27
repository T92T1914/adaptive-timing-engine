"""The depth model does what its assumptions say, and the saved predictions are its output."""
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SAVED = ROOT / "docs" / "evidence" / "gpu-depth-predictions.json"

spec = importlib.util.spec_from_file_location("gpu_depth_predictions", ROOT / "tools" / "gpu_depth_predictions.py")
model = importlib.util.module_from_spec(spec)
spec.loader.exec_module(model)


def one(depth, schedule, chunks=8, waits=0):
    saved = [{"chunk_ms": [1.0] * chunks, "waits": waits}]
    return model.simulate(schedule, saved, depth, lambda passes: passes[0])


class DepthModelTests(unittest.TestCase):
    def test_a_stopped_pass_runs_the_queued_chunks_and_no_more(self):
        # The newer request arrives at 2.5. The next check is when a chunk finishes at 3.
        for depth, end in ((1, 3.0), (2, 4.0), (4, 6.0)):
            result = one(depth, [0.0, 2.5])
            self.assertEqual(result["stopped_pass_ms"], end, depth)
            self.assertEqual(result["stale_after_newer_ms_per_stop"], end - 2.5, depth)
            # The second request starts when the first pass ends and completes.
            self.assertEqual(result["mean_adopted_age_ms"], end + 8.0 - 2.5, depth)
            self.assertEqual((result["computed_share"], result["adopted_share"]), (1.0, 0.5))

    def test_a_request_after_the_last_check_leaves_a_complete_stale_pass(self):
        # With depth 4 the last check is when the fourth chunk finishes.
        result = one(4, [0.0, 4.5])
        self.assertEqual(result["stopped_share_of_requests"], 0.0)
        self.assertIsNone(result["stale_after_newer_ms_per_stop"])
        self.assertEqual(result["stale_gpu_time_share"], 0.5)

    def test_queueing_every_chunk_never_stops(self):
        for depth in (None, 8, 16):
            result = one(depth, [0.0, 0.5])
            self.assertEqual(result["stopped_share_of_requests"], 0.0, depth)
            self.assertEqual(result["synchronizes_per_pass"], 1.0)

    def test_only_the_newest_waiting_request_is_computed(self):
        result = one(None, [0.0, 1.0, 2.0, 3.0])
        self.assertEqual((result["computed_share"], result["adopted_share"]), (0.5, 0.25))
        self.assertEqual(result["mean_adopted_age_ms"], 16.0 - 3.0)

    def test_a_stopped_pass_makes_its_share_of_the_waits(self):
        # Depth 2 over 8 chunks has 6 checks past the first two. The third finds the pass
        # stale, and each of the three waited before it asked.
        result = one(2, [0.0, 2.5], waits=6)
        self.assertEqual(result["synchronizes_per_pass"], (1 + 3.0 + 1 + 6) / 2)


class SavedDepthPredictionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.saved = json.loads(SAVED.read_text(encoding="utf-8"))
        cls.source = json.loads(model.SOURCE.read_text(encoding="utf-8"))

    def test_every_depth_arrival_and_load_has_a_prediction(self):
        found = [(r["chunk_blocks"], r["depth"], r["arrival"], r["load"]) for r in self.saved["rows"]]
        expected = [(size, depth, arrival, load) for size in (1, 2, 4, 8) for depth in (1, 2, 4, 8, None)
                    for arrival in ("poisson", "periodic", "bursty") for load in (0.9, 1.5)]
        self.assertEqual(found, expected)
        self.assertEqual(self.saved["chunk_times_revision"], self.source["revision"])

    def assert_close(self, found, saved, where):
        if isinstance(saved, dict):
            self.assertEqual(set(found), set(saved), where)
            for key in saved:
                self.assert_close(found[key], saved[key], f"{where}.{key}")
        elif isinstance(saved, list):
            self.assertEqual(len(found), len(saved), where)
            for number, (one_found, one_saved) in enumerate(zip(found, saved)):
                self.assert_close(one_found, one_saved, f"{where}[{number}]")
        elif isinstance(saved, float):
            self.assertAlmostEqual(found, saved, places=6, msg=where)
        else:
            self.assertEqual(found, saved, where)

    def test_the_script_reproduces_the_whole_saved_report(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "predictions.json"
            self.assertEqual(model.main(["--output", str(output)]), 0)
            again = json.loads(output.read_text(encoding="utf-8"))
        self.assert_close(again, self.saved, "report")


if __name__ == "__main__":
    unittest.main()
