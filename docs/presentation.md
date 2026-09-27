# Reading saved traces

The trace explorer and public page use the Clair/Obscur family. Clair has warm light surfaces, and Obscur uses near-black surfaces. Auto follows the browser's system preference. An explicit choice takes precedence until Auto is selected again. The preference is stored under `adaptive-timing-engine.appearance.v1`. If storage is unavailable, switching still works for that page.

The adapter pins [the source tokens](https://github.com/T92T1914/clair-obscur-themes/blob/7a57fe750ff50205a17e1d342106a0d3f2777159/tokens.json). It runs during report generation, outside the planner, worker and executor. Runtime timing behavior is unchanged.

## Values and evidence

`python tools/render_saved_viewer.py --output reports/retained` reads the retained compressed traces and verifies that their payload matches the historical HTML. It renders a new explorer without running a workload or rewriting the historical files. The site builder uses this same renderer. The original diagram, experiment results and traces retain their bytes.

The generated provenance includes the archive SHA-256, a canonical report hash, the rendering revision, uncommitted-state flag, renderer text hashes and pinned theme source. Renderer text hashes normalize checkout line endings to LF. The archive's introduction commit identifies the stored bytes. It is not evidence of the revision evaluated by that experiment, which was not recorded and remains `null`.

The plot and table show planning decisions. A constrained task is still planned, with its requested time adjusted to a feasible interval. A rejected task has no admitted start. These are not dispatch, expiration or observed-response outcomes. The full embedded report retains its separate execution experiment, conditions and all unfavorable results. Exporting a selected case preserves its original numeric values and task identities.

Circles mark planned tasks, squares mark constrained planned tasks and crosses mark rejected tasks. Color reinforces those meanings. Appearance changes redraw the canvas while retaining the selected case and time window. The scrollable table gives the equivalent values with units and status text. With JavaScript disabled, Auto and all saved case summaries remain available. Print uses Clair without changing the stored screen preference.

The [worker diagram](visual-example.md) has a separate renderer for the retained
101 request example. New PNG and outlined SVG editions preserve its counters,
source revision and event order without running work. The original figure
remains available. Its recorded source is separate from the unknown evaluated
revision of the trace archive above.

## Typography

The separate causal report is built by `tools/render_causal_report.py` from
`docs/causal-evidence/summary.json` and `raw.json.gz`. It checks that the summary
matches the raw evidence and uses the same appearance, typography and page
styles. The site builder exposes it at `causal.html`, with summary and full
trace downloads linked from the project page. The HTML embeds its summary for
offline export and includes every pair and outcome as text tables.

Its evaluated implementation revision is recorded in the saved evidence. The
renderer records its own source revision, hashes and dirty state separately.
Rendering performs no comparison. Browser checks preserve all 24 pairs and 48
run summaries across both appearances, no JavaScript, blocked storage, print,
narrow layouts and embedded summary download. Local rendered glyph checks
cover its actual headings, labels and body text when strict Inter checking is
enabled. Original batch evidence and its unknown evaluated revision remain
unchanged.

The HTML viewer uses six local Inter lookups that cover Regular 400, SemiBold 600, Bold 700 and their genuine italics. No font files are embedded or downloaded. Visitors without those faces use a system fallback. Code stays monospace, and unsupported characters use normal language or symbol fallbacks. Local rendered-glyph checks establish the tested environment only, not every visitor's font installation or native application acceptance.

The fixed worker figures use verified Inter files during authoring. Their PNG
pixels and SVG outlines preserve those glyphs even when the viewer has no Inter
installed. This does not change the HTML viewer's local font policy.

## Verification

```sh
python -m unittest discover -s tests -v
python tools/build_site.py
npm ci --ignore-scripts
npm run test:state
npm run test:browser
```

Browser checks require Playwright's matching Chromium or an already installed supported Chrome channel. Set `TIMING_BROWSER_CHANNEL=chrome` to use installed Chrome. CI uses that channel with sandboxing enabled. There is no visible-browser or disabled-sandbox fallback. `TIMING_REQUIRE_INTER=1` adds strict six-face rendered-glyph checks when Inter is installed. The intentionally missing-font control is a separate test. Optional `TIMING_SCREENSHOT_DIR` saves isolated-page captures.

The browser suite checks every saved case, case downloads, a downloaded offline HTML file, appearance changes, blocked storage, no JavaScript, keyboard controls, narrow and enlarged-text layouts, print and forced colors. It also checks that no external font or page requests escape the owned test origin. Headless verification does not establish physical-display comfort, a screen-reader audit or native Chrome/Equibop acceptance.

Check distributable resources separately:

```sh
python -m pip wheel . --no-deps --no-build-isolation --wheel-dir reports/wheel
python tools/check_wheel.py reports/wheel/adaptive_timing_engine-0.1.0-py3-none-any.whl
```

The second command unpacks that wheel into a temporary directory and renders through the public API in isolated Python outside the checkout. It verifies that the template, appearance script and tokens ship with the package. It does not rerun the retained experiment.
