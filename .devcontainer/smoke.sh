#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
.venv/bin/python examples/replanning.py
.venv/bin/python -m adaptive_timing demo --events 24 --output reports/container-smoke
