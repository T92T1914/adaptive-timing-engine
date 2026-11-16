"""The predictions from the measured GPU service time reduce to the known forms and stay in range."""
import importlib.util
import json
from pathlib import Path
import random
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


def load(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "tools" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


predictions = load("gpu_queue_predictions")
model = load("queue_model_study")


class GpuQueuePredictionTests(unittest.TestCase):
    def test_a_constant_service_time_gives_the_fixed_form(self):
        for load in predictions.LOADS:
            got = predictions.predict(load, [3.7] * 50)
            want = model.predict(load, "fixed")
            for key in want:
                self.assertAlmostEqual(got[key], want[key], places=9, msg=key)

    def test_a_large_exponential_sample_gives_the_exponential_form(self):
        rng = random.Random(5)
        sample = [rng.expovariate(1.0) for _ in range(200000)]
        for load in predictions.LOADS:
            got = predictions.predict(load, sample)
            want = model.predict(load, "exponential")
            for key in want:
                self.assertAlmostEqual(got[key], want[key], delta=0.01, msg=key)

    def test_bad_durations_are_refused(self):
        for bad in ([], [1.0, 0.0], [-1.0]):
            with self.assertRaises(ValueError):
                predictions.predict(1.0, bad)

    def test_the_retained_predictions_cover_every_case_and_stay_in_range(self):
        report = json.loads((ROOT / "docs/evidence/gpu-queue-predictions.json").read_text(encoding="utf-8"))
        self.assertEqual(len(report["rows"]), 4 * len(predictions.ARRIVALS) * len(predictions.LOADS))
        for row in report["rows"]:
            p = row["predicted"]
            self.assertLessEqual(p["adopted_share"], p["computed_share"])
            self.assertTrue(0 <= p["stale_gpu_time_share"] < 1)
            self.assertGreater(p["mean_adopted_age"], 0)

    def test_the_script_rewrites_the_retained_file_exactly(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "again.json"
            self.assertEqual(predictions.main(["--output", str(output)]), 0)
            self.assertEqual(output.read_text(encoding="utf-8"),
                             (ROOT / "docs/evidence/gpu-queue-predictions.json").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
