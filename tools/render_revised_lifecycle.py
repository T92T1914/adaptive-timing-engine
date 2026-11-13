"""Render the saved three-revision lifecycle on Obscur without running analysis work."""

import argparse
import json
import tempfile
from pathlib import Path

if __package__:
    from .obscur_figure import FACES, TOKENS_PATH, digest, font_files, render
else:
    from obscur_figure import FACES, TOKENS_PATH, digest, font_files, render


ROOT = Path(__file__).resolve().parents[1]
DATA = "tests/fixtures/receipt-complete-demo.json"
PROVENANCE = "tests/fixtures/receipt-provenance.json"
MANIFEST = "docs/revised-lifecycle-figure.json"
INPUTS = (
    DATA,
    PROVENANCE,
    TOKENS_PATH,
    "docs/revised-work.md",
    "tools/obscur_figure.py",
    "tools/render_revised_lifecycle.py",
)
OBSCUR = (
    "docs/revised-lifecycle-obscur.png",
    "docs/revised-lifecycle-obscur.svg",
    "docs/revised-lifecycle-obscur-wide.png",
    "docs/revised-lifecycle-obscur-wide.svg",
)
CLAIR = (
    "docs/revised-lifecycle-clair.png",
    "docs/revised-lifecycle-clair.svg",
)
STAGES = (
    (
        "physical-running",
        "Revision 1 starts",
        "The first input is running.\nIt keeps its physical resource.",
        "plain",
    ),
    (
        "capacity-refused",
        "Revision 2 is refused",
        "The physical executor refuses it.\nIts exact dispatch stays unsubmitted.",
        "dashed",
    ),
    (
        "waiting-superseded",
        "Revision 3 replaces revision 2",
        "Revision 2 is superseded.\nIts unsubmitted work never runs.",
        "dashed",
    ),
    (
        "stale-reconciled",
        "Revision 1 returns stale",
        "Its result stays inspectable.\nThe owner reconciles the return, then retires physical ownership.",
        "plain",
    ),
    (
        "current-adopted",
        "Revision 3 becomes current",
        "It is admitted and completes.\nThe owner adopts its current result.",
        "current",
    ),
)
QUALIFIERS = (
    "Controlled event order, not elapsed time.",
    "Saved public receipt. No workload rerun.",
    "No speedup or deadline claim.",
)


def load_data():
    """Require the saved schedule and its public derivative provenance."""
    receipt = json.loads((ROOT / DATA).read_text(encoding="utf-8"))
    provenance = json.loads((ROOT / PROVENANCE).read_text(encoding="utf-8"))
    source = next(
        item for item in provenance["fixtures"]
        if item["fixture"] == Path(DATA).name
    )
    expected_metrics = {
        "offered": 3, "admitted": 2, "refused": 1, "replaced": 1,
        "physical_started": 2, "physical_completed": 2,
        "physical_values": 10240, "usable": 1, "stale": 1,
    }
    metrics = receipt["metrics"]
    rows, results = receipt["revisions"], receipt["results"]
    if (
        source["kind"] != "sanitized derivative of retained execution receipt"
        or source["fixture_sha256"] != digest(ROOT / DATA, text=True)
        or metrics != expected_metrics
        or any(type(metrics[key]) is not int for key in expected_metrics)
        or receipt["revision"] != 3
        or receipt["current_revision"] != 3
        or len(rows) != 3 or len(results) != 2
        or [row["revision"] for row in rows] != [1, 2, 3]
        or any(type(row["revision"]) is not int for row in rows)
        or [row["job_id"] for row in rows]
        != ["analysis-0001", "analysis-0002", "analysis-0003"]
        or rows[0]["disposition"] != "completed"
        or rows[0]["physical_stale"] is not True
        or rows[0]["adopted_at"] is not None
        or rows[1]["disposition"] != "superseded_before_admission"
        or rows[1]["refusals"] != 1
        or rows[1]["processed"] != 0
        or "admitted_at" in rows[1]
        or rows[2]["disposition"] != "completed"
        or rows[2]["current"] is not True
        or rows[2]["adopted_at"] is None
        or [result["generation"] for result in results] != [1, 3]
        or any(result["status"] != "completed" for result in results)
        or any(result["started_at"] is None for result in results)
        or [result["stale"] for result in results] != [True, False]
        or results[0]["usable"] is not False
        or results[1]["usable"] is not True
        or results[1]["value"] != receipt["current"]
        or receipt["current"]["count"] != 2048
        or receipt["current"]["total"] != 142336
        or receipt["complete"] is not True
        or receipt["closed"] is not True
        or receipt["planner_stopped"] is not True
        or receipt["outstanding"] != 0
        or receipt["reserved_resources"] != []
        or receipt["pending_revision"] is not None
        or receipt["bounds"]["physical_workers"] != 1
        or receipt["bounds"]["physical_capacity"] != 1
    ):
        raise ValueError("The figure requires the retained three-revision receipt")
    expected_order = [
        (job_id, kind)
        for job_id in ("analysis-0001", "analysis-0003")
        for kind in ("submitted", "started", "completed", "reconciled")
    ]
    if [(item["id"], item["kind"]) for item in receipt["observations"]] != expected_order:
        raise ValueError("The saved physical observation order changed")
    return {"receipt": receipt, "provenance": source}


def semantic_record(data):
    receipt, source = data["receipt"], data["provenance"]
    equivalent = [
        "Keep the current answer.",
        "3 revisions. 2 physical completions.",
        *(
            f"{index + 1}. {title}. " + body.replace("\n", " ")
            for index, (_, title, body, _) in enumerate(STAGES)
        ),
        f'Current revision 3: {receipt["current"]["count"]:,} values; '
        f'sum {receipt["current"]["total"]:,}.',
        *QUALIFIERS,
    ]
    return {
        "source_fixture": {
            "path": DATA,
            "sha256_lf": digest(ROOT / DATA, text=True),
            "kind": source["kind"],
            "origin": source["origin"],
            "original_receipt_sha256": source["source_file_sha256"],
            "limits": source["limits"],
        },
        "experiment_rerun": False,
        "time_scale": "Schematic event order only, not elapsed time",
        "metrics": receipt["metrics"],
        "current": {
            "revision": receipt["current_revision"],
            **{key: receipt["current"][key] for key in ("count", "total", "digest")},
        },
        "terminal_cleanup": {
            key: receipt[key] for key in
            ("closed", "planner_stopped", "outstanding", "reserved_resources")
        },
        "ownership_source": "docs/revised-work.md",
        "ownership_scope": (
            "The fixture records final cleanup and physical observation order. "
            "Retention until return and reconciliation is the documented API "
            "contract, not an intermediate resource snapshot or measured delay."
        ),
        "sequence": [
            {"id": key, "title": title, "explanation": body, "mark": mark}
            for key, title, body, mark in STAGES
        ],
        "marks": "A dashed outline marks work that never ran. A raised card with a ring marks the current result.",
        "text_equivalent": "\n\n".join(equivalent),
    }


def spec(data):
    current = data["receipt"]["current"]
    return dict(
        title="Keep the current answer.",
        subtitle="3 revisions, 2 physical completions.",
        stats=[
            (f'{current["count"]:,}', "values in revision 3"),
            (f'{current["total"]:,}', "sum of those values"),
        ],
        cards=[
            dict(id=key, title=title, lines=body.split("\n"), mark=mark)
            for key, title, body, mark in STAGES
        ],
        footer=list(QUALIFIERS),
    )


def check_outputs():
    """Check committed identities using only the standard library."""
    record = json.loads((ROOT / MANIFEST).read_text(encoding="utf-8"))
    if record["evidence"] != semantic_record(load_data()):
        raise ValueError("Lifecycle figure evidence is stale")
    for name in INPUTS:
        if record["inputs_sha256_lf"][name] != digest(ROOT / name, text=True):
            raise ValueError(f"Lifecycle figure input changed: {name}")
    if set(record["outputs_sha256"]) != set(OBSCUR):
        raise ValueError("Both lifecycle PNG and SVG editions are required")
    alternates = record.get("retained_alternates")
    if not alternates or alternates.get("rebuilt") is not False:
        raise ValueError("The retained Clair alternates are not recorded")
    if set(alternates["outputs_sha256"]) != set(CLAIR):
        raise ValueError("The retained Clair alternates are incomplete")
    for group in (record["outputs_sha256"], alternates["outputs_sha256"]):
        for name, checksum in group.items():
            if digest(ROOT / name) != checksum:
                raise ValueError(f"Lifecycle figure output changed: {name}")
    return record


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--font-dir", type=Path, help="Directory of the three official static Inter TTFs")
    parser.add_argument("--check", action="store_true", help="Verify committed files without fonts or rendering")
    args = parser.parse_args()
    if args.check:
        check_outputs()
        print("Both lifecycle editions match the saved public receipt and renderer")
        return
    if args.font_dir is None:
        parser.error("--font-dir is required for authoring; no font download is performed")
    data = load_data()
    files, font_evidence = font_files(args.font_dir)
    previous = json.loads((ROOT / MANIFEST).read_text(encoding="utf-8"))
    alternates = previous.get("retained_alternates") or {
        "outputs_sha256": {name: digest(ROOT / name) for name in CLAIR},
        "rebuilt": False,
        "note": "Recorded bytes of the Clair editions, kept as explicit alternatives. This renderer does not rebuild them.",
    }
    description = json.dumps(semantic_record(data), sort_keys=True)
    with tempfile.TemporaryDirectory() as temporary:
        output = Path(temporary)
        layout = {}
        for edition, wide in (("narrow", False), ("wide", True)):
            stem = "revised-lifecycle-obscur" + ("-wide" if wide else "")
            layout[edition] = render(
                spec(data), wide, files, output, stem, "timing-revised-lifecycle-obscur-v1", description
            )
        import PIL
        import matplotlib

        record = {
            "schema_version": 2,
            "evidence": semantic_record(data),
            "rules": json.loads((ROOT / TOKENS_PATH).read_text(encoding="utf-8"))["basis"],
            "renderer": {
                "matplotlib": matplotlib.__version__,
                "Pillow": PIL.__version__,
                "backend": "Agg",
            },
            "layout": layout,
            "typography": {
                "release": "Inter 4.1",
                "files": font_evidence,
                "painted_faces": list(FACES),
                "png": "Glyphs rasterized from verified Inter files",
                "svg": "Labels outlined from the same files. Full text is in evidence.text_equivalent",
            },
            "inputs_sha256_lf": {name: digest(ROOT / name, text=True) for name in INPUTS},
            "outputs_sha256": {"docs/" + path.name: digest(path) for path in sorted(output.iterdir())},
            "retained_alternates": alternates,
        }
        for path in output.iterdir():
            (ROOT / "docs" / path.name).write_bytes(path.read_bytes())
        (ROOT / MANIFEST).write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8", newline="\n")
    check_outputs()
    print("Rendered both lifecycle editions from the saved receipt with verified Inter files")


if __name__ == "__main__":
    main()
