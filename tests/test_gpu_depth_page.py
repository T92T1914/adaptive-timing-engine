"""docs/gpu-queue-depth.md holds what the tools compute from the saved runs."""
import importlib.util
from pathlib import Path
import statistics
import unittest

ROOT = Path(__file__).resolve().parents[1]


def tool(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "tools" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


page_tool = tool("gpu_depth_page")
figure = tool("render_depth_figure")
PAGE = (ROOT / "docs" / "gpu-queue-depth.md").read_text(encoding="utf-8")


class ReplayTests(unittest.TestCase):
    def test_stretched_passes_keep_their_shape_and_reach_the_target_mean(self):
        row = {"depth": 1, "passes": [{"chunk_ms": [1.0, 1.0], "waits": 1}, {"chunk_ms": [1.0, 3.0], "waits": 0}]}
        again = page_tool.stretched(row, 6.0)
        self.assertEqual(again["passes"], [{"chunk_ms": [2.0, 2.0], "waits": 1}, {"chunk_ms": [2.0, 6.0], "waits": 0}])
        self.assertEqual(row["passes"][0]["chunk_ms"], [1.0, 1.0])

    def test_marks_follow_their_rules(self):
        data = page_tool.inputs()
        replayed = page_tool.replay(data)
        rows = page_tool.marked_rows(data, replayed)
        measured = {page_tool.key_of(row): row["measured"] for row in data["runs"]["rows"]}
        for row in rows:
            for name, result in row["metrics"].items():
                self.assertEqual(bool(result["mark"]), result["miss"])
                if result["mark"] == "A":
                    self.assertIn(name, page_tool.STOP_METRICS)
                    self.assertTrue(row["depth"] is None or row["depth"] >= row["chunks_per_pass"])
                    self.assertIn(result["predicted"], (0, None))
                    self.assertGreater(measured[page_tool.key_of(row)]["stopped_share_of_requests"]["mean"], 0)
                self.assertIn(result["mark"], ("", "A", "?"))
                again = page_tool.runs_tool.compare(replayed[page_tool.key_of(row)][name],
                                                    measured[page_tool.key_of(row)][name])
                self.assertEqual(again["miss"], not result["replay_holds"])

    def test_a_marked_table_changes_nothing_but_the_marks(self):
        data = page_tool.inputs()
        rows = page_tool.marked_rows(data, page_tool.replay(data))
        for columns, delay in ((page_tool.runs_tool.STOP_COLUMNS, False),
                               (page_tool.runs_tool.ADOPTION_COLUMNS, True)):
            plain = page_tool.runs_tool.table(rows, columns, delay).split("\n")
            marked = page_tool.comparison_table(rows, columns, delay).split("\n")
            self.assertEqual(len(plain), len(marked))
            for before, after in zip(plain, marked):
                for mark in (" * A", " * ?"):
                    after = after.replace(mark, " *")
                self.assertEqual(after, before)
            self.assertEqual(sum(line.count(" * ") for line in marked),
                             sum(row["metrics"][name]["miss"] for row in rows for name, _, _ in columns))


class PageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.data = page_tool.inputs()
        cls.tables = page_tool.tables()
        cls.replayed = page_tool.replay(cls.data)
        cls.rows = page_tool.marked_rows(cls.data, cls.replayed)
        cls.trade = page_tool.trade(cls.data)

    def says(self, text):
        self.assertIn(text, PAGE)

    def test_every_table_is_the_tool_output(self):
        for name, table in self.tables.items():
            self.assertIn(f"\n\n{table}\n\n", PAGE, name)

    def test_the_miss_counts_in_the_text(self):
        results = [result for row in self.rows for result in row["metrics"].values()]
        count = {mark: sum(r["mark"] == mark for r in results) for mark in ("A", "?")}
        reached = sum(map(page_tool.replay_reaches, results))
        self.assertEqual(count["A"] + count["?"], sum(r["miss"] for r in results))
        self.says(f"miss in {sum(r['miss'] for r in results)} of {len(results)} cells by more than 25 percent")
        self.says(f"For {count['A']} of them, three stop metrics in each of {count['A'] // 3} conditions")
        self.says(f"brings {reached} of the other misses within 25 percent")
        self.says(f"In all, {count['?']} misses are not explained, the {reached} among them.")
        self.says(f"{count['?']} misses carry no reason.")

        def of(name, mark=None):
            return sum(r["metrics"][name]["miss"] if mark is None else r["metrics"][name]["mark"] == mark
                       for r in self.rows)

        def within(name):
            return sum(page_tool.replay_reaches(r["metrics"][name]) for r in self.rows)

        largest = sorted(page_tool.METRICS, key=lambda name: of(name, "?"), reverse=True)[:2]
        self.assertEqual(largest, ["mean_adopted_age_ms", "stale_after_newer_ms_per_stop"])
        self.says(f"the adopted age, with {of(largest[0], '?')}, and the time after the newer request, "
                  f"with {of(largest[1], '?')}.")
        self.says(f"in all {of('mean_adopted_age_ms')} conditions where it misses")
        self.says(f"the replay brings {within('mean_adopted_age_ms')} of the {of('mean_adopted_age_ms')}")
        self.says(f"moves within 25 percent in {within('stale_gpu_time_share')} of its "
                  f"{of('stale_gpu_time_share')} misses")

        def sides(replay_missed_only=False):
            found = [r["metrics"]["synchronizes_per_pass"] for r in self.rows]
            found = [r for r in found if r["miss"] and not (replay_missed_only and r["replay_holds"])]
            above = sum(r["measured"] > r["predicted"] for r in found)
            return len(found) - above, above

        below, above = sides()
        left_below, left_above = sides(replay_missed_only=True)
        self.says(f"misses in {below + above} conditions, below the prediction in {below} and above it in {above}, "
                  f"and the replay brings {within('synchronizes_per_pass')} within 25 percent. Of the "
                  f"{left_below + left_above} it does not, {left_below} are below and {left_above} above.")
        tail = "stale_after_newer_ms_per_stop"
        both = [r["metrics"][tail] for r in self.rows
                if r["metrics"][tail]["miss"] and r["metrics"][tail]["relative"] is not None]
        self.says(f"above the prediction in {sum(r['measured'] > r['predicted'] for r in both)} of the "
                  f"{len(both)} misses where both sides have a value")
        self.says(f"All {of(tail, '?')} misses of that metric outside `A` are not explained. The replay with "
                  f"longer passes brings {within(tail)} of them within 25 percent.")
        age = [r["metrics"]["mean_adopted_age_ms"] for r in self.rows if r["metrics"]["mean_adopted_age_ms"]["miss"]]
        self.assertTrue(all(r["measured"] > r["predicted"] for r in age))

    def test_the_trade_between_depths(self):
        by = {(entry["chunk_blocks"], entry["depth"]): entry for entry in self.trade}

        def median(size, depth, name):
            return by[(size, depth)][f"{name}_measured"][0]

        for size in (1, 2, 4, 8):
            waits = [median(size, depth, "synchronizes_per_pass") for depth in page_tool.DEPTHS]
            self.assertEqual(waits, sorted(waits, reverse=True))
            tail = [median(size, depth, "stale_after_newer_ms_per_stop") for depth in page_tool.DEPTHS]
            self.assertGreater(tail[1], tail[0])
            self.assertGreater(tail[-1], tail[0])
            if size > 1:
                self.assertNotEqual(tail[1:], sorted(tail[1:]))
        self.says(f"the median goes from {median(1, 1, 'synchronizes_per_pass'):.2f} at depth 1 to "
                  f"{median(1, None, 'synchronizes_per_pass'):.2f} with every chunk queued")
        for depth, ending in ((1, "ms at depth 1 for chunk sizes 1, 2, 4 and 8"), (None, "ms with every chunk queued")):
            values = [f"{median(size, depth, 'stale_after_newer_ms_per_stop'):.3f}" for size in (1, 2, 4, 8)]
            self.says(f"{values[0]}, {values[1]}, {values[2]} and {values[3]} {ending}")

    def test_passes_inside_the_worker_were_longer(self):
        for entry in self.trade:
            self.assertGreater(entry["completed_pass_ms"][0], entry["back_to_back_ms"])
        above = sum(entry["above_host"] for entry in self.trade)
        self.says(f"longer than that card time in all {6 * len(self.trade)} conditions, and longer than the back "
                  f"to back pass on the host in {above} of them")
        shares = [entry["stale_gpu_time_share_measured"][0] for entry in self.trade]
        self.says(f"its medians stay between {min(shares):.3f} and {max(shares):.3f}")

    def test_a_common_pass_length_does_at_least_as_well(self):
        entries = page_tool.controls(self.data, self.rows)
        own = entries[0][1]
        self.assertEqual(own["within"], sum(page_tool.replay_reaches(result) for row in self.rows
                                            for result in row["metrics"].values()))
        for title, counts in entries[1:]:
            self.assertGreater(counts["within"], own["within"], title)
            self.assertLess(counts["replay_missed"], own["replay_missed"], title)
        self.says("So the replay is an observation and gives no miss a reason.")
        self.says("Why the passes inside the worker were longer is not yet explained.")
        for gone in ("`L`", " * L", "candidate reason"):
            self.assertNotIn(gone, PAGE)

    def test_the_within_noise_cells_of_the_first_study(self):
        arrivals = page_tool.load("gpu-arrival-runs.json")
        within = [(row["arrival"], row["load"], name) for row in arrivals["rows"]
                  for name in ("computed_share", "mean_adopted_age", "stale_gpu_time_share")
                  if row[name]["verdict"] == "within noise"]
        self.assertEqual(len(within), 9)
        covered = [item for item in within if item[0] in ("poisson", "bursty") and item[1] in (0.9, 1.5)]
        self.assertEqual(covered, [("bursty", 0.9, "mean_adopted_age")] * 3)
        self.says("three of them are bursty adopted ages at load 0.9")
        revisions = page_tool.facts()["revisions"]
        for value in revisions.values():
            self.says(f"`{value[:7]}`")
        self.says(f"as few as {min(entry['fewest_completed'] for entry in self.trade)}, so their median")

    def test_the_periodic_collapse(self):
        found = [(row, row["metrics"]["adopted_share"]) for row in self.rows
                 if row["arrival"] == "periodic" and row["load"] == 0.9
                 and row["metrics"]["adopted_share"]["predicted"] >= 0.53]
        predicted = [result["predicted"] for _, result in found]
        measured = [result["measured"] for _, result in found]
        again = [(row, self.replayed[page_tool.key_of(row)]["adopted_share"]["mean"]) for row, _ in found]
        low = [value for _, value in again if value < 0.07]
        high = [(row, value) for row, value in again if value >= 0.07]
        self.says(f"adopted shares from {min(predicted):.2f} to {max(predicted):.2f} in {len(found)} of those "
                  f"conditions, and the runs gave {min(measured):.3f} to {max(measured):.3f}")
        self.says(f"below 0.07 in {len(low)} of the {len(found)}")
        self.says(f"the replay stays between {min(v for _, v in high):.3f} and {max(v for _, v in high):.3f}")
        self.assertEqual({(row["chunk_blocks"], row["depth"]) for row, _ in high}, {(1, 2), (2, 2), (4, 1)})
        self.assertEqual(len(high), 3)
        samples = {page_tool.key_of(item): item for item in self.data["samples"]["conditions"]}
        units = self.data["predictions"]["settings"]["time_unit_ms"]
        for row, _ in high:
            spacing = units[str(row["chunk_blocks"])] / 0.9
            self.assertLessEqual(
                statistics.median(page_tool.completed_passes_ms(samples[page_tool.key_of(row)])), spacing)
        # Most of the low ones are still misses, as the text says.
        self.assertGreater(sum(not result["replay_holds"] for _, result in found), len(found) / 2)

    def test_late_stops_and_run_size(self):
        late = page_tool.late_stops(self.data)
        self.says(f"All {late['conditions']} such conditions stopped passes, {late['stops']:,} of them.")
        self.says(f"a median of {late['median_ms']:.3f} ms after the pass started, between "
                  f"{late['low_median_ms']:.3f} and {late['high_median_ms']:.3f} ms across the conditions and as "
                  f"late as {late['max_ms']:.3f} ms")
        runs = self.data["runs"]
        self.says(f"It holds {sum(row['starts'] for row in runs['rows']):,} calculations that started")
        self.says(f"the run at `{runs['revision'][:7]}`")
        self.says(f"committed in `{runs['predictions_commit'][:7]}`")
        mismatch = sum(run["worker"]["adopted_check_mismatch"] for row in runs["rows"] for run in row["runs"])
        self.assertLessEqual(max(run["worker"]["adopted_check_mismatch"] for row in runs["rows"]
                                 for run in row["runs"]), 0)
        self.says(f"saw {-mismatch} of the same kind")
        units = [f"{runs['settings']['time_unit_ms'][size]:.3f}" for size in ("1", "2", "4", "8")]
        self.says(f"{units[0]}, {units[1]}, {units[2]} and {units[3]} ms for chunk sizes 1, 2, 4 and 8")

    def test_start_delay_ranges(self):
        rows = self.data["runs"]["rows"]
        ranges = [(min(r["start_delay"][q] for r in rows), max(r["start_delay"][q] for r in rows))
                  for q in ("p50_ms", "p99_ms", "p99_9_ms")]
        (a, b), (c, d), (e, f) = ranges
        self.says(f"its median was between {a:.3f} and {b:.3f} ms, its 99th percentile between {c:.3f} and "
                  f"{d:.3f} ms and its 99.9th percentile between {e:.3f} and {f:.3f} ms")
        starts = [r["starts"] for r in rows]
        self.assertLess(max(starts), 10_000)
        self.says(f"between {min(starts)} and {max(starts)} starts, fewer than 10,000")

    def test_the_first_study_sizing_and_pass_timing(self):
        entries = page_tool.first_study_sizing()
        self.assertEqual(len(entries), 8)

        def gaps(name, side):
            return [100 * abs(e["metrics"][name][side] - e["metrics"][name][2]) / e["metrics"][name][2]
                    for e in entries]

        def span(values):
            return f"{min(values):.0f} to {max(values):.0f} percent"

        names = ("computed_share", "mean_adopted_age", "stale_gpu_time_share")
        before = [span(gaps(name, 0)).removesuffix(" percent") for name in names]
        self.says(f"was {before[0]} percent from the measured computed share, {before[1]} percent from the adopted "
                  f"age and {before[2]} percent from the stale share")
        after = [span(gaps(name, 1)) for name in names]
        self.says(f"the gaps are {after[0]}, {after[1]} and {after[2]}.")
        timing = page_tool.load("gpu-pass-timing.json")["rows"]
        plain = {(r["chunk_blocks"], r["arrival"], r["load"]): r["completed_pass"]["p50_ms"]
                 for r in timing if r["mode"] == "plain"}
        stop = {(r["chunk_blocks"], r["arrival"], r["load"]): r["completed_pass"]["p50_ms"]
                for r in timing if r["mode"] == "stop"}
        poisson = [value for key, value in plain.items() if key[1] == "poisson"]
        self.says(f"between {min(poisson):.3f} and {max(poisson):.3f} ms")
        bursty = {key: value for key, value in plain.items() if key[1] == "bursty"}
        self.says(f"medians of {min(bursty.values()):.3f} to {max(bursty.values()):.3f} ms")
        self.assertEqual(sum(stop[key] > value for key, value in bursty.items()), 8)
        self.says(f"from {plain[(1, 'poisson', 0.9)]:.3f} to {plain[(1, 'poisson', 1.5)]:.3f} ms at 1 block")
        for size in (2, 4, 8):
            self.assertLess(abs(plain[(size, "poisson", 0.9)] - plain[(size, "poisson", 1.5)]), 0.01)

    def test_the_load_readings_and_the_environment(self):
        readings = page_tool.reading_ranges()
        for name, title in (("gpu-depth-chunk-times.json", "Chunk times"), ("gpu-depth-runs.json", "Depth runs"),
                            ("gpu-pass-timing.json", "Pass timing")):
            item = readings[name]
            low, high = item["memory"]
            memory = f"{low:.0f}" if low == high else f"{low:.0f} to {high:.0f}"
            self.says(f"| {title} | {item['readings']} | {item['utilization'][0]:.0f} to "
                      f"{item['utilization'][1]:.0f} | {memory} |")
        runs = self.data["runs"]
        for value in (runs["card"], runs["driver"], runs["toolkit"], runs["torch"]):
            self.says(value)

    def test_the_page_keeps_its_limits_and_cites_the_stream_page(self):
        self.says("https://docs.nvidia.com/cuda/cuda-runtime-api/group__CUDART__STREAM.html")
        self.says("lists no call that cancels or clears work already placed on a stream")
        self.says("do not preempt already-running work")
        self.says("arrivals generated by a script")
        self.says("No result here is a latency bound")

    def test_the_readme_adds_two_sentences_with_the_page_counts(self):
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        results = [result for row in self.rows for result in row["metrics"].values()]
        added = readme[readme.index("A [queue depth study](docs/gpu-queue-depth.md)"):].split("\n")[0]
        self.assertEqual(added.count(". ") + added.endswith("."), 2)
        self.assertIn(f"{sum(r['miss'] for r in results)} of {len(results)} cells miss", added)


class FigureTests(unittest.TestCase):
    def test_the_committed_figure_matches_its_sources(self):
        figure.check_outputs()

    def test_the_figure_values_are_the_trade_table_values(self):
        trade = {(e["chunk_blocks"], "all" if e["depth"] is None else e["depth"]): e
                 for e in page_tool.trade(page_tool.inputs())}
        for panel in figure.load_data():
            for point in panel["points"]:
                entry = trade[(panel["chunk_blocks"], point["depth"])]
                for side in ("measured", "predicted"):
                    expected = entry[f"stale_after_newer_ms_per_stop_{side}"]
                    found = point[side]
                    self.assertEqual(expected, None if found is None else
                                     (found["median"], found["low"], found["high"], found["conditions"]))


if __name__ == "__main__":
    unittest.main()
