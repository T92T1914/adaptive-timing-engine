"""Render the saved three-revision lifecycle without running analysis work."""

import argparse
import hashlib
import json
import tempfile
from pathlib import Path

if __package__:
    from .render_worker_figure import font_files
else:
    from render_worker_figure import font_files


ROOT = Path(__file__).resolve().parents[1]
DATA = "tests/fixtures/receipt-complete-demo.json"
PROVENANCE = "tests/fixtures/receipt-provenance.json"
MANIFEST = "docs/revised-lifecycle-figure.json"
INPUTS = (
    DATA,
    PROVENANCE,
    "adaptive_timing/presentation.json",
    "docs/revised-work.md",
    "tools/render_worker_figure.py",
    "tools/render_revised_lifecycle.py",
)
WIDTH_POINTS = 547.2
HEIGHT_POINTS = 1584
MINIMUM_POINTS = 24
STAGES = (
    (
        "physical-running",
        "1  Revision 1 starts",
        "The first input is running.\nIt keeps its physical resource.",
        "accent",
        178,
    ),
    (
        "capacity-refused",
        "2  Revision 2 is refused",
        "The physical executor refuses it.\nIts exact dispatch stays unsubmitted.",
        "warning",
        178,
    ),
    (
        "waiting-superseded",
        "3  Revision 3 replaces revision 2",
        "Revision 2 is superseded.\nIts unsubmitted work never runs.",
        "warning",
        178,
    ),
    (
        "stale-reconciled",
        "4  Revision 1 returns stale",
        "Its result stays inspectable.\nThe owner reconciles the return,\nthen retires physical ownership.",
        "muted",
        228,
    ),
    (
        "current-adopted",
        "5  Revision 3 becomes current",
        "It is admitted and completes.\nThe owner adopts its current result.",
        "success",
        178,
    ),
)
QUALIFIERS = (
    "Controlled event order, not elapsed time.",
    "Saved public receipt. No workload rerun.",
    "No speedup or deadline claim.",
)


def digest(path, text=False):
    data = path.read_text(encoding="utf-8").encode("utf-8") if text else path.read_bytes()
    return hashlib.sha256(data).hexdigest()


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
        *(title + ". " + body.replace("\n", " ") for _, title, body, _, _ in STAGES),
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
            {"id": key, "title": title, "explanation": body, "role": role}
            for key, title, body, role, _ in STAGES
        ],
        "text_equivalent": "\n\n".join(equivalent),
    }


def draw(data, tokens, mode, files, output):
    import matplotlib

    matplotlib.use("Agg")
    from matplotlib import rc_context
    from matplotlib.backends.backend_agg import FigureCanvasAgg
    from matplotlib.figure import Figure
    from matplotlib.font_manager import FontProperties
    from matplotlib.patches import FancyArrowPatch, FancyBboxPatch

    roles = tokens["themes"][mode]
    fonts = {name: FontProperties(fname=str(path)) for name, path in files.items()}
    with rc_context({"svg.fonttype": "path", "svg.hashsalt": "timing-revised-lifecycle-v1"}):
        fig = Figure(figsize=(WIDTH_POINTS / 72, HEIGHT_POINTS / 72), dpi=200,
                     facecolor=roles["canvas"])
        labels = []

        def label(x, top, text, size=MINIMUM_POINTS, face="Regular", color=None,
                  right=515.2, bottom=1552):
            item = fig.text(x / WIDTH_POINTS, 1 - top / HEIGHT_POINTS, text,
                            fontsize=size, fontproperties=fonts[face],
                            color=color or roles["text"], va="top", linespacing=1.3)
            labels.append((item, right, bottom))

        label(32, 32, "ADAPTIVE TIMING ENGINE", face="SemiBold", color=roles["accent"])
        label(32, 84, "Keep the current answer.", 38, "Bold")
        label(32, 160, "3 revisions. 2 physical completions.")
        top = 224
        for index, (key, title, body, role, height) in enumerate(STAGES):
            fig.add_artist(FancyBboxPatch(
                (28 / WIDTH_POINTS, 1 - (top + height) / HEIGHT_POINTS),
                (WIDTH_POINTS - 56) / WIDTH_POINTS, height / HEIGHT_POINTS,
                transform=fig.transFigure, boxstyle="round,pad=0,rounding_size=0.008",
                facecolor=roles["panel"], edgecolor=roles["border"], linewidth=0.9,
                gid=f"stage-{key}",
            ))
            label(46, top + 17, title, 26, "SemiBold", roles[role],
                  right=WIDTH_POINTS - 46, bottom=top + 62)
            label(46, top + 70, body, right=WIDTH_POINTS - 46,
                  bottom=top + height - 14)
            if index < len(STAGES) - 1:
                fig.add_artist(FancyArrowPatch(
                    (0.5, 1 - (top + height + 4) / HEIGHT_POINTS),
                    (0.5, 1 - (top + height + 18) / HEIGHT_POINTS),
                    transform=fig.transFigure, arrowstyle="-|>", mutation_scale=14,
                    color=roles["muted"], gid=f"next-{key}",
                ))
            top += height + 22
        current = data["receipt"]["current"]
        label(32, 1290, f'Current R3: {current["count"]:,} values.\nSum: {current["total"]:,}.',
              face="SemiBold")
        label(32, 1408, "\n".join(QUALIFIERS), face="Italic", color=roles["muted"])
        canvas = FigureCanvasAgg(fig)
        canvas.draw()
        for item, right, bottom in labels:
            bounds = item.get_window_extent(canvas.get_renderer())
            if (bounds.x0 < 0 or bounds.y0 < 0 or bounds.x1 > right * 200 / 72
                    or bounds.y0 < (HEIGHT_POINTS - bottom) * 200 / 72
                    or bounds.y1 > fig.bbox.height):
                raise ValueError(
                    "A lifecycle label exceeds its text area: " + item.get_text()
                    + f" (right {bounds.x1 * 72 / 200:.1f}/{right:.1f}, "
                    + f"bottom {HEIGHT_POINTS - bounds.y0 * 72 / 200:.1f}/{bottom:.1f})"
                )
        layout = {
            "width": round(fig.bbox.width), "height": round(fig.bbox.height),
            "width_points": WIDTH_POINTS, "height_points": HEIGHT_POINTS,
            "labels_within_text_areas": len(labels),
            "minimum_font_points": min(item.get_fontsize() for item, _, _ in labels),
            "intended_maximum_css_width": 760,
            "narrow_content_css_width": 350,
            "minimum_css_pixels_at_narrow_width": round(MINIMUM_POINTS * 350 / WIDTH_POINTS, 3),
            "scale_scope": "350px content inside a 390px viewport with 20px side padding",
        }
        for extension in ("png", "svg"):
            metadata = {"Description": json.dumps(semantic_record(data), sort_keys=True)}
            if extension == "svg":
                metadata["Date"] = None
            path = output / f"revised-lifecycle-{mode}.{extension}"
            fig.savefig(path, metadata=metadata)
            if extension == "svg":
                path.write_text(
                    "\n".join(line.rstrip() for line in path.read_text().splitlines()) + "\n",
                    encoding="utf-8", newline="\n",
                )
        fig.clear()
        return layout


def check_outputs():
    """Check committed identities using only the standard library."""
    record = json.loads((ROOT / MANIFEST).read_text(encoding="utf-8"))
    if record["evidence"] != semantic_record(load_data()):
        raise ValueError("Lifecycle figure evidence is stale")
    for name in INPUTS:
        if record["inputs_sha256_lf"][name] != digest(ROOT / name, text=True):
            raise ValueError(f"Lifecycle figure input changed: {name}")
    expected = {
        f"docs/revised-lifecycle-{mode}.{extension}"
        for mode in ("clair", "obscur") for extension in ("png", "svg")
    }
    if set(record["outputs_sha256"]) != expected:
        raise ValueError("Both lifecycle PNG and SVG editions are required")
    for name, checksum in record["outputs_sha256"].items():
        if digest(ROOT / name) != checksum:
            raise ValueError(f"Lifecycle figure output changed: {name}")
    return record


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--font-dir", type=Path, help="Directory of six official static Inter TTFs")
    parser.add_argument("--check", action="store_true", help="Verify committed files without fonts or rendering")
    args = parser.parse_args()
    if args.check:
        check_outputs()
        print("Both lifecycle editions match the saved public receipt and renderer")
        return
    if args.font_dir is None:
        parser.error("--font-dir is required for authoring; no font download is performed")
    data = load_data()
    tokens = json.loads((ROOT / "adaptive_timing/presentation.json").read_text(encoding="utf-8"))
    files, font_evidence = font_files(args.font_dir)
    with tempfile.TemporaryDirectory() as temporary:
        output = Path(temporary)
        layouts = {mode: draw(data, tokens, mode, files, output) for mode in ("clair", "obscur")}
        import matplotlib

        record = {
            "schema_version": 1,
            "evidence": semantic_record(data),
            "theme_source": tokens["source"],
            "renderer": {"matplotlib": matplotlib.__version__, "backend": "Agg"},
            "layout": layouts,
            "typography": {
                "files": font_evidence,
                "painted_faces": ["Regular", "SemiBold", "Bold", "Italic"],
                "png": "Glyphs rasterized from verified Inter files",
                "svg": "Labels outlined from the same files; full text is in evidence.text_equivalent",
            },
            "inputs_sha256_lf": {name: digest(ROOT / name, text=True) for name in INPUTS},
            "outputs_sha256": {"docs/" + path.name: digest(path) for path in sorted(output.iterdir())},
        }
        for path in output.iterdir():
            (ROOT / "docs" / path.name).write_bytes(path.read_bytes())
        (ROOT / MANIFEST).write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8", newline="\n")
    check_outputs()
    print("Rendered both lifecycle editions from the saved receipt with verified Inter files")


if __name__ == "__main__":
    main()
