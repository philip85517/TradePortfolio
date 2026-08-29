# Research Review Workbench V2 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Upgrade the read-only AlphaLab research review workbench with automatically enriched industry metadata, collapsible navigation, a vertical chart/factor layout, and open-source TradingView-style daily/weekly/monthly chart analysis with volume and EMA.

**Architecture:** Keep the existing frozen-run and read-only ReviewState boundary as the single business seam. Add a focused charting module for deterministic OHLCV aggregation and EMA calculation; extend review payloads with industry provenance and chart options; keep the browser responsible only for layout and interaction state. Industry enrichment reuses the existing AlphaLab provider/updater chain and never retroactively applies a current snapshot to a historical research decision.

**Tech Stack:** Python 3.13, pandas, DuckDB, stdlib HTTP server, pytest, vanilla JavaScript, CSS, and the pinned open-source Lightweight Charts renderer with a readable offline fallback.

**Spec:** GitHub issue #2 — https://github.com/philip85517/TradePortfolio/issues/2

## Global Constraints

- Research runs remain immutable and the review server remains read-only.
- V1 does not add manual CSV/Parquet import; industry and market data use the existing local DuckDB and AlphaLab provider/updater path.
- A current industry snapshot may improve display coverage but must remain display-only/listing-only unless dated effective intervals are available.
- Selection mode must not return or render any bar, volume, EMA value, or marker after the effective signal date.
- Evaluation mode may return the future window only after explicit user selection.
- The base market data remains daily A-share data; supported chart timeframes are 1D, 1W, and 1M.
- V1 indicators are volume and EMA with periods 5, 20, and 60; full TradingView/Pine/drawing/realtime functionality is out of scope.
- Existing API calls that omit new chart parameters retain daily candles, volume, markers, and current response semantics.
- Existing research, data-binding, universe-history, Portfolio, and simulation tests must remain green.

---

### Task 1: Industry enrichment and frozen-run metadata contract

**Files:**
- Modify: `alphalab/research/data_binding.py`
- Modify: `alphalab/research/universe_history.py`
- Modify: `alphalab/research/engine.py`
- Modify: `alphalab/tests/unit/test_research_data_binding.py`
- Modify: `alphalab/tests/unit/test_universe_history.py`
- Modify: `alphalab/tests/integration/test_research_pipeline.py`

**Interfaces:**
- Consumes: `ResearchDataBinding`, existing automatic updater callbacks, and the current universe-history schema.
- Produces: an industry enrichment summary with level 1/2/3 values, source, classification system, snapshot date, effective interval availability, missing-symbol count, and `display_only`/`point_in_time` quality state; the research manifest preserves the chosen industry snapshot and never changes it during review.

- [ ] **Step 1: Write the failing tests**

Add behavior tests covering:

```python
def test_auto_binding_enriches_missing_industry_without_manual_import(tmp_path):
    calls = []

    def updater(path, market, start_date, end_date):
        calls.append((path, market, start_date, end_date))
        write_market_db(path, with_current_industry=True)

    binding = ensure_research_data(
        db_path="auto",
        candidate_paths=[tmp_path / "source.duckdb"],
        cache_path=tmp_path / "cache.duckdb",
        market="a_share",
        start_date=date(2025, 1, 2),
        end_date=date(2025, 2, 28),
        updater=updater,
    )

    assert calls
    assert binding.industry_source == "baostock"
    assert binding.industry_coverage == 1.0
    assert binding.industry_quality == "display-only"


def test_current_industry_snapshot_does_not_pass_point_in_time_quality(tmp_path):
    history = build_current_industry_snapshot([{"symbol": "000001", "industry": "金融"}])

    assert history["effective_from"].isna().all()
    assert history["effective_to"].isna().all()
    assert history.attrs["quality"] == "display-only"


def test_manifest_preserves_industry_snapshot_identity(tmp_path):
    report = run_research_with_industry_snapshot(tmp_path)

    assert report.manifest["data_binding"]["industry_source"] == "baostock"
    assert report.manifest["data_binding"]["industry_snapshot_id"]
    assert report.manifest["diagnostics"]["universe"]["point_in_time_quality"] == "listing-only"
```

- [ ] **Step 2: Run the focused tests to verify they fail**

Run: `python -m pytest -q alphalab/tests/unit/test_research_data_binding.py alphalab/tests/unit/test_universe_history.py alphalab/tests/integration/test_research_pipeline.py -k "industry or snapshot"`

Expected: FAIL because the binding has no industry coverage/source fields and the automatic provider path does not persist an industry snapshot identity.

- [ ] **Step 3: Implement the minimum data contract**

Extend the binding object and its serialization with industry coverage and provenance. Reuse the existing BaoStock industry query and the existing market database schema; do not add a second downloader. Store current mappings as a dated snapshot with no fabricated effective interval. Add a separate summary helper that reports display-only quality when level 1/2/3 values exist but effective dates do not. Make research manifest creation copy this summary at run time.

- [ ] **Step 4: Run the focused tests to verify they pass**

Run: `python -m pytest -q alphalab/tests/unit/test_research_data_binding.py alphalab/tests/unit/test_universe_history.py alphalab/tests/integration/test_research_pipeline.py -k "industry or snapshot"`

Expected: PASS, with strict point-in-time tests still failing closed when effective industry intervals are absent.

- [ ] **Step 5: Commit the data contract**

```bash
git add alphalab/research/data_binding.py alphalab/research/universe_history.py alphalab/research/engine.py alphalab/tests/unit/test_research_data_binding.py alphalab/tests/unit/test_universe_history.py alphalab/tests/integration/test_research_pipeline.py
git commit -m "feat(research): enrich industry metadata automatically"
```

### Task 2: Deterministic chart aggregation and EMA module

**Files:**
- Create: `alphalab/research/charting.py`
- Create: `alphalab/tests/unit/test_research_charting.py`

**Interfaces:**
- Consumes: a normalized daily DataFrame containing `date`, `open`, `high`, `low`, `close`, `volume`, and `amount`.
- Produces: `aggregate_ohlcv(rows, timeframe) -> DataFrame`, `ema_series(rows, period) -> Series`, and `build_chart_series(rows, timeframe, ema_periods) -> dict` with deterministic bars, volume, and indicator series.

- [ ] **Step 1: Write the failing tests**

```python
def test_weekly_ohlcv_uses_first_open_extreme_high_low_last_close_and_sum_volume():
    rows = daily_rows("2025-01-02", [10, 11, 9, 12, 13])

    weekly = aggregate_ohlcv(rows, "1W")

    assert weekly.iloc[0][["open", "high", "low", "close", "volume"]].tolist() == [10, 13, 9, 13, 5]
    assert weekly.iloc[0]["source_start"] == date(2025, 1, 2)
    assert weekly.iloc[0]["source_end"] == date(2025, 1, 8)


def test_ema_uses_only_prior_and_current_values_and_keeps_warmup_nulls():
    rows = daily_rows("2025-01-02", [10, 12, 14, 16])

    result = ema_series(rows["close"], period=3)

    assert result.iloc[:2].isna().all()
    assert result.iloc[2] == pytest.approx(12.0)
    assert result.iloc[3] == pytest.approx(14.0)


def test_chart_series_rejects_unknown_timeframe_or_nonpositive_ema_period():
    rows = daily_rows("2025-01-02", [10, 12, 14])

    with pytest.raises(ValueError, match="timeframe"):
        build_chart_series(rows, "5m", [20])
    with pytest.raises(ValueError, match="EMA"):
        build_chart_series(rows, "1D", [0])
```

- [ ] **Step 2: Run the charting tests to verify they fail**

Run: `python -m pytest -q alphalab/tests/unit/test_research_charting.py`

Expected: FAIL because the charting module does not exist.

- [ ] **Step 3: Implement the minimum charting module**

Normalize dates to calendar dates, validate the three supported timeframes, aggregate 1W by ISO week and 1M by calendar month, preserve source start/end dates, and keep daily rows unchanged for 1D. Calculate EMA with the standard recursive multiplier `2 / (period + 1)` and null warmup rows until the requested period is available. Return JSON-ready records only at the review boundary; never fetch data or read current time from this module.

- [ ] **Step 4: Run the charting tests to verify they pass**

Run: `python -m pytest -q alphalab/tests/unit/test_research_charting.py`

Expected: PASS with exact hand-calculated aggregation and EMA values.

- [ ] **Step 5: Commit the charting module**

```bash
git add alphalab/research/charting.py alphalab/tests/unit/test_research_charting.py
git commit -m "feat(research): add deterministic chart timeframes and EMA"
```

### Task 3: ReviewState industry and chart API

**Files:**
- Modify: `alphalab/research/review.py`
- Modify: `alphalab/research/__init__.py`
- Modify: `alphalab/tests/integration/test_research_review.py`

**Interfaces:**
- Consumes: the Task 1 frozen industry summary and Task 2 charting functions.
- Produces: summary industry metadata; candidate level fields; stock detail chart payload accepting `mode`, `portfolio_id`, `timeframe`, and `ema`; explicit 4xx validation for unsupported values; unchanged default payload for old callers.

- [ ] **Step 1: Write the failing tests**

```python
def test_review_summary_exposes_industry_coverage_and_source(tmp_path):
    state = _state_with_industry(tmp_path)

    summary = state.summary()

    assert summary["industry"]["coverage"] == 1.0
    assert summary["industry"]["source"] == "baostock"
    assert summary["industry"]["quality"] == "display-only"


def test_stock_detail_returns_weekly_bars_and_requested_ema(tmp_path):
    state = _state(tmp_path)

    payload = state.stock_detail("300468", mode="selection", timeframe="1W", ema=(20, 60))

    assert payload["chart"]["timeframe"] == "1W"
    assert payload["chart"]["bars"]
    assert {item["period"] for item in payload["chart"]["indicators"]["ema"]} == {20, 60}
    assert max(date.fromisoformat(row["date"]) for row in payload["chart"]["bars"]) <= state.signal_date


def test_selection_chart_does_not_expose_future_rows_or_ema(tmp_path):
    state = _state(tmp_path)

    payload = state.stock_detail("300468", mode="selection", timeframe="1M", ema=(20,))

    assert all(date.fromisoformat(row["date"]) <= state.signal_date for row in payload["chart"]["bars"])
    assert all(date.fromisoformat(row["date"]) <= state.signal_date for row in payload["chart"]["indicators"]["ema"][0]["values"])


def test_review_server_rejects_invalid_chart_options(tmp_path):
    state = _state(tmp_path)
    server = create_review_server(state)

    response = _get_error(server, "/api/stock?symbol=300468&mode=selection&timeframe=5m&ema=0")

    assert response.status == 400
    assert "timeframe" in response.body or "EMA" in response.body
```

- [ ] **Step 2: Run the review integration tests to verify they fail**

Run: `python -m pytest -q alphalab/tests/integration/test_research_review.py -k "industry or chart or future_rows or invalid_chart"`

Expected: FAIL because review payloads only return daily rows and have no industry contract or chart options.

- [ ] **Step 3: Extend the read-only review contract**

Add a single chart-options parser and pass its validated result through `ReviewState.stock_detail`. Apply the mode date boundary before aggregation and use only earlier rows for EMA warmup. Return `chart` with timeframe, bars, volume, EMA series, marker dates, data range, and quality. Add `industry` to summary, `industry_level1/2/3` to candidates, and full industry metadata to stock/Portfolio payloads. Keep old top-level `rows` and `markers` aliases populated for backward compatibility.

- [ ] **Step 4: Run the review integration tests to verify they pass**

Run: `python -m pytest -q alphalab/tests/integration/test_research_review.py`

Expected: PASS, including all existing future-data and Portfolio isolation tests.

- [ ] **Step 5: Commit the review API**

```bash
git add alphalab/research/review.py alphalab/research/__init__.py alphalab/tests/integration/test_research_review.py
git commit -m "feat(review): expose industry and chart analysis payloads"
```

### Task 4: Collapsible navigation and vertical factor layout

**Files:**
- Modify: `alphalab/research/static/index.html`
- Modify: `alphalab/research/static/styles.css`
- Modify: `alphalab/research/static/app.js`
- Modify: `alphalab/tests/integration/test_research_review.py`

**Interfaces:**
- Consumes: the existing summary/candidate/stock payloads and the browser-only review state.
- Produces: an accessible `aria-expanded` navigation toggle, persisted collapsed preference, full-width chart panel, horizontal responsive factor strip, and visible industry/provenance information without changing server state.

- [ ] **Step 1: Write the failing tests**

```python
def test_review_html_contains_accessible_navigation_toggle_and_vertical_detail_contract(tmp_path):
    state = _state(tmp_path)
    body = _get_html(state)

    assert 'aria-controls="workbenchSidebar"' in body
    assert 'aria-expanded="true"' in body
    assert 'class="chart-layout vertical-detail"' in body
    assert 'class="factor-strip"' in body
    assert "行业来源" in body
```

Add a JavaScript smoke assertion using the browser harness or a DOM fixture that toggles the control, verifies `aria-expanded` changes, and verifies the `collapsed` preference survives a reload.

- [ ] **Step 2: Run the layout tests to verify they fail**

Run: `python -m pytest -q alphalab/tests/integration/test_research_review.py -k "accessible_navigation or vertical_detail"`

Expected: FAIL because the sidebar has no toggle, the chart layout is a two-column grid, and the factor grid has no horizontal-layout contract.

- [ ] **Step 3: Implement the minimum layout behavior**

Add a sidebar toggle before the existing navigation, icons with text alternatives, `aria-controls`, and `aria-expanded`. Store only the preference key in browser storage. Use a collapsed desktop column that preserves icon navigation and a mobile drawer that returns focus to the toggle when closed. Change the detail area to a single column with chart first and factor strip below. Expose industry hierarchy, source, and quality in a compact detail block. Use CSS grid/flex wrapping rather than fixed pixel placement.

- [ ] **Step 4: Run the layout tests to verify they pass**

Run: `python -m pytest -q alphalab/tests/integration/test_research_review.py`

Expected: PASS, and the existing review page still renders the candidate and Portfolio sections.

- [ ] **Step 5: Commit the layout**

```bash
git add alphalab/research/static/index.html alphalab/research/static/styles.css alphalab/research/static/app.js alphalab/tests/integration/test_research_review.py
git commit -m "feat(review): add collapsible navigation and factor strip layout"
```

### Task 5: TradingView-style chart controls and renderer

**Files:**
- Modify: `alphalab/research/static/index.html`
- Modify: `alphalab/research/static/styles.css`
- Modify: `alphalab/research/static/app.js`
- Modify: `alphalab/tests/integration/test_research_review.py`

**Interfaces:**
- Consumes: Task 3 chart payloads and the existing Lightweight Charts renderer.
- Produces: timeframe buttons for 1D/1W/1M, volume visibility toggle, EMA period toggles, crosshair/tooltips, zoom/pan/fit controls, and event markers that remain safe across selection/evaluation modes.

- [ ] **Step 1: Write the failing tests**

```python
def test_review_html_contains_chart_controls_and_indicator_labels(tmp_path):
    body = _get_html(_state(tmp_path))

    for label in ["1D", "1W", "1M", "成交量", "EMA 5", "EMA 20", "EMA 60", "适配", "重置"]:
        assert label in body


def test_chart_default_payload_is_daily_and_selection_safe(tmp_path):
    state = _state(tmp_path)

    payload = state.stock_detail("300468", mode="selection")

    assert payload["chart"]["timeframe"] == "1D"
    assert payload["chart"]["volume"][0]["date"] <= payload["signal_date"]
```

Add a browser smoke test that clicks 1W, enables EMA 20, toggles volume, and checks the visible toolbar state and chart canvas/SVG remain non-empty. The same test switches to evaluation and checks the future markers appear only then.

- [ ] **Step 2: Run the chart UI tests to verify they fail**

Run: `python -m pytest -q alphalab/tests/integration/test_research_review.py -k "chart_controls or default_payload"`

Expected: FAIL because the current page has only a chart container and a static signal legend.

- [ ] **Step 3: Implement the minimum chart controls and renderer state**

Add an explicit chart toolbar with safe defaults: 1D selected, volume on, EMA 20 and EMA 60 off unless chosen by the user. Keep chart state in the browser only. Fetch the chart payload on timeframe/indicator changes, destroy and recreate the Lightweight Chart instance when options change, add candlesticks, volume histogram, EMA line series, crosshair, resize handling, fit-content, and reset-range actions. Render a readable SVG fallback with the same timeframe/indicator labels if the open-source library is unavailable. Escape all server values before inserting HTML.

- [ ] **Step 4: Run the chart UI tests to verify they pass**

Run: `python -m pytest -q alphalab/tests/integration/test_research_review.py`

Expected: PASS with default daily behavior, interactive chart controls, future-data guard, and existing Portfolio switching intact.

- [ ] **Step 5: Commit the chart UI**

```bash
git add alphalab/research/static/index.html alphalab/research/static/styles.css alphalab/research/static/app.js alphalab/tests/integration/test_research_review.py
git commit -m "feat(review): add timeframe volume and EMA chart controls"
```

### Task 6: Regression, real-data smoke, and documentation

**Files:**
- Modify: `alphalab/README.md`
- Modify: `docs/superpowers/plans/2026-08-29-research-review-workbench-v2.md`

**Interfaces:**
- Consumes: all completed Tasks 1–5.
- Produces: documented startup/review commands, evidence that existing research remains unchanged, and a real AlphaLab data smoke result covering industry, layout, chart controls, and independent Portfolio metrics.

- [ ] **Step 1: Run the complete automated suite**

Run: `python -m pytest -q`

Expected: all existing and new tests pass with no warnings that indicate unhandled browser/API errors.

- [ ] **Step 2: Run static and syntax checks**

Run: `git diff --check && python -m compileall -q alphalab etf_strategy && node --check alphalab/research/static/app.js`

Expected: exit code 0 with no output.

- [ ] **Step 3: Run the real-data smoke**

Run:

```bash
python -m alphalab research run \
  --as-of 2025-07-01 \
  --horizons 21,42 \
  --top-n 10 \
  --universe-mode point-in-time \
  --portfolio small=50000 \
  --portfolio core=100000 \
  --portfolio large=200000 \
  --runs-dir alphalab/reports/research
```

Then start review for the emitted run ID and manually verify the actual page shows industry metadata/provenance, a collapsible sidebar, the vertical chart/factor layout, 1D/1W/1M controls, volume, EMA, selection-mode future-data protection, evaluation markers, and all three Portfolio choices.

- [ ] **Step 4: Document the user-visible workflow**

Update the research README with the automatic industry behavior, quality distinction between display-only and point-in-time, chart controls, and the exact review command. State that the page remains read-only and that current snapshots are never used to backfill historical industry.

- [ ] **Step 5: Commit the final documentation and evidence notes**

```bash
git add alphalab/README.md docs/superpowers/plans/2026-08-29-research-review-workbench-v2.md
git commit -m "docs(research): document review workbench v2"
```

## Self-Review Checklist

- [ ] Industry enrichment is automatic, provenance-bearing, and never upgrades listing-only data to complete point-in-time quality without effective intervals.
- [ ] The single ReviewState/API seam serves both old daily requests and new chart options.
- [ ] Selection mode applies the future-data boundary before aggregation and EMA warmup.
- [ ] Directory collapse, factor-strip layout, and chart controls are browser-only state.
- [ ] Volume and EMA values are deterministic and tested with hand-calculated data.
- [ ] Offline renderer failure is visible and does not create a blank review surface.
- [ ] Existing research and Portfolio behavior remains unchanged.
- [ ] No plan step contains a placeholder or relies on manual data import.
