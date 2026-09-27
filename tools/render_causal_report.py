"""Render retained causal study values with the existing presentation adapter."""
import argparse
import gzip
import hashlib
import html
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from adaptive_timing.presentation import (ROOT as PRESENTATION, appearance_control,
    appearance_css, report_hash, safe_json, tokens)

EVIDENCE = ROOT / "docs/causal-evidence"
PUBLIC = "https://t92t1914.github.io/adaptive-timing-engine/"
SOURCE = "https://github.com/T92T1914/adaptive-timing-engine/"


def display(value, signed=False):
    if value is None:
        return "unknown"
    if isinstance(value, float):
        return f"{value:+.3f}" if signed else f"{value:.3f}"
    return f"{value:+d}" if signed and isinstance(value, int) else str(value)


def retained():
    summary = json.loads((EVIDENCE / "summary.json").read_text(encoding="utf-8"))
    raw_bytes = (EVIDENCE / "raw.json.gz").read_bytes()
    raw = json.loads(gzip.decompress(raw_bytes))
    excluded = {"plans", "execution", "changes", "terminal", "offsets_by_id_ms"}
    derived = {key: value for key, value in raw.items() if key != "streams"}
    derived["cases"] = [{key: value for key, value in case.items() if key not in excluded}
                        for case in raw["cases"]]
    if summary != derived:
        raise ValueError("Summary differs from raw retained evidence")
    return summary, hashlib.sha256(raw_bytes).hexdigest()


def tables(summary):
    pair_headers = ["Workload", "Delivery", "Seed", "Admission difference", "Dispatch difference",
                    "Expired difference", "Shared IDs", "Mean offset difference on shared IDs (ms)"]
    pair_rows = [[row["scenario"], row["pattern"], row["seed"],
        display(row["admitted_difference"], True), display(row["dispatched_difference"], True),
        display(row["expired_difference"], True), row["shared_dispatched"],
        display(row["shared_mean_offset_difference_ms"], True)] for row in summary["pairs"]]
    state_headers = ["Workload", "Delivery", "Seed", "Policy", "Admitted", "Rejected",
                     "Expired", "Dispatched", "Canceled", "Unknown", "Offset p95 (ms)"]
    state_rows = [[row["scenario"], row["pattern"], row["seed"], row["mode"],
        *[display(row[key]) for key in ("admitted", "rejected", "expired", "dispatched",
                                     "canceled", "unknown", "offset_p95_ms")]] for row in summary["cases"]]
    work_headers = ["Workload", "Delivery", "Seed", "Policy", "Calculations", "Event evaluations",
                    "Planned entries", "Busy seconds", "Mean density", "Mean fatigue", "Mean sigma (ms)"]
    work_rows = [[row["scenario"], row["pattern"], row["seed"], row["mode"],
        *[display(row[key]) for key in ("calculations", "event_evaluations", "planned",
                                     "busy_seconds", "mean_plan_density", "mean_plan_fatigue",
                                     "mean_plan_sigma_ms")]] for row in summary["cases"]]
    return [("All 24 pairs. Differences are causal minus offline.", pair_headers, pair_rows),
            ("All 48 runs. Terminal columns exclude admission, which can overlap them.", state_headers, state_rows),
            ("Work and policy estimates across every installed plan.", work_headers, work_rows)]


def findings(summary):
    pairs = summary["pairs"]
    lower = sum(row["dispatched_difference"] < 0 for row in pairs)
    equal = sum(row["dispatched_difference"] == 0 for row in pairs)
    higher = sum(row["dispatched_difference"] > 0 for row in pairs)
    worst = min(pairs, key=lambda row: row["dispatched_difference"])
    key = (worst["scenario"], worst["pattern"], worst["seed"])
    cases = {row["mode"]: row for row in summary["cases"]
             if (row["scenario"], row["pattern"], row["seed"]) == key}
    return [
        f"The causal policy dispatched fewer tasks in {lower} of the 24 pairs, the same number in {equal}, "
        f"and more in {higher}. This comparison retains every declared workload and seed.",
        f"The largest dispatch deficit was {key[0]} with {key[1]} delivery at seed {key[2]}. "
        f"The causal session dispatched {cases['causal']['dispatched']} tasks and the offline comparator "
        f"dispatched {cases['offline']['dispatched']}. Admission alone would not describe that result.",
        "At seed 42 with rolling saturation, the causal policy dispatched 45 tasks and expired 7. "
        "The offline comparator dispatched 8 and expired 42. The causal session evaluated 736 event "
        "snapshots across replans, compared with 120 in the single offline calculation. Only 7 IDs "
        "were dispatched by both policies in that pair, which limits its shared offset comparison.",
        "The offline comparator knows final versions and cancellations before service begins. The causal "
        "policy knows only delivered facts and also changes how effort and replanning are handled. These "
        "results compare those complete policies. They do not isolate one mechanism or establish a universal winner.",
        "Offsets are measured among dispatched tasks, so the sample changes when admission or expiration changes. "
        "The paired table also compares mean absolute offsets only among IDs dispatched by both policies. "
        "A positive difference means the causal policy started farther from its applicable target on that subset.",
    ]


def markdown(summary, raw_hash):
    revision = summary["evaluated_revision"]
    lines = ["# Planning after information arrives", "", *sum(([paragraph, ""] for paragraph in findings(summary)), []),
        f"Evaluated implementation and protocol: [`{revision}`]({SOURCE}tree/{revision}). "
        "Four synthetic workloads, two delivery patterns, three seeds and 120 IDs per stream produce 24 pairs.", "",
        "Read the [information contract](../causal-policy.md), [committed protocol](../causal-protocol.md), "
        f"[HTML report]({PUBLIC}causal.html), [summary](summary.json) and [raw traces](raw.json.gz).", "",
        "Planned entries count every admitted schedule across calculations. Admission counts distinct IDs that "
        "were admitted at least once. Rejected, expired, dispatched, canceled and unknown are separate final "
        "states and partition the event IDs. They must not be added to admission. Completed remains null because "
        "there is no external completion observation. Modeled service finish uses the assumed duration only.", ""]
    for caption, headers, rows in tables(summary):
        lines += [caption, "", "| " + " | ".join(headers) + " |",
                  "| " + " | ".join("---" for _ in headers) + " |"]
        lines += ["| " + " | ".join(map(str, row)) + " |" for row in rows]
        lines.append("")
    lines += ["The raw archive retains all delivery groups, event versions, installed plans and execution records. "
        "The summary also records ignored terminal changes, modeled service finishes, utilization and diagnostic "
        "calculation times. Means over plan entries include repeated IDs. Calculation time is local context, "
        "not a scaling study or a deadline guarantee. Other figure unit checks were active during this run, "
        "so calculation durations are not compared. Expiration records retain the executor's attempted service "
        "start, without a separate poll emission timestamp.", "",
        "The study ran synchronously in one process, with virtual polls every 20 ms and a candidate budget of 32. "
        "No external device was controlled. Stale and delayed planner results are tested with deterministic gates "
        "outside this comparison. Lower offset alone does not establish a better scheduling policy, and "
        "synthetic workloads do not establish behavior on an unseen production stream.", "",
        f"Raw archive SHA-256: `{raw_hash}`. The original 48 case batch study and its figures remain unchanged. "
        "Its evaluated revision was not recorded and remains unknown.", ""]
    return "\n".join(lines)


def render(output: Path):
    summary, raw_hash = retained()
    evaluated = summary["evaluated_revision"]
    rendered = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    identity = {"evaluated_revision": evaluated, "rendered_revision": rendered,
        "renderer_has_uncommitted_changes": bool(subprocess.check_output(["git", "status", "--porcelain"], cwd=ROOT, text=True).strip()),
        "experiment_rerun": False, "summary_sha256": report_hash(summary),
        "raw_archive_sha256": raw_hash, "theme_source": tokens()["source"],
        "renderer_lf_sha256": hashlib.sha256(Path(__file__).read_text(encoding="utf-8").encode()).hexdigest(),
        "presentation_lf_sha256": {name: hashlib.sha256((ROOT / name).read_text(encoding="utf-8").encode()).hexdigest()
            for name in ("adaptive_timing/presentation.py", "adaptive_timing/presentation.json",
                         "adaptive_timing/appearance.js", "site/style.css")}}
    sections = []
    for index, (caption, headers, rows) in enumerate(tables(summary)):
        table = ('<div class="table-wrap" tabindex="0" role="region" aria-label="' + html.escape(caption) + '">'
            '<table id="table-' + str(index) + '"><caption>' + html.escape(caption) + '</caption><thead><tr>'
            + ''.join('<th scope="col">' + html.escape(value) + '</th>' for value in headers)
            + '</tr></thead><tbody>' + ''.join('<tr>' + ''.join('<td>' + html.escape(str(value)) + '</td>'
                for value in row) + '</tr>' for row in rows) + '</tbody></table></div>')
        if index:
            table = '<details><summary>' + ('Every outcome' if index == 1 else 'Work and estimates') + '</summary>' + table + '</details>'
        sections.append(table)
    style = (ROOT / "site/style.css").read_text(encoding="utf-8")
    paragraphs = ''.join('<p>' + html.escape(text) + '</p>' for text in findings(summary))
    source_at = SOURCE + 'tree/' + evaluated
    document = f'''<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Planning after information arrives | Adaptive Timing Engine</title>
<script>{(PRESENTATION / 'appearance.js').read_text(encoding='utf-8')}</script>
<style>{appearance_css()}{style}
td{{font-variant-numeric:tabular-nums}}p{{max-width:78ch}}button{{cursor:pointer;font:inherit;background:var(--control);color:var(--text)}}
</style></head><body><a class="skip" href="#main">Skip to results</a><div class="wrap">
<nav aria-label="Report navigation"><a href="{PUBLIC}">Project page</a><a href="{source_at}">Evaluated source</a></nav>
<main id="main">{appearance_control()}<noscript><p>Auto follows your system. All recorded results remain available below.</p></noscript>
<p class="eyebrow">Causal information study</p><h1>Planning after information arrives.</h1>
<p class="lead">24 paired synthetic streams compare delivered information with a complete batch that knows final versions and cancellations.</p>
<section class="panel"><h2>What happened</h2>{paragraphs}</section>
<p>Each stream has 120 stable task IDs. Workloads are steady, burst, recovery and saturation. Delivery is rolling or includes updates, cancellations and late arrivals. Seeds are 7, 42 and 73.</p>
<p><a href="{SOURCE}blob/{evaluated}/docs/causal-protocol.md">Read the committed protocol</a> and
<a href="{SOURCE}blob/{evaluated}/docs/causal-policy.md">information contract</a>.
The exact evaluated revision is <a href="{source_at}"><code>{evaluated}</code></a>.</p>
<div class="actions"><button class="button" id="download-summary" hidden>Download embedded summary</button>
<a class="button" href="{PUBLIC}causal-raw.json.gz" download>Download full raw traces</a>
<a class="button" href="{PUBLIC}causal.html" download>Download this offline report</a></div>
<h2>Every paired result</h2><p>Positive differences mean a larger value for the causal policy. A larger admission count does not guarantee more dispatches. The shared offset column uses only IDs dispatched by both members of that pair. On a narrow screen, scroll each table horizontally to read every column.</p>
{''.join(sections)}
<p>Means over plan entries count an ID again whenever it appears in another plan. They describe the installed calculations, not an equally weighted sample of unique tasks.</p>
<section class="panel"><h2>What the states mean</h2>
<p>Planned entries count schedules across all installed calculations. Admitted counts distinct IDs admitted at least once. These counts can include tasks later canceled, rejected or expired.</p>
<p>Rejected means no feasible interval was admitted. Expired means an admitted task missed its completion window at polling. Dispatched means a record was emitted. Canceled means cancellation became terminal before dispatch. Unknown means no final state was available at the horizon. Those five states partition each stream.</p>
<p>Completed is unknown because no external completion was observed. Modeled service finish, available in the summary, only means the assumed finish time elapsed.</p></section>
<h2>Limits and reproduction</h2><p>This is one process with synchronous planning and virtual polls every 20 ms. It does not measure operating system wakeups or process scaling. Synthetic streams do not establish behavior on unseen production work. The offline comparator has unavailable future information, while the causal policy also changes effort history and replanning. The comparison does not isolate one mechanism.</p>
<p>Other figure unit checks were active during the run. Calculation durations are retained as diagnostic values and are not compared. Expiration records retain the executor's attempted service start, without a separate poll emission timestamp.</p>
<p>Every stream, plan, event version and execution record is in the raw archive. Use a new output directory to reproduce the committed protocol. Rendering this report reads retained values and runs no experiment.</p>
<pre><code>python tools/run_causal_study.py --output /path/to/new/causal-run
python tools/render_causal_report.py --output reports/causal</code></pre>
<p>The retained original 48 case batch study is separate. Its evaluated revision remains unknown. <a href="{PUBLIC}explorer.html">Read its unchanged evidence through the current explorer</a>.</p>
<details><summary>Evidence and presentation identity</summary><pre id="identity">{html.escape(json.dumps(identity, indent=2))}</pre></details>
</main><footer><p class="font-note">Inter uses installed local faces with a system fallback. No font files or external resources are fetched by this report. Headless checks do not establish physical display or native application acceptance.</p></footer></div>
<script type="application/json" id="causal-data">{safe_json(summary)}</script>
<script type="application/json" id="causal-presentation">{safe_json(identity)}</script>
<script>const button=document.querySelector('#download-summary');button.hidden=false;
button.addEventListener('click',()=>{{const blob=new Blob([document.querySelector('#causal-data').textContent],{{type:'application/json'}});
const url=URL.createObjectURL(blob),link=document.createElement('a');link.href=url;link.download='causal-summary.json';link.click();setTimeout(()=>URL.revokeObjectURL(url),1000);}});</script>
</body></html>'''
    output.mkdir(parents=True, exist_ok=True)
    (output / "causal.html").write_text(document, encoding="utf-8", newline="\n")
    (output / "causal-presentation.json").write_text(json.dumps(identity, indent=2) + "\n", encoding="utf-8", newline="\n")
    return summary, identity


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path("reports/causal"))
    args = parser.parse_args()
    render(args.output)
