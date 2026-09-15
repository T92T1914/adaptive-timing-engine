# Contributing to Adaptive Timing Engine

I welcome focused fixes, clearer examples and results that challenge an assumption in the project. If something looks wrong, I would rather have a small case I can run than a broad claim that it is broken.

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

Check the existing issues first. Include the revision, Python version, operating system, command, expected behavior and actual output. For a numerical issue, include the smallest input that demonstrates it. Remove credentials and private data from logs before posting.

Keep a pull request focused on one problem. Explain what changes for someone using the project, why the approach fits and which checks you ran. Add a regression test when it captures a real failure. Documentation changes should be checked against the current code and examples.

## Evidence and scope

Keep event identities, workloads and paired random draws consistent when comparing policies. Distinguish rejected plans, expired tasks and dispatched work. Record interpreter and machine details for measured latency. The effort and recovery parameters are synthetic; a successful simulation does not validate human behavior.

Useful next work includes comparing admission policies under the same workloads and measuring execution costs separately from virtual polling delays. Keep the public examples independent and reproducible.

## Writing

Use plain language and concrete examples. Avoid em dashes and unnecessary hyphens in authored prose. Preserve the exact spelling of code, commands, paths, package names, links and quoted evidence. Claims about performance should link to measurements and say what was actually tested.

Be respectful when discussing a change. Questions and disagreements are welcome; keep them about the work.
