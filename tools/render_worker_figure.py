"""Render the retained worker example on Obscur without starting a worker or experiment."""

import argparse
import json
import tempfile
from pathlib import Path

if __package__:
    from .obscur_figure import FACES, TOKENS_PATH, digest, font_files, render
else:
    from obscur_figure import FACES, TOKENS_PATH, digest, font_files, render

ROOT = Path(__file__).resolve().parents[1]
DATA = "docs/visual-example-data.json"
MANIFEST = "docs/adaptive-timing-figure.json"
INPUTS = (DATA, TOKENS_PATH, "tools/obscur_figure.py", "tools/render_worker_figure.py")
OBSCUR = (
    "docs/adaptive-timing-obscur.png",
    "docs/adaptive-timing-obscur.svg",
    "docs/adaptive-timing-obscur-wide.png",
    "docs/adaptive-timing-obscur-wide.svg",
)
CLAIR = tuple(name.replace("obscur", "clair") for name in OBSCUR)
EVENTS = (
    (
        "start",
        "Start payload 0",
        "Calculation stays active.\nA thread event holds its result.",
        "plain",
    ),
    (
        "replace",
        "Requests 1 to 100 arrive",
        "One waiting slot.\n99 requests replaced.",
        "plain",
    ),
    (
        "reject",
        "Release the first calculation",
        "Payload 0 completes.\nIts obsolete result is rejected.",
        "dashed",
    ),
    (
        "publish",
        "Compute payload 100",
        "Current result kept:\npayload 100, revision 101.",
        "current",
    ),
)
FOOTER = (
    "0 failures. Nothing queued or running.",
    "Event order, not elapsed time.",
    "No speedup or deadline guarantee.",
    "Controlled example, no new workload.",
    "Recorded source: 6e12229.",
)


def load_data():
    data = json.loads((ROOT / DATA).read_text(encoding="utf-8"))
    expected = {
        "generation": 101,
        "submitted": 101,
        "computed": 2,
        "replaced": 99,
        "stale": 1,
        "failures": 0,
        "queued": 0,
        "running": False,
    }
    if (
        data["source_commit"] != "6e12229"
        or data["source"] != "adaptive_timing.experiment.worker_experiment"
        or type(data["request_count"]) is not int
        or data["request_count"] != 101
        or data["computed_payloads"] != [0, 100]
        or any(type(v) is not int for v in data["computed_payloads"])
        or data["stats"] != expected
        or any(type(data["stats"][k]) is not type(v) for k, v in expected.items())
        or data["timeline"]
        != "Schematic event order; horizontal spacing does not represent elapsed time"
    ):
        raise ValueError(
            "The diagram requires the retained worker example and counters"
        )
    return data


def semantic_record(data):
    return {
        "retained_worker_example": data,
        "experiment_rerun": False,
        "sequence": [
            dict(id=key, title=title, explanation=body, mark=mark)
            for key, title, body, mark in EVENTS
        ],
        "marks": "A dashed outline marks the rejected result. A raised card with a ring marks the current result.",
        "spacing": "Event order only, not elapsed time",
        "separate_trace_report_evaluated_revision": None,
    }


def spec(data):
    stats = data["stats"]
    return dict(
        title="Keep the current result.",
        subtitle="One active, one waiting request.",
        stats=[
            (str(data["request_count"]), "requests submitted"),
            (str(stats["computed"]), "calculations executed"),
        ],
        cards=[
            dict(id=key, title=title, lines=body.split("\n"), mark=mark)
            for key, title, body, mark in EVENTS
        ],
        footer=list(FOOTER),
    )


def check_outputs():
    record = json.loads((ROOT / MANIFEST).read_text(encoding="utf-8"))
    if record["evidence"] != semantic_record(load_data()):
        raise ValueError("Figure evidence is stale")
    for name in INPUTS:
        if record["inputs_sha256_lf"][name] != digest(ROOT / name, text=True):
            raise ValueError(f"Figure input changed: {name}")
    if set(record["outputs_sha256"]) != set(OBSCUR):
        raise ValueError("Both PNG and SVG editions are required")
    alternates = record.get("retained_alternates")
    if not alternates or alternates.get("rebuilt") is not False:
        raise ValueError("The retained Clair alternates are not recorded")
    if set(alternates["outputs_sha256"]) != set(CLAIR):
        raise ValueError("The retained Clair alternates are incomplete")
    for group in (record["outputs_sha256"], alternates["outputs_sha256"]):
        for name, checksum in group.items():
            if digest(ROOT / name) != checksum:
                raise ValueError(f"Figure output changed: {name}")
    return record


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--font-dir", type=Path, help="Directory of the three official static Inter TTFs"
    )
    parser.add_argument(
        "--check", action="store_true", help="Verify committed output without rendering"
    )
    args = parser.parse_args()
    if args.check:
        check_outputs()
        print("Both editions match the retained worker evidence and renderer")
        return
    if args.font_dir is None:
        parser.error(
            "--font-dir is required for rendering; no font download is performed"
        )
    data = load_data()
    files, font_evidence = font_files(args.font_dir)
    previous = json.loads((ROOT / MANIFEST).read_text(encoding="utf-8"))
    alternates = previous.get("retained_alternates") or {
        "outputs_sha256": {
            name: digest(ROOT / name) for name in CLAIR
        },
        "rebuilt": False,
        "note": "Recorded bytes of the Clair editions, kept as explicit alternatives. This renderer does not rebuild them.",
    }
    description = json.dumps(semantic_record(data), sort_keys=True)
    with tempfile.TemporaryDirectory() as temp:
        output = Path(temp)
        layout = {}
        for edition, wide in (("narrow", False), ("wide", True)):
            stem = "adaptive-timing-obscur" + ("-wide" if wide else "")
            layout[edition] = render(
                spec(data), wide, files, output, stem, "timing-worker-obscur-v1", description
            )
        import PIL
        import matplotlib

        record = {
            "schema_version": 3,
            "evidence": semantic_record(data),
            "renderer": {
                "matplotlib": matplotlib.__version__,
                "Pillow": PIL.__version__,
                "backend": "Agg",
            },
            "rules": json.loads((ROOT / TOKENS_PATH).read_text(encoding="utf-8"))["basis"],
            "layout": layout,
            "typography": {
                "release": "Inter 4.1",
                "files": font_evidence,
                "painted_faces": list(FACES),
                "png": "Glyphs rasterized from explicit Inter files",
                "svg": "Labels outlined from the same Inter files. Source data accompanies the figure",
            },
            "inputs_sha256_lf": {
                name: digest(ROOT / name, text=True) for name in INPUTS
            },
            "outputs_sha256": {
                "docs/" + p.name: digest(p) for p in sorted(output.iterdir())
            },
            "retained_alternates": alternates,
        }
        for path in output.iterdir():
            (ROOT / "docs" / path.name).write_bytes(path.read_bytes())
        (ROOT / MANIFEST).write_text(
            json.dumps(record, indent=2) + "\n", encoding="utf-8"
        )
    check_outputs()
    print("Rendered both Obscur editions from retained data with verified Inter files")


if __name__ == "__main__":
    main()

