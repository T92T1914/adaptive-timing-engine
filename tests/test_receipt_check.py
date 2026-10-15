"""Passive receipt checks use retained claims and independent contradictions."""
from contextlib import redirect_stderr, redirect_stdout
import copy
import hashlib
import io
import json
import os
from pathlib import Path
import socket
import tempfile
import threading
import unittest
from unittest.mock import patch

from adaptive_timing.receipt_check import inspect_bytes, inspect_file, main


FIXTURES = Path(__file__).with_name("fixtures")


def fixture(name="complete-demo"):
    return json.loads((FIXTURES / f"receipt-{name}.json").read_text(encoding="utf-8"))


def encoded(value):
    return json.dumps(value, ensure_ascii=True).encode("utf-8")


class ReceiptCheckTests(unittest.TestCase):
    def inspect(self, value, **kwargs):
        return inspect_bytes(encoded(value), **kwargs)

    def assert_issue(self, verdict, path, code=None):
        matches = [issue for issue in verdict["issues"] if issue["path"] == path]
        self.assertTrue(matches, (path, verdict["issues"]))
        if code is not None:
            self.assertIn(code, [issue["code"] for issue in matches])

    def contradiction(self, mutate, path, code=None):
        value = fixture()
        mutate(value)
        result = self.inspect(value)
        self.assertEqual(result["admission"], "accepted")
        self.assertEqual(result["consistency"], "contradictory")
        self.assert_issue(result, path, code)
        return result

    def test_retained_demonstration_is_consistent_complete_and_unauthenticated(self):
        value = fixture()
        result = self.inspect(value)
        self.assertEqual(result["admission"], "accepted")
        self.assertEqual(result["consistency"], "consistent")
        self.assertEqual(result["completion"], "complete")
        self.assertEqual(result["issues"], [])
        self.assertIs(result["execution_authenticated"], False)
        self.assertTrue(result["not_checked"])
        self.assertEqual(result["receipt_sha256"], hashlib.sha256(encoded(value)).hexdigest())

    def test_historical_usable_adoptions_are_not_forced_to_final_revision(self):
        value = fixture("historical-adoptions")
        historical = [row for row in value["results"]
                      if row["usable"] and row["generation"] != value["revision"]]
        self.assertEqual(len(historical), 15)
        self.assertEqual(value["metrics"]["usable"], 16)
        self.assertEqual(sum(row["current"] for row in value["revisions"]), 1)
        result = self.inspect(value)
        self.assertEqual((result["admission"], result["consistency"], result["completion"]),
                         ("accepted", "consistent", "complete"))

    def test_zero_quantized_physical_intervals_are_supported(self):
        value = fixture("historical-adoptions")
        self.assertTrue(all(row["queue_seconds"] == row["callable_seconds"]
                            == row["reconciliation_seconds"] == 0
                            for row in value["results"]))
        self.assertEqual(self.inspect(value)["consistency"], "consistent")

    def test_shared_job_ids_are_valid_cross_references(self):
        value = fixture()
        for result in value["results"]:
            row = next(row for row in value["revisions"]
                       if row["revision"] == result["generation"])
            self.assertEqual(result["dispatch"]["id"], row["job_id"])
            self.assertIn(row["job_id"], [entry["id"] for entry in value["observations"]])
        self.assertEqual(self.inspect(value)["consistency"], "consistent")

    def test_known_legacy_receipt_is_explicitly_unsupported(self):
        value = fixture("legacy-incomplete")
        self.assertIs(value["complete"], False)
        self.assertEqual(value["stop_reason"], "no_current_result")
        result = self.inspect(value)
        self.assertEqual(result["admission"], "unsupported")
        self.assertEqual(result["consistency"], "not_checked")
        self.assertEqual(result["completion"], "unavailable")
        for path in ("$.source_identity.scope", "$.source_identity.captured_monotonic",
                     "$.clock.session_clock", "$.clock.physical_clock", "$.timeout_policy"):
            self.assert_issue(result, path, "unsupported_profile")

    def test_wrapper_selection_is_explicit_and_preserves_input_hash(self):
        value = fixture()
        for selector in ("session", "consumer"):
            with self.subTest(record=selector):
                raw = encoded({selector: value, "wrapper_metadata": "retained separately"})
                result = inspect_bytes(raw, record=selector)
                self.assertEqual(result["record"], selector)
                self.assertEqual(result["consistency"], "consistent")
                self.assertEqual(result["receipt_sha256"], hashlib.sha256(raw).hexdigest())
                self.assertNotEqual(inspect_bytes(raw)["admission"], "accepted")

    def test_selected_wrapper_issues_keep_the_selected_record_prefix(self):
        value = fixture()
        value["results"][0]["value"]["digest"] = "0" * 64
        result = self.inspect({"session": value}, record="session")
        self.assertEqual(result["consistency"], "contradictory")
        self.assert_issue(result, "$.session.results[0].value.digest", "digest_mismatch")

    def test_missing_or_unknown_selector_is_explicitly_unsupported(self):
        for raw, selector, path in ((encoded(fixture()), "session", "$.session"),
                                    (encoded(fixture()), "arbitrary", "$")):
            with self.subTest(record=selector):
                result = inspect_bytes(raw, record=selector)
                self.assertEqual(result["admission"], "unsupported")
                self.assertEqual(result["consistency"], "not_checked")
                self.assert_issue(result, path)

    def test_valid_hex_digest_mismatch_is_a_recorded_identity_contradiction(self):
        self.contradiction(lambda value: value["results"][0]["value"].update(digest="0" * 64),
                           "$.results[0].value.digest", "digest_mismatch")

    def test_matching_untrusted_digest_changes_do_not_claim_authentication(self):
        value = fixture()
        value["revisions"][0]["input_digest"] = "0" * 64
        value["results"][0]["value"]["digest"] = "0" * 64
        result = self.inspect(value)
        self.assertEqual(result["consistency"], "consistent")
        self.assertIs(result["execution_authenticated"], False)
        self.assertTrue(result["not_checked"])

    def test_histogram_lengths_are_checked_at_the_summary(self):
        for length in (255, 257):
            def mutate(value):
                value["results"][0]["value"]["histogram"] = [32] * length
            with self.subTest(length=length):
                self.contradiction(mutate, "$.results[0].value.histogram", "summary_shape")

    def test_histogram_sum_is_checked_against_count(self):
        self.contradiction(lambda value: value["results"][0]["value"].update(count=8193),
                           "$.results[0].value.count", "summary_count")

    def test_histogram_weighted_sum_is_checked_against_total(self):
        self.contradiction(lambda value: value["results"][0]["value"].update(total=1_044_481),
                           "$.results[0].value.total", "summary_total")

    def test_current_comparison_checks_bins_even_when_count_and_total_match(self):
        def mutate(value):
            histogram = value["current"]["histogram"]
            histogram[6] += 1
            histogram[7] -= 2
            histogram[8] += 1
            self.assertEqual(sum(histogram), 2048)
            self.assertEqual(sum(index * count for index, count in enumerate(histogram)), 142_336)
        self.contradiction(mutate, "$.current", "current_result")

    def test_current_comparison_checks_digest_even_when_numbers_match(self):
        self.contradiction(lambda value: value["current"].update(digest="0" * 64),
                           "$.current", "current_result")

    def test_stale_result_cannot_become_current(self):
        def mutate(value):
            value["current"] = copy.deepcopy(value["results"][0]["value"])
        self.contradiction(mutate, "$.current", "current_result")

    def test_adoption_error_requires_the_rows_own_usable_physical_result(self):
        for row_index in (0, 1):
            with self.subTest(revision=row_index + 1):
                def mutate(value):
                    value["revisions"][row_index]["adoption_error"] = (
                        "numeric analysis requires a Summary result")
                    if row_index == 0:
                        value["results"][0]["value"]["digest"] = "0" * 64
                self.contradiction(mutate, f"$.revisions[{row_index}].adoption_error")

    def test_unknown_result_revision_has_a_precise_identity_issue(self):
        self.contradiction(lambda value: value["results"][0].update(generation=64),
                           "$.results[0].generation", "identity")

    def test_unknown_dispatch_id_has_a_precise_identity_issue(self):
        self.contradiction(lambda value: value["results"][0]["dispatch"].update(id="unknown-job"),
                           "$.results[0].dispatch.id", "identity")

    def test_duplicate_revision_entity_is_not_a_cross_reference(self):
        self.contradiction(lambda value: value["revisions"].append(copy.deepcopy(value["revisions"][0])),
                           "$.revisions[3].revision", "identity")

    def test_duplicate_issued_job_id_is_rejected_in_revision_collection(self):
        self.contradiction(lambda value: value["revisions"][2].update(job_id=value["revisions"][0]["job_id"]),
                           "$.revisions[2].job_id", "identity")

    def test_each_metric_uses_its_own_record_population(self):
        for key in ("offered", "admitted", "refused", "replaced", "physical_started",
                    "physical_completed", "physical_values", "usable", "stale"):
            with self.subTest(metric=key):
                self.contradiction(lambda value: value["metrics"].update({key: value["metrics"][key] + 1}),
                                   "$.metrics." + key, "metric")

    def test_impossible_physical_order_is_not_a_cross_clock_check(self):
        def mutate(value):
            row = value["results"][0]
            row["completed_at"] = row["started_at"] - 1
        self.contradiction(mutate, "$.results[0].completed_at")

    def test_duration_claims_are_derived_from_physical_endpoints(self):
        self.contradiction(lambda value: value["results"][0].update(queue_seconds=1),
                           "$.results[0].queue_seconds")

    def test_complete_claim_cannot_hide_outstanding_work(self):
        self.contradiction(lambda value: value.update(outstanding=1), "$.outstanding")

    def test_terminal_rows_cannot_lose_stale_or_historically_adopted_results(self):
        for name in ("complete-demo", "historical-adoptions"):
            with self.subTest(fixture=name):
                value = fixture(name)
                removed = value["results"].pop(0)
                row_index = next(index for index, row in enumerate(value["revisions"])
                                 if row["revision"] == removed["generation"])
                value["observations"] = [entry for entry in value["observations"]
                                         if entry["id"] != removed["dispatch"]["id"]]
                value["metrics"]["physical_started"] -= int(removed["started_at"] is not None)
                value["metrics"]["physical_completed"] -= 1
                value["metrics"]["usable"] -= int(removed["usable"])
                value["metrics"]["stale"] -= int(removed["stale"])
                result = self.inspect(value)
                self.assertEqual(result["admission"], "accepted")
                self.assertEqual(result["consistency"], "contradictory")
                self.assert_issue(result, f"$.revisions[{row_index}].disposition")

    def test_completed_outcome_requires_a_physical_start(self):
        def mutate(value):
            result = value["results"][0]
            result.update(started_at=None, queue_seconds=None, callable_seconds=None)
            value["metrics"]["physical_started"] -= 1
            value["observations"] = [entry for entry in value["observations"]
                                     if not (entry["id"] == result["dispatch"]["id"]
                                             and entry["kind"] == "started")]
        self.contradiction(mutate, "$.results[0].started_at")

    def test_completion_field_cannot_deny_its_recorded_closed_idle_state(self):
        self.contradiction(lambda value: value.update(complete=False), "$.complete")

    def test_admitted_disposition_requires_dispatch_and_submission_timestamp(self):
        for missing in ("dispatch", "admitted_at"):
            with self.subTest(missing=missing):
                def mutate(value):
                    row = value["revisions"][0]
                    row["disposition"] = "admitted"
                    del row[missing]
                self.contradiction(mutate, "$.revisions[0]." + missing)

    def test_disposed_unadmitted_work_cannot_claim_submission_or_progress(self):
        for field in ("admitted_at", "processed"):
            with self.subTest(field=field):
                def mutate(value):
                    row = value["revisions"][1]
                    if field == "admitted_at":
                        row[field] = row["offered_at"]
                        value["metrics"]["admitted"] += 1
                    else:
                        row[field] = 1
                        value["metrics"]["physical_values"] += 1
                self.contradiction(mutate, "$.revisions[1]." + field)

    def test_integer_pending_interval_uses_its_exact_python_sum(self):
        # This is a synthetic scalar compatibility control, not an execution capture.
        value = fixture()
        pending = 2 ** 53 + 1
        value["bounds"]["pending_seconds"] = pending
        by_revision = {}
        for offered, row in enumerate(value["revisions"]):
            row["offered_at"] = offered
            row["deadline"] = offered + pending
            for key in ("planned_at", "dispatched_at", "admitted_at", "last_refused_at",
                        "disposed_at", "adopted_at"):
                if key in row and row[key] is not None:
                    row[key] = offered
            if "dispatch" in row:
                row["dispatch"].update(actual_start=offered, finish=offered)
            by_revision[row["revision"]] = row
        for result in value["results"]:
            result["dispatch"] = copy.deepcopy(by_revision[result["generation"]]["dispatch"])
        self.assertEqual(value["revisions"][0]["deadline"], 9_007_199_254_740_993)
        result = self.inspect(value)
        self.assertEqual((result["admission"], result["consistency"], result["completion"]),
                         ("accepted", "consistent", "complete"))

    def test_boolean_values_are_not_admitted_as_integers(self):
        for mutate, path in ((lambda value: value.update(revision=True), "$.revision"),
                             (lambda value: value["results"][0]["value"]["histogram"].__setitem__(0, True),
                              "$.results[0].value.histogram[0]"),
                             (lambda value: value["metrics"].update(physical_started=True), "$.metrics.physical_started")):
            with self.subTest(path=path):
                value = fixture()
                mutate(value)
                result = self.inspect(value)
                self.assertEqual(result["admission"], "malformed")
                self.assertEqual(result["consistency"], "not_checked")
                self.assert_issue(result, path, "invalid_type")

    def test_future_schema_is_unsupported(self):
        value = fixture()
        value["schema_version"] = 2
        result = self.inspect(value)
        self.assertEqual(result["admission"], "unsupported")
        self.assertEqual(result["consistency"], "not_checked")
        self.assert_issue(result, "$.schema_version")

    def test_missing_required_data_is_not_treated_as_a_zero_value(self):
        value = fixture()
        del value["metrics"]["physical_values"]
        result = self.inspect(value)
        self.assertEqual(result["admission"], "malformed")
        self.assertEqual(result["consistency"], "not_checked")
        self.assert_issue(result, "$.metrics.physical_values")

    def test_unknown_recorded_state_is_not_fabricated_compatibility(self):
        value = fixture()
        value["revisions"][0]["disposition"] = "future-new-state"
        result = self.inspect(value)
        self.assertEqual(result["admission"], "unsupported")
        self.assertEqual(result["consistency"], "not_checked")
        self.assert_issue(result, "$.revisions[0].disposition", "unsupported_state")

    def test_unavailable_source_hash_is_preserved_without_opening_paths(self):
        value = fixture()
        value["source_identity"]["package_directory"] = "https://example.invalid/do-not-fetch"
        value["source_identity"]["module_files"]["model.py"] = {"unavailable": "FileNotFoundError"}
        with patch.object(Path, "read_bytes", side_effect=AssertionError("record path followed")), \
                patch.object(socket.socket, "connect", side_effect=AssertionError("network used")), \
                patch.object(threading.Thread, "start", side_effect=AssertionError("worker started")):
            result = self.inspect(value)
        self.assertEqual(result["consistency"], "consistent")
        self.assertIs(result["execution_authenticated"], False)
        self.assertTrue(result["not_checked"])

    def test_repeated_inspection_has_no_mutation_or_worker_start(self):
        raw = (FIXTURES / "receipt-complete-demo.json").read_bytes()
        before = hashlib.sha256(raw).hexdigest()
        with patch.object(threading.Thread, "start", side_effect=AssertionError("worker started")):
            first, second = inspect_bytes(raw), inspect_bytes(raw)
        self.assertEqual(first, second)
        self.assertEqual(hashlib.sha256(raw).hexdigest(), before)
        self.assertEqual((FIXTURES / "receipt-complete-demo.json").read_bytes(), raw)

    def test_bad_json_and_unicode_have_controlled_admission_errors(self):
        for raw, code in ((b"\xff", "invalid_utf8"), (b'{"x":', "json_syntax"),
                          (b'{"x":1,"x":2}', "duplicate_key"),
                          (b'{"x":NaN}', "nonfinite_number"),
                          (b'{"x":Infinity}', "nonfinite_number"),
                          (b'{"x":-Infinity}', "nonfinite_number"),
                          (b'{"x":1e999}', "nonfinite_number"),
                          (b'{"x":"\\ud800"}', "invalid_unicode")):
            with self.subTest(code=code, raw=raw):
                result = inspect_bytes(raw)
                self.assertEqual(result["admission"], "malformed")
                self.assertEqual(result["consistency"], "not_checked")
                self.assert_issue(result, "$", code)

    def test_nonbytes_input_has_a_controlled_type_error(self):
        for value in ("{}", bytearray(b"{}"), None):
            with self.subTest(input_type=type(value).__name__):
                result = inspect_bytes(value)
                self.assertEqual(result["admission"], "malformed")
                self.assertEqual(result["consistency"], "not_checked")
                self.assert_issue(result, "$", "input_type")

    def test_declared_parser_resource_limits_admit_exact_and_refuse_one_over(self):
        # The node fixtures contain the root, 200 child arrays and their values.
        exact_nodes = [[0] * 499 for _ in range(199)] + [[0] * 498]
        self.assertEqual(1 + len(exact_nodes) + sum(map(len, exact_nodes)), 100_000)
        over_nodes = exact_nodes[:-1] + [[0] * 499]
        byte_limit = 4 * 1024 * 1024
        examples = {
            "bytes": (b"{}" + b" " * (byte_limit - 2), b"{}" + b" " * (byte_limit - 1)),
            "depth": (b"[" * 32 + b"0" + b"]" * 32,
                      b"[" * 33 + b"0" + b"]" * 33),
            "integer_token": (b'{"x":' + b"1" * 128 + b"}",
                              b'{"x":' + b"1" * 129 + b"}"),
            "string": (encoded({"x": "a" * 16_384}), encoded({"x": "a" * 16_385})),
            "array": (encoded([0] * 512), encoded([0] * 513)),
            "object": (encoded({str(index): 0 for index in range(128)}),
                       encoded({str(index): 0 for index in range(129)})),
            "total_values": (encoded(exact_nodes), encoded(over_nodes)),
        }
        for name, (exact, over) in examples.items():
            with self.subTest(limit=name):
                admitted = inspect_bytes(exact)
                self.assertNotIn("resource_limit", [issue["code"] for issue in admitted["issues"]])
                self.assertNotIn("json_syntax", [issue["code"] for issue in admitted["issues"]])
                result = inspect_bytes(over)
                self.assertEqual(result["admission"], "unsupported")
                self.assertEqual(result["consistency"], "not_checked")
                self.assert_issue(result, "$", "resource_limit")

    def test_entity_counts_are_bounded_separately_from_internal_contradictions(self):
        for field, limit in (("revisions", 64), ("results", 64), ("observations", 512)):
            with self.subTest(collection=field):
                value = fixture()
                population = value[field]
                value[field] = [copy.deepcopy(population[index % len(population)])
                                for index in range(limit)]
                exact = self.inspect(value)
                self.assertEqual(exact["admission"], "accepted")
                self.assertEqual(exact["consistency"], "contradictory")
                value[field].append(copy.deepcopy(population[0]))
                over = self.inspect(value)
                self.assertEqual(over["admission"], "unsupported")
                self.assertEqual(over["consistency"], "not_checked")
                if field == "observations":
                    self.assert_issue(over, "$", "resource_limit")
                else:
                    self.assert_issue(over, "$." + field, "record_limit")

    def test_entire_wrapper_is_admitted_before_record_selection(self):
        receipt = encoded(fixture())
        duplicate = b'{"session":' + receipt + b',"metadata":{"x":1,"x":2}}'
        result = inspect_bytes(duplicate, record="session")
        self.assertEqual(result["admission"], "malformed")
        self.assert_issue(result, "$", "duplicate_key")
        oversized = encoded({"session": fixture(), "metadata": "a" * 16_385})
        result = inspect_bytes(oversized, record="session")
        self.assertEqual(result["admission"], "unsupported")
        self.assert_issue(result, "$", "resource_limit")

    def test_diagnostics_are_bounded_without_false_consistency(self):
        value = fixture("historical-adoptions")
        for row in value["revisions"]:
            row["input_count"] = True
        result = self.inspect(value)
        self.assertLessEqual(len(result["issues"]), 32)
        self.assertNotEqual(result["consistency"], "consistent")

    def test_explicit_file_inspection_preserves_original_bytes(self):
        raw = (FIXTURES / "receipt-complete-demo.json").read_bytes()
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "input.json"
            source.write_bytes(raw)
            result = inspect_file(source)
            self.assertEqual(result["consistency"], "consistent")
            self.assertEqual(result["receipt_sha256"], hashlib.sha256(raw).hexdigest())
            self.assertEqual(source.read_bytes(), raw)

    def test_missing_file_is_unreadable_not_an_execution_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            result = inspect_file(Path(directory) / "missing.json")
        self.assertEqual((result["admission"], result["consistency"], result["completion"]),
                         ("unreadable", "not_checked", "unavailable"))
        self.assert_issue(result, "$", "input_read")

    def test_directory_input_is_unsupported_before_file_read(self):
        with tempfile.TemporaryDirectory() as directory:
            result = inspect_file(directory)
        self.assertEqual(result["admission"], "unsupported")
        self.assertEqual(result["consistency"], "not_checked")
        self.assert_issue(result, "$", "input_file")

    def test_unc_input_is_refused_without_following_network_path(self):
        with patch.object(Path, "stat", side_effect=AssertionError("UNC path inspected")), \
                patch("os.open", side_effect=AssertionError("UNC path opened")):
            result = inspect_file(r"\\example.invalid\share\receipt.json")
        self.assertEqual(result["admission"], "unsupported")
        self.assert_issue(result, "$", "input_file")

    def test_cli_exit_status_and_output_do_not_conflate_inspection_with_execution(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "input.json"
            source.write_bytes(encoded(fixture()))
            output = io.StringIO()
            with redirect_stdout(output), redirect_stderr(io.StringIO()):
                status = main([str(source), "--json"])
            self.assertEqual(status, 0)
            self.assertEqual(json.loads(output.getvalue())["completion"], "complete")
            changed = fixture()
            changed["metrics"]["offered"] += 1
            source.write_bytes(encoded(changed))
            output = io.StringIO()
            with redirect_stdout(output), redirect_stderr(io.StringIO()):
                status = main([str(source), "--json"])
            self.assertEqual(status, 1)
            self.assertEqual(json.loads(output.getvalue())["consistency"], "contradictory")
            source.write_bytes(b'{"truncated":')
            output, errors = io.StringIO(), io.StringIO()
            with redirect_stdout(output), redirect_stderr(errors):
                self.assertEqual(main([str(source), "--json"]), 2)
            self.assertEqual(json.loads(output.getvalue())["admission"], "malformed")
            self.assertNotIn("Traceback", output.getvalue() + errors.getvalue())

    def test_cli_new_output_is_separate_and_input_alias_is_refused(self):
        raw = encoded(fixture())
        with tempfile.TemporaryDirectory() as directory:
            source, target = Path(directory) / "input.json", Path(directory) / "verdict.json"
            source.write_bytes(raw)
            with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                self.assertEqual(main([str(source), "--output", str(target)]), 0)
            saved = json.loads(target.read_text(encoding="utf-8"))
            self.assertEqual(saved["delivery"], "written")
            output = io.StringIO()
            with redirect_stdout(output), redirect_stderr(io.StringIO()):
                status = main([str(source), "--json", "--output", str(source)])
            self.assertEqual(status, 2)
            verdict = json.loads(output.getvalue())
            self.assertEqual(verdict["delivery"], "refused")
            self.assertEqual(verdict["admission"], "accepted")
            self.assertEqual(verdict["consistency"], "consistent")
            self.assertEqual(source.read_bytes(), raw)

    def test_cli_existing_output_and_hard_link_alias_are_not_overwritten(self):
        raw = encoded(fixture())
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "input.json"
            target = Path(directory) / "existing.json"
            source.write_bytes(raw)
            target.write_bytes(b"existing report bytes")
            with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                self.assertEqual(main([str(source), "--output", str(target)]), 2)
            self.assertEqual(target.read_bytes(), b"existing report bytes")
            alias = Path(directory) / "alias.json"
            try:
                os.link(source, alias)
            except OSError as error:
                self.skipTest(f"temporary hard links unavailable: {type(error).__name__}")
            with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                self.assertEqual(main([str(source), "--output", str(alias)]), 2)
            self.assertEqual(source.read_bytes(), raw)
            self.assertEqual(alias.read_bytes(), raw)

    def test_cli_symbolic_link_output_alias_is_not_overwritten(self):
        raw = encoded(fixture())
        with tempfile.TemporaryDirectory() as directory:
            source, alias = Path(directory) / "input.json", Path(directory) / "alias.json"
            source.write_bytes(raw)
            try:
                alias.symlink_to(source)
            except (OSError, NotImplementedError) as error:
                self.skipTest(f"temporary symbolic links unavailable: {type(error).__name__}")
            output = io.StringIO()
            with redirect_stdout(output), redirect_stderr(io.StringIO()):
                self.assertEqual(main([str(source), "--json", "--output", str(alias)]), 2)
            self.assertEqual(json.loads(output.getvalue())["delivery"], "refused")
            self.assertEqual(source.read_bytes(), raw)
            self.assertEqual(alias.read_bytes(), raw)

    def test_fixture_provenance_hashes_and_sanitization_are_explicit(self):
        record = json.loads((FIXTURES / "receipt-provenance.json").read_text(encoding="utf-8"))
        self.assertEqual(len(record["fixtures"]), 3)
        for entry in record["fixtures"]:
            raw = (FIXTURES / entry["fixture"]).read_bytes()
            self.assertEqual(hashlib.sha256(raw).hexdigest(), entry["fixture_sha256"])
            self.assertEqual(json.loads(raw)["source_identity"]["package_directory"],
                             "example-package/adaptive_timing")
            self.assertNotRegex(raw.decode("utf-8"), r"(?i)(?:[a-z]:[\\/]|/users/|/home/)")
            self.assertNotEqual(entry["fixture_sha256"], entry["source_file_sha256"])


if __name__ == "__main__":
    unittest.main()
