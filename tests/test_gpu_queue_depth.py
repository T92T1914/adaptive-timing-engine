"""The depth variant keeps its bound, stops only before a launch and changes no result."""
import ast
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
EXAMPLE = ROOT / "examples" / "gpu_queue_study"
SAVED = ROOT / "docs" / "evidence" / "gpu-depth-chunk-times.json"

spec = importlib.util.spec_from_file_location("gpu_depth", EXAMPLE / "depth.py")
depth = importlib.util.module_from_spec(spec)
spec.loader.exec_module(depth)


class Stream:
    """A stand in for one stream. Work finishes only when something waits for it,
    or `finish_after` launches after its own launch."""

    def __init__(self, finish_after=None):
        self.finish_after = finish_after
        self.launched = 0
        self.finished = 0
        self.most_queued = 0

    def run_chunk(self, chunk, value):
        self.launched += 1
        if self.finish_after is not None:
            self.finished = max(self.finished, self.launched - self.finish_after)
        self.most_queued = max(self.most_queued, self.launched - self.finished)
        return value + [chunk]

    def new_event(self):
        stream, number = self, self.launched

        class Event:
            def query(self):
                return stream.finished >= number

            def synchronize(self):
                stream.finished = max(stream.finished, number)

        return Event()


class DepthOrderTests(unittest.TestCase):
    def run_on(self, stream, count, limit, stale=None):
        return depth.run_depth(list(range(count)), [], limit, stream.run_chunk, stream.new_event, stale)

    def test_every_depth_gives_the_plain_result(self):
        for limit in (1, 2, 4, 8, None):
            for finish_after in (None, 1, 3):
                value, events, _ = self.run_on(Stream(finish_after), 16, limit, lambda: False)
                self.assertEqual(value, list(range(16)), (limit, finish_after))
                self.assertEqual(len(events), 16)

    def test_the_queue_never_exceeds_the_depth(self):
        for limit in (1, 2, 4, 8):
            stream = Stream()
            _, _, waits = self.run_on(stream, 16, limit)
            self.assertEqual(stream.most_queued, limit)
            self.assertEqual(waits, 16 - limit)

    def test_no_depth_queues_all_and_never_waits(self):
        stream = Stream()
        _, _, waits = self.run_on(stream, 16, None)
        self.assertEqual((stream.most_queued, waits, stream.finished), (16, 0, 0))

    def test_finished_work_is_dropped_by_query_without_a_wait(self):
        stream = Stream(finish_after=1)
        _, _, waits = self.run_on(stream, 16, 2)
        self.assertEqual(waits, 0)

    def test_a_stale_pass_launches_nothing_more_and_leaves_its_queue(self):
        for limit in (1, 2, 4, None):
            stream = Stream()
            # The request goes stale once five chunks have been launched.
            value, events, _ = self.run_on(stream, 16, limit, lambda: stream.launched >= 5)
            self.assertEqual((value, len(events)), (list(range(5)), 5), limit)
            queued = 5 - stream.finished
            self.assertEqual(queued, 5 if limit is None else limit - 1, limit)

    def test_a_depth_below_one_is_refused(self):
        with self.assertRaises(ValueError):
            self.run_on(Stream(), 4, 0)

    def test_depth_imports_nothing_from_the_card(self):
        tree = ast.parse((EXAMPLE / "depth.py").read_text(encoding="utf-8"))
        names = {alias.name for node in ast.walk(tree) if isinstance(node, ast.Import) for alias in node.names}
        names |= {node.module for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)}
        self.assertEqual(names, {"collections"})

    def test_nothing_in_the_package_imports_the_depth_files(self):
        for path in (ROOT / "adaptive_timing").glob("*.py"):
            for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
                if isinstance(node, (ast.Import, ast.ImportFrom)):
                    names = [alias.name for alias in node.names] + [getattr(node, "module", None) or ""]
                    for name in names:
                        self.assertNotIn(name.split(".")[0], {"depth", "chunk_depth"}, path.name)


@unittest.skipUnless(importlib.util.find_spec("torch"), "torch is not installed here")
class DepthRunTests(unittest.TestCase):
    def setUp(self):
        import torch
        if not torch.cuda.is_available():
            self.skipTest("no CUDA device")
        sys.path.insert(0, str(EXAMPLE))
        self.addCleanup(sys.path.remove, str(EXAMPLE))

    def test_every_depth_returns_the_same_tensor_as_plain(self):
        import torch
        import chunk_depth
        import model
        blocks, inputs = model.build()
        for size in model.CHUNK_SIZES:
            chunk_list = model.chunks(blocks, size)
            plain = inputs
            for chunk in chunk_list:
                plain = model.run_chunk(chunk, plain)
            torch.cuda.synchronize()
            for limit in chunk_depth.DEPTHS:
                value, events, _ = depth.run_depth(chunk_list, inputs, limit, model.run_chunk,
                                                   chunk_depth.new_event, lambda: False)
                torch.cuda.synchronize()
                self.assertEqual(len(events), len(chunk_list))
                self.assertTrue(torch.equal(value, plain), (size, limit))

    def test_a_quick_chunk_time_run_saves_every_depth(self):
        import chunk_depth
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "chunks.json"
            self.assertEqual(chunk_depth.main(["--quick", "--output", str(output)]), 0)
            report = json.loads(output.read_text(encoding="utf-8"))
        self.assertEqual([row["depth"] for row in report["rows"]], [1, 2, 4, 8, None])
        for row in report["rows"]:
            self.assertEqual(row["summary"], chunk_depth.summarise(row["passes"]))


class SavedChunkTimeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.report = json.loads(SAVED.read_text(encoding="utf-8"))

    def test_the_run_names_its_machine_and_load(self):
        for key in ("revision", "card", "driver", "toolkit", "torch", "started_utc", "finished_utc"):
            self.assertTrue(self.report[key], key)
        for reading in self.report["readings"]:
            for key in ("utilization_memory_clock_before", "utilization_memory_clock_after"):
                self.assertEqual(len(reading[key].split(",")), 3)

    def test_every_chunk_size_holds_every_depth_with_all_its_passes(self):
        settings = self.report["settings"]
        wanted = settings["rounds"] * settings["passes_per_round"]
        found = [(row["chunk_blocks"], row["depth"]) for row in self.report["rows"]]
        self.assertEqual(found, [(size, limit) for size in (1, 2, 4, 8) for limit in (1, 2, 4, 8, None)])
        for row in self.report["rows"]:
            self.assertEqual(len(row["passes"]), wanted)
            for item in row["passes"]:
                self.assertEqual(len(item["chunk_ms"]), row["chunks_per_pass"])
                self.assertTrue(all(value > 0 for value in item["chunk_ms"]))
                if row["depth"] is None:
                    self.assertEqual(item["waits"], 0)
                else:
                    self.assertLessEqual(item["waits"], max(0, row["chunks_per_pass"] - row["depth"]))

    def test_the_summaries_follow_from_the_saved_passes(self):
        for row in self.report["rows"]:
            summary = row["summary"]
            values = [value for item in row["passes"] for value in item["chunk_ms"]]
            self.assertEqual(summary["chunk_count"], len(values))
            self.assertAlmostEqual(summary["chunk_mean_ms"], sum(values) / len(values))
            waits = sum(item["waits"] for item in row["passes"]) / len(row["passes"])
            self.assertAlmostEqual(summary["synchronizes_per_pass"], 1 + waits)


if __name__ == "__main__":
    unittest.main()
