"""Recorded-claim checks for the current revised-analysis receipt profile."""
from __future__ import annotations

import math
import re


MAX_ISSUES = 32
_HEX = re.compile(r"[0-9a-f]{64}\Z")
_ROW_STATES = {
    "planning", "pending_admission", "admitted", "superseded_before_admission",
    "canceled_before_admission", "expired_before_admission", "planning_failed",
    "completed", "failed", "canceled",
}
_RESULT_STATES = {"completed", "failed", "canceled"}
_OBSERVATION_STATES = {
    "submitted", "started", "completed", "failed", "cancel_requested",
    "cancel_confirmed", "reconciled",
}
_METRICS = (
    "offered", "admitted", "refused", "replaced", "physical_started",
    "physical_completed", "physical_values", "usable", "stale",
)
_DISPATCH_FIELDS = ("id", "resource", "actual_start", "finish", "status")
_MODULES = (
    "__init__.py", "model.py", "causal.py", "runtime.py", "worker.py",
    "execution.py", "revised_analysis.py",
)


class _Invalid(Exception):
    def __init__(self, path: str, code: str, message: str,
                 admission: str = "malformed"):
        self.path, self.code, self.message, self.admission = path, code, message, admission


def _object(value, path):
    if type(value) is not dict:
        raise _Invalid(path, "invalid_type", "Expected an object.")
    return value


def _member(obj, key, path):
    if key not in obj:
        raise _Invalid(path + "." + key, "missing_field", "Required field is missing.")
    return obj[key]


def _text(value, path, *, nonempty=False):
    if type(value) is not str:
        raise _Invalid(path, "invalid_type", "Expected a string.")
    if nonempty and not value:
        raise _Invalid(path, "invalid_value", "Expected a nonempty identifier.")
    return value


def _integer(value, path, *, maximum=None, minimum=0):
    if type(value) is not int:
        raise _Invalid(path, "invalid_type", "Expected an integer, excluding booleans.")
    if value < minimum or (maximum is not None and value > maximum):
        raise _Invalid(path, "invalid_value", "Integer is outside the supported range.")
    return value


def _number(value, path, *, minimum=None):
    try:
        finite = type(value) in (int, float) and math.isfinite(value)
    except OverflowError:
        finite = False
    if not finite:
        raise _Invalid(path, "invalid_type", "Expected a finite number, excluding booleans.")
    if minimum is not None and value < minimum:
        raise _Invalid(path, "invalid_value", "Number is outside the supported range.")
    return value


def _boolean(value, path):
    if type(value) is not bool:
        raise _Invalid(path, "invalid_type", "Expected a boolean.")
    return value


def _enum(value, choices, path):
    _text(value, path)
    if value not in choices:
        raise _Invalid(path, "unsupported_state", "State is outside the supported profile.",
                       "unsupported")
    return value


def _array(value, path, maximum):
    if type(value) is not list:
        raise _Invalid(path, "invalid_type", "Expected an array.")
    if len(value) > maximum:
        raise _Invalid(path, "record_limit", "Collection exceeds the supported entity limit.",
                       "unsupported")
    return value


def _digest(value, path):
    _text(value, path)
    if _HEX.fullmatch(value) is None:
        raise _Invalid(path, "invalid_value", "Expected a lowercase SHA-256 hexadecimal string.")


def _field(obj, key, path, validator, **kwargs):
    return validator(_member(obj, key, path), path + "." + key, **kwargs)


def _dispatch(value, path):
    obj = _object(value, path)
    for key in ("id", "resource"):
        _field(obj, key, path, _text, nonempty=True)
    for key in ("actual_start", "finish"):
        _field(obj, key, path, _number, minimum=0)
    _enum(_member(obj, "status", path), {"dispatched", "expired"}, path + ".status")


def _summary_shape(value, path):
    obj = _object(value, path)
    for key in ("count", "total"):
        _field(obj, key, path, _integer)
    _digest(_member(obj, "digest", path), path + ".digest")
    histogram = _array(_member(obj, "histogram", path), path + ".histogram", 512)
    for index, count in enumerate(histogram):
        _integer(count, f"{path}.histogram[{index}]")


def _clock_shape(value, path):
    obj = _object(value, path)
    _field(obj, "name", path, _text)
    for key in ("implementation", "resolution", "monotonic", "adjustable"):
        item = _member(obj, key, path)
        if item is None:
            continue
        if key == "implementation":
            _text(item, path + "." + key)
        elif key == "resolution":
            _number(item, path + "." + key, minimum=0)
        else:
            _boolean(item, path + "." + key)


def _structure(record, root):
    for key in ("mode", "qualification", "timeout_policy"):
        _field(record, key, root, _text)
    if record["mode"] != "bounded real integer analysis":
        raise _Invalid(root + ".mode", "unsupported_profile", "Receipt mode is unsupported.",
                       "unsupported")
    source = _field(record, "source_identity", root, _object)
    sp = root + ".source_identity"
    for key in ("scope", "package_directory"):
        _field(source, key, sp, _text)
    _field(source, "captured_monotonic", sp, _number)
    distribution = _member(source, "distribution_version", sp)
    if distribution is not None:
        _text(distribution, sp + ".distribution_version")
    files = _field(source, "module_files", sp, _object)
    for name in _MODULES:
        fp = sp + ".module_files[" + repr(name) + "]"
        if name not in files:
            raise _Invalid(fp, "missing_field", "Required source identity entry is missing.")
        entry = _object(files[name], fp)
        if ("sha256" in entry) == ("unavailable" in entry):
            raise _Invalid(fp, "invalid_value", "Expected a hash or an unavailable-source entry.")
        if "sha256" in entry:
            _digest(entry["sha256"], fp + ".sha256")
        else:
            _text(entry["unavailable"], fp + ".unavailable")
    clock = _field(record, "clock", root, _object)
    cp = root + ".clock"
    _field(clock, "session_origin", cp, _number, minimum=0)
    _field(clock, "physical_origin", cp, _number)
    for key in ("session_clock", "physical_clock"):
        _clock_shape(_member(clock, key, cp), cp + "." + key)
    for key in ("session_units", "physical_units", "deadline"):
        _field(clock, key, cp, _text)
    for key in ("duration_estimate", "resource_gap"):
        _field(clock, key, cp, _number, minimum=0)
        if clock[key] != 0:
            raise _Invalid(cp + "." + key, "unsupported_profile",
                           "Nonzero estimates are outside this consumer profile.", "unsupported")
    bounds = _field(record, "bounds", root, _object)
    bp = root + ".bounds"
    for key in ("max_values", "max_revisions", "physical_workers", "physical_capacity",
                "unsubmitted_dispatches"):
        _field(bounds, key, bp, _integer, minimum=1)
    if (bounds["max_values"] != 65536 or not 1 <= bounds["max_revisions"] <= 64
            or bounds["physical_workers"] != 1 or bounds["physical_capacity"] not in (1, 2)
            or bounds["unsubmitted_dispatches"] != 1):
        raise _Invalid(bp, "unsupported_profile", "Bounds are outside the supported profile.",
                       "unsupported")
    _field(bounds, "pending_seconds", bp, _number, minimum=0)
    if bounds["pending_seconds"] == 0:
        raise _Invalid(bp + ".pending_seconds", "invalid_value", "Pending interval must be positive.")
    for key in ("revision", "current_revision"):
        _field(record, key, root, _integer, maximum=64)
    for key in ("closed", "planner_stopped", "complete"):
        _field(record, key, root, _boolean)
    _field(record, "outstanding", root, _integer, maximum=2)
    resources = _array(_member(record, "reserved_resources", root), root + ".reserved_resources", 2)
    for index, name in enumerate(resources):
        _text(name, f"{root}.reserved_resources[{index}]", nonempty=True)
    pending = _member(record, "pending_revision", root)
    if pending is not None:
        _integer(pending, root + ".pending_revision", minimum=1, maximum=64)
    reason = _member(record, "stop_reason", root)
    if reason is not None:
        _enum(reason, {"timeout", "no_current_result", "demonstration_error"}, root + ".stop_reason")
    current = _member(record, "current", root)
    if current is not None:
        _summary_shape(current, root + ".current")
    metrics = _field(record, "metrics", root, _object)
    for key in _METRICS:
        _field(metrics, key, root + ".metrics", _integer)
    definitions = _field(record, "metric_definitions", root, _object)
    for key in ("physical_values", "usable", "stale"):
        _field(definitions, key, root + ".metric_definitions", _text)
    planner = _field(record, "planner", root, _object)
    for key in ("generation", "submitted", "computed", "replaced", "stale", "failures", "queued"):
        _field(planner, key, root + ".planner", _integer)
    _field(planner, "running", root + ".planner", _boolean)
    rows = _array(_member(record, "revisions", root), root + ".revisions", 64)
    for index, row in enumerate(rows):
        path = f"{root}.revisions[{index}]"
        _object(row, path)
        _field(row, "revision", path, _integer, minimum=1, maximum=64)
        _field(row, "job_id", path, _text, nonempty=True)
        for key in ("input_count", "refusals", "processed"):
            _field(row, key, path, _integer)
        for key in ("causal_generation", "mailbox_generation"):
            _field(row, key, path, _integer, minimum=1)
        _digest(_member(row, "input_digest", path), path + ".input_digest")
        for key in ("offered_at", "deadline"):
            _field(row, key, path, _number, minimum=0)
        _enum(_member(row, "disposition", path), _ROW_STATES, path + ".disposition")
        for key in ("superseded", "cancel_requested", "current"):
            _field(row, key, path, _boolean)
        adopted = _member(row, "adopted_at", path)
        if adopted is not None:
            _number(adopted, path + ".adopted_at", minimum=0)
        for key in ("planned_at", "disposed_at", "admitted_at", "dispatched_at", "last_refused_at"):
            if key in row:
                _number(row[key], path + "." + key, minimum=0)
        for key in ("error", "adoption_error"):
            if key in row:
                _text(row[key], path + "." + key)
        if "physical_stale" in row:
            _boolean(row["physical_stale"], path + ".physical_stale")
        if "dispatch" in row:
            _dispatch(row["dispatch"], path + ".dispatch")
    results = _array(_member(record, "results", root), root + ".results", 64)
    row_by_generation = {row["revision"]: row for row in rows}
    for index, result in enumerate(results):
        path = f"{root}.results[{index}]"
        _object(result, path)
        _field(result, "generation", path, _integer, minimum=1, maximum=64)
        _dispatch(_member(result, "dispatch", path), path + ".dispatch")
        _enum(_member(result, "status", path), _RESULT_STATES, path + ".status")
        for key in ("submitted_at", "completed_at", "reconciled_at"):
            _field(result, key, path, _number)
        start = _member(result, "started_at", path)
        if start is not None:
            _number(start, path + ".started_at")
        for key in ("cancellation_requested", "stale", "usable"):
            _field(result, key, path, _boolean)
        for key in ("queue_seconds", "callable_seconds"):
            value = _member(result, key, path)
            if value is not None:
                _number(value, path + "." + key)
        _field(result, "reconciliation_seconds", path, _number)
        error = _member(result, "error", path)
        if error is not None:
            _text(error, path + ".error")
        value = _member(result, "value", path)
        row = row_by_generation.get(result["generation"], {})
        if result["status"] == "completed" and type(value) is dict and "adoption_error" not in row:
            if not all(key in value for key in ("count", "total", "histogram", "digest")):
                raise _Invalid(path + ".value", "unsupported_value_profile",
                               "Object outcome is outside the declared numeric value profile.", "unsupported")
            _summary_shape(value, path + ".value")
    observations = _array(_member(record, "observations", root), root + ".observations", 512)
    for index, observation in enumerate(observations):
        path = f"{root}.observations[{index}]"
        _object(observation, path)
        for key in ("id", "resource"):
            _field(observation, key, path, _text, nonempty=True)
        _field(observation, "generation", path, _integer, minimum=1, maximum=64)
        _enum(_member(observation, "kind", path), _OBSERVATION_STATES, path + ".kind")
        _field(observation, "at", path, _number)
        stale = _member(observation, "stale", path)
        if stale is not None:
            _boolean(stale, path + ".stale")


def check_record(record: object, root: str = "$") -> dict:
    """Inspect saved fields only, without replaying work or following provenance."""
    issues = []
    truncated = False

    def issue(code, path, message):
        nonlocal truncated
        if len(issues) < MAX_ISSUES:
            issues.append({"code": code, "path": path, "message": message})
        elif not truncated:
            issues[-1] = {"code": "issue_limit", "path": root,
                          "message": "Further findings exceeded the diagnostic limit."}
            truncated = True

    limits = [
        "Internal consistency does not authenticate execution, input bytes, source or analyzer identity.",
        "Observations are not a complete atomic replay trace; absent history is not reconstructed.",
        "Progress is recorded work, not independently measured computation or total system work.",
        "The closed flag alone does not certify retirement; a closing snapshot may retain owned work.",
        "Unknown bounded annotations and explanatory text are retained without semantic certification.",
    ]

    def verdict(admission="accepted"):
        checked = admission == "accepted"
        return {"admission": admission,
                "consistency": ("contradictory" if issues else "consistent") if checked else "not_checked",
                "completion": ("complete" if record["complete"] else "incomplete") if checked else "unavailable",
                "issues": issues, "not_checked": limits}

    try:
        _object(record, root)
        schema = _field(record, "schema_version", root, _integer)
        if schema != 1:
            raise _Invalid(root + ".schema_version", "unsupported_schema",
                           "Schema version is unsupported.", "unsupported")
        for container, keys in (("source_identity", ("scope", "captured_monotonic")),
                                ("clock", ("session_clock", "physical_clock"))):
            parent = record.get(container)
            if type(parent) is dict:
                for key in keys:
                    if key not in parent:
                        issue("unsupported_profile", root + "." + container + "." + key,
                              "Current-profile metadata is absent.")
        if "timeout_policy" not in record:
            issue("unsupported_profile", root + ".timeout_policy", "Current-profile metadata is absent.")
        if issues:
            return verdict("unsupported")
        _structure(record, root)
    except _Invalid as error:
        issue(error.code, error.path, error.message)
        return verdict(error.admission)

    rows, results = record["revisions"], record["results"]
    by_revision, by_id, result_by_id, result_by_revision = {}, {}, {}, {}
    final = record["revision"]
    if record["current_revision"] != final:
        issue("identity", root + ".current_revision", "Current revision disagrees with the session revision.")
    if final != len(rows) or final > record["bounds"]["max_revisions"]:
        issue("identity", root + ".revision", "Session revision disagrees with its admitted revision rows.")

    def numeric(value, path, row=None):
        histogram = value["histogram"]
        if len(histogram) != 256 or any(count > 65536 for count in histogram):
            issue("summary_shape", path + ".histogram", "Summary requires 256 bounded histogram bins.")
        if value["count"] > 65536 or sum(histogram) != value["count"]:
            issue("summary_count", path + ".count", "Histogram count disagrees with the bounded Summary count.")
        if value["total"] > 16711680 or sum(index * count for index, count in enumerate(histogram)) != value["total"]:
            issue("summary_total", path + ".total", "Histogram weighted total disagrees with the Summary total.")
        if row is not None:
            if value["count"] != row["input_count"]:
                issue("summary_count", path + ".count", "Summary count disagrees with the referenced input count.")
            if value["digest"] != row["input_digest"]:
                issue("digest_mismatch", path + ".digest", "Summary digest disagrees with the captured input digest.")

    previous_offer = None
    for index, row in enumerate(rows):
        path = f"{root}.revisions[{index}]"
        revision, ident = row["revision"], row["job_id"]
        if revision in by_revision or revision != index + 1:
            issue("identity", path + ".revision", "Revision identities must be unique and consecutively ordered.")
        if ident in by_id:
            issue("identity", path + ".job_id", "Job identity is duplicated within revision entities.")
        by_revision.setdefault(revision, row)
        by_id.setdefault(ident, row)
        if row["input_count"] > 65536:
            issue("progress", path + ".input_count", "Recorded input exceeds the supported bound.")
        if row["processed"] > row["input_count"]:
            issue("progress", path + ".processed", "Recorded input or progress exceeds its supported bound.")
        if previous_offer is not None and row["offered_at"] <= previous_offer:
            issue("session_order", path + ".offered_at", "Information deliveries must advance session time.")
        previous_offer = row["offered_at"]
        if (row["deadline"] != row["offered_at"] + record["bounds"]["pending_seconds"]
                or row["deadline"] <= row["offered_at"]):
            issue("cutoff", path + ".deadline", "Submission cutoff disagrees with its recorded pending interval.")
        for key in ("planned_at", "dispatched_at", "admitted_at", "last_refused_at", "adopted_at"):
            if row.get(key) is not None and row[key] < row["offered_at"]:
                issue("session_order", path + "." + key, "Session event precedes the input offer.")
        if "admitted_at" in row and row["admitted_at"] >= row["deadline"]:
            issue("cutoff", path + ".admitted_at", "Physical submission was recorded at or after its cutoff.")
        if row["refusals"] and "last_refused_at" not in row:
            issue("identity", path + ".last_refused_at", "Refusal count has no last-attempt timestamp.")
        if "last_refused_at" in row and row["last_refused_at"] >= row["deadline"]:
            issue("cutoff", path + ".last_refused_at", "Refusal attempt was recorded after admission expired.")
        if "dispatch" in row and row["dispatch"]["id"] != ident:
            issue("identity", path + ".dispatch.id", "Dispatch identity disagrees with its revision.")
        if row["disposition"] == "admitted":
            for key in ("dispatch", "admitted_at"):
                if key not in row:
                    issue("identity", path + "." + key, "Admitted work lacks its physical submission record.")
        elif row["disposition"] not in _RESULT_STATES:
            if "admitted_at" in row:
                issue("identity", path + ".admitted_at", "Unsubmitted disposition retains a physical admission.")
            if row["processed"]:
                issue("progress", path + ".processed", "Unsubmitted work cannot record analysis visits.")
        if row["disposition"] == "canceled_before_admission" and not row["cancel_requested"]:
            issue("cancellation", path + ".cancel_requested", "Canceled unsubmitted work requires an owner cancellation flag.")
        expected_current = revision == final and record["current"] is not None
        if row["current"] != expected_current:
            issue("current_result", path + ".current", "Current flag disagrees with the final snapshot.")

    seen_results = set()
    eligible_current = None
    for index, result in enumerate(results):
        path = f"{root}.results[{index}]"
        generation, dispatch = result["generation"], result["dispatch"]
        ident = dispatch["id"]
        row = by_revision.get(generation)
        if generation in seen_results or ident in result_by_id:
            issue("identity", path + ".generation", "A physical result entity is duplicated.")
        seen_results.add(generation)
        result_by_id.setdefault(ident, result)
        result_by_revision.setdefault(generation, result)
        if row is None:
            issue("identity", path + ".generation", "Result generation has no data-revision entity.")
        else:
            if ident != row["job_id"]:
                issue("identity", path + ".dispatch.id", "Result job identity disagrees with its data revision.")
            if "dispatch" not in row or any(dispatch[key] != row["dispatch"][key] for key in _DISPATCH_FIELDS):
                issue("identity", path + ".dispatch", "Result dispatch disagrees with the saved revision dispatch.")
            if "admitted_at" not in row:
                issue("identity", path + ".generation", "Physical result has no recorded physical admission.")
            if row["disposition"] != result["status"]:
                issue("identity", path + ".status", "Physical status disagrees with the revision disposition.")
            if row.get("physical_stale") != result["stale"]:
                issue("identity", path + ".stale", "Physical stale flag disagrees with the saved revision flag.")
            if result["cancellation_requested"] and not row["cancel_requested"]:
                issue("cancellation", path + ".cancellation_requested", "Physical cancellation request lacks its owner cancellation flag.")
        usable = result["status"] == "completed" and not result["stale"] and not result["cancellation_requested"]
        if result["usable"] != usable:
            issue("usable", path + ".usable", "Usability disagrees with the immutable result fields.")
        if generation == final and result["stale"]:
            issue("identity", path + ".stale", "A final-generation result cannot be stale in this session.")
        start, submitted = result["started_at"], result["submitted_at"]
        completed, reconciled = result["completed_at"], result["reconciled_at"]
        if completed < submitted or (start is not None and completed < start):
            issue("physical_order", path + ".completed_at", "Physical completion precedes submission or start.")
        if reconciled < completed:
            issue("physical_order", path + ".reconciled_at", "Reconciliation precedes physical completion.")
        if start is not None and start < submitted:
            issue("physical_order", path + ".started_at", "Physical start precedes submission.")
        if result["status"] == "completed" and start is None:
            issue("physical_order", path + ".started_at", "Completed physical work requires a recorded start.")
        durations = {
            "queue_seconds": None if start is None else start - submitted,
            "callable_seconds": None if start is None else completed - start,
            "reconciliation_seconds": reconciled - completed,
        }
        for key, expected in durations.items():
            if result[key] != expected:
                issue("duration", path + "." + key, "Duration disagrees with its recorded endpoint difference.")
        status, value = result["status"], result["value"]
        if dispatch["status"] != "dispatched":
            issue("identity", path + ".dispatch.status", "A physical result requires a dispatched virtual record.")
        if status == "canceled" and not result["cancellation_requested"]:
            issue("cancellation", path + ".cancellation_requested", "Confirmed cancellation requires a cancellation request.")
        if (status == "failed" and result["error"] is None
                or status != "failed" and result["error"] is not None
                or status != "completed" and value is not None):
            issue("result_payload", path, "Physical status disagrees with its error or value payload.")
        nonnumeric = row is not None and "adoption_error" in row or type(value) is not dict
        if status == "completed":
            if nonnumeric:
                note = "Non-Summary physical outcomes are retained; numeric Summary validation is not applicable to them."
                if note not in limits:
                    limits.append(note)
                if usable and row is not None and "adoption_error" not in row:
                    issue("current_result", path + ".value", "Non-Summary adoption attempt has no recorded adoption error.")
            else:
                numeric(value, path + ".value", row)
                if generation == final and usable and row is not None and not row["cancel_requested"]:
                    eligible_current = value
        if row is not None and row["adopted_at"] is not None:
            if not usable or nonnumeric:
                issue("current_result", path + ".usable", "Recorded adoption requires an eligible numeric result.")
            next_row = by_revision.get(generation + 1)
            if next_row is not None and row["adopted_at"] >= next_row["offered_at"]:
                issue("session_order", path + ".generation", "Historical adoption did not precede the next input offer.")

    for index, row in enumerate(rows):
        if row["disposition"] in _RESULT_STATES and row["revision"] not in result_by_revision:
            issue("identity", f"{root}.revisions[{index}].disposition",
                  "Terminal physical disposition has no saved result entity.")
        if "adoption_error" in row:
            result = result_by_revision.get(row["revision"])
            eligible = (result is not None and result["status"] == "completed"
                        and not result["stale"] and not result["cancellation_requested"])
            if not eligible or row["adopted_at"] is not None:
                issue("current_result", f"{root}.revisions[{index}].adoption_error",
                      "Adoption error requires an unadopted, historically usable saved result.")

    if record["current"] is not None:
        numeric(record["current"], root + ".current", by_revision.get(final))
        keys = ("count", "total", "histogram", "digest")
        if eligible_current is None or any(record["current"][key] != eligible_current[key] for key in keys):
            issue("current_result", root + ".current", "Current Summary disagrees with an eligible final saved result.")
        final_row = by_revision.get(final)
        if final_row is None or final_row["adopted_at"] is None:
            issue("current_result", root + ".current", "Current Summary has no recorded final adoption.")
    elif eligible_current is not None:
        issue("current_result", root + ".current", "An eligible uncanceled final adoption is missing from the snapshot.")

    observed_previous = None
    for index, observation in enumerate(record["observations"]):
        path = f"{root}.observations[{index}]"
        row = by_id.get(observation["id"])
        if row is None or observation["generation"] != row["revision"]:
            issue("identity", path + ".id", "Observation does not join a recorded data revision and job.")
        elif "dispatch" in row and observation["resource"] != row["dispatch"]["resource"]:
            issue("identity", path + ".resource", "Observation resource disagrees with its dispatch.")
        if observed_previous is not None and observation["at"] < observed_previous:
            issue("physical_order", path + ".at", "Saved observation times are not nondecreasing.")
        observed_previous = observation["at"]
        if observation["kind"] != "reconciled" and observation["stale"] is not None:
            issue("observation", path + ".stale", "Only reconciliation observations carry stale flags.")
        result = result_by_id.get(observation["id"])
        if result is None:
            continue
        endpoint = {"submitted": "submitted_at", "started": "started_at", "reconciled": "reconciled_at"}.get(observation["kind"])
        if observation["kind"] in ("completed", "failed", "cancel_confirmed"):
            endpoint = "completed_at"
            expected_status = {"completed": "completed", "failed": "failed", "cancel_confirmed": "canceled"}[observation["kind"]]
            if result["status"] != expected_status:
                issue("observation", path + ".kind", "Terminal observation disagrees with the saved physical outcome.")
        if endpoint is not None and observation["at"] != result[endpoint]:
            issue("observation", path + ".at", "Observation disagrees with its saved result endpoint.")
        if observation["kind"] == "reconciled" and observation["stale"] != result["stale"]:
            issue("observation", path + ".stale", "Reconciliation observation disagrees with its result stale flag.")

    expected_metrics = {
        "offered": len(rows), "admitted": sum("admitted_at" in row for row in rows),
        "refused": sum(row["refusals"] for row in rows),
        "replaced": sum(row["disposition"] == "superseded_before_admission" for row in rows),
        "physical_started": sum(result["started_at"] is not None for result in results),
        "physical_completed": sum(result["status"] == "completed" for result in results),
        "physical_values": sum(row["processed"] for row in rows),
        "usable": sum(result["status"] == "completed" and not result["stale"] and not result["cancellation_requested"] for result in results),
        "stale": sum(result["stale"] for result in results),
    }
    for key, expected in expected_metrics.items():
        if record["metrics"][key] != expected:
            issue("metric", root + ".metrics." + key, "Metric disagrees with its declared recorded population.")
    pending_rows = [row for row in rows if row["disposition"] == "pending_admission"]
    expected_pending = pending_rows[0]["revision"] if len(pending_rows) == 1 else None
    if len(pending_rows) > 1 or record["pending_revision"] != expected_pending:
        issue("identity", root + ".pending_revision", "Pending identity disagrees with the single retained dispatch slot.")
    if record["outstanding"] != sum(row["disposition"] == "admitted" for row in rows):
        issue("cleanup", root + ".outstanding", "Outstanding count disagrees with unreconciled admitted rows.")
    if record["outstanding"] > record["bounds"]["physical_capacity"]:
        issue("cleanup", root + ".outstanding", "Outstanding count exceeds physical capacity.")
    expected_resources = {row["dispatch"]["resource"] for row in rows
                          if row["disposition"] == "admitted" and "dispatch" in row}
    if (len(record["reserved_resources"]) != len(set(record["reserved_resources"]))
            or set(record["reserved_resources"]) != expected_resources):
        issue("cleanup", root + ".reserved_resources", "Reservations disagree with recorded unreconciled ownership.")
    active = bool(record["outstanding"]) or record["pending_revision"] is not None or any(
        row["disposition"] in {"planning", "pending_admission", "admitted"} for row in rows)
    expected_complete = (record["closed"] and record["planner_stopped"] and not active
                         and record["stop_reason"] is None)
    if record["complete"] != expected_complete:
        issue("completion", root + ".complete", "Complete flag disagrees with the recorded closure formula.")
    if record["complete"]:
        for key in ("closed", "planner_stopped"):
            if not record[key]:
                issue("cleanup", root + "." + key, "Complete claim requires closed and stopped owners.")
        if record["outstanding"]:
            issue("cleanup", root + ".outstanding", "Complete claim retains outstanding physical work.")
        if record["reserved_resources"]:
            issue("cleanup", root + ".reserved_resources", "Complete claim retains a physical reservation.")
        if record["pending_revision"] is not None or any(row["disposition"] == "planning" for row in rows):
            issue("cleanup", root + ".pending_revision", "Complete claim retains unsubmitted work.")
        if record["stop_reason"] is not None:
            issue("cleanup", root + ".stop_reason", "Complete claim conflicts with a recorded stop reason.")
        if final and record["current"] is None:
            issue("current_result", root + ".current", "A nonempty complete session has no current numeric result.")
    if record["planner_stopped"] and (record["planner"]["running"] or record["planner"]["queued"]):
        issue("cleanup", root + ".planner", "Stopped planner claim conflicts with recorded running or queued work.")
    planner = record["planner"]
    if (planner["generation"] != planner["submitted"] or planner["computed"] > planner["submitted"]
            or planner["replaced"] > planner["submitted"] or planner["stale"] > planner["computed"]
            or planner["failures"] > planner["computed"] or planner["queued"] > 1):
        issue("planner", root + ".planner", "Planner counters disagree with the single-mailbox bounds.")
    return verdict()
