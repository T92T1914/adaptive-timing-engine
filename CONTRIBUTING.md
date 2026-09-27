# Working on Adaptive Timing Engine

This project separates a plan from what the executor actually manages to dispatch. I want changes to keep that distinction visible, especially when work becomes obsolete or misses its deadline.

## Start locally

Use Python 3.11 or later and run these commands from the repository root. A virtual environment keeps the development dependencies separate from your other projects.

```sh
python -m venv .venv
```

Activate it with `.venv\Scripts\Activate.ps1` in PowerShell, or `source .venv/bin/activate` on macOS or Linux. Then install what this project needs:

```sh
python -m pip install -e .
```

## Check a change

```sh
python -m unittest discover -s tests -v
python examples/replanning.py
python -m adaptive_timing demo --events 120 --output reports/review
```

Start with [the timing model](adaptive_timing/model.py), [worker](adaptive_timing/worker.py) and [executor](adaptive_timing/runtime.py). The [design notes](docs/design-decisions.md) explain the ownership and cancellation choices.

## Report a bug or propose a change

Include the event sequence, revision, expected outcome and actual trace. A short reproducible race or deadline failure is more useful than a screenshot alone. Keep issued event IDs and resource reservations intact when testing replanning.

For concurrency changes, use deterministic thread gates like the [engine tests](tests/test_engine.py). A convenient sleep can hide the failure being tested. For policy changes, retain paired traces and report admission as well as timing offsets; less variation can admit more work.

## Evidence and scope

Keep event identities, workloads and paired random draws consistent when comparing policies. Distinguish rejected plans, expired tasks and dispatched work. Record interpreter and machine details for measured latency. The effort and recovery parameters are synthetic; a successful simulation does not validate human behavior.

Useful next work includes comparing admission policies under the same workloads and measuring execution costs separately from virtual polling delays. Keep the public examples independent and reproducible.

## Development container and public site

Open this repository in Codespaces or use VS Code Dev Containers. The container uses Python 3.11 and installs the project into `.venv` during setup. Its image is pinned by digest. The `Project access` workflow builds that same environment and runs `.devcontainer/smoke.sh`. Runtime dependencies still follow the project configuration. Codespaces uses the creating account's compute and storage allowance.

Run `python tools/build_site.py` to assemble the public page in `_site`, then `python -m http.server 8080 --directory _site` to preview it. The builder copies only the listed example files. The page reads saved evidence; it does not silently rerun the experiment or claim current results. Pages deploys from `main` after the site and development environment checks pass.

For presentation changes, also run the state and isolated browser checks in [the presentation contract](docs/presentation.md). The site build renders the retained trace payload with the current viewer, preserves historical evidence and records presentation provenance. `Project access` tests that generated site before publishing its same-run artifact. The browser dependency is for development only.

The [worker figure instructions](docs/visual-example.md#rebuild-the-figure-without-another-experiment)
cover optional authoring dependencies and the retained source. Use
`python tools/render_worker_figure.py --check` to validate committed images
without fonts or another worker experiment. Keep original evidence separate
from new presentation editions.

For causal policy changes, read the [information contract](docs/causal-policy.md)
and run the regressions in `tests/test_causal.py`. The public comparison is
rendered from retained values by `python tools/render_causal_report.py` and by
the site builder. `tests/test_causal_evidence.py` audits every saved plan,
terminal count, paired offset and resource interval without another study.
Do not run the full comparison just to refresh a report. A new comparison
needs its own committed protocol and output directory.
