# Point-in-Time Stock Universe Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 为 AlphaLab 因子迭代提供可审计的历史股票池数据契约、导入校验工具和按日期读取接口，并接入历史研究引擎的 `point-in-time` 模式。

**Architecture:** 在现有市场 DuckDB 中新增只追加的 `market_universe_history` 有效区间表；外部 CSV/Parquet 通过显式导入器进入该表，导入前拒绝重复区间、日期错误和缺少来源身份。研究引擎通过 `load_universe_as_of(as_of, market)` 读取信号日生效的股票池与行业元数据；旧的当前快照仅继续服务 `observed-history` 探索模式，不会被自动复制成历史数据。

**Tech Stack:** Python 3.13、pandas、DuckDB、argparse、pytest、现有 `alphalab.research` 研究接口。

**Spec:** `AlphaLab 历史截面因子研究与组合回测 Spec.md`

## Global Constraints

- 历史区间使用半开区间 `[effective_from, effective_to)`；`effective_to = NULL` 表示当前仍有效。
- 研究运行只读数据；导入器是唯一写入历史股票池的入口。
- 不把当前 `market_universe` 快照推断为过去的历史状态；没有有效历史数据时 point-in-time 必须失败并说明覆盖缺口。
- 每条历史记录必须带 `source` 和 `snapshot_id`，以便复现和审计。
- 同一市场、股票和来源快照的有效区间不得重叠；日期边界相接允许。
- `observed-history` 保持现有行为，并在 manifest、诊断和审阅页标记为探索模式。

---

### Task 1: 定义历史股票池表与纯校验模块

**Files:**
- Create: `etf_strategy/src/universe_history.py`
- Modify: `etf_strategy/src/market_data_store.py:initialize_market_database`
- Test: `etf_strategy/tests/test_universe_history.py`

**Interfaces:**
- Produces `HISTORY_COLUMNS`、`normalize_history_frame(frame, *, source, snapshot_id)`、`validate_history_frame(frame)` 和 `initialize_market_database` 创建的 `market_universe_history` 表。
- `validate_history_frame` 返回 `{"rows": int, "symbols": int, "markets": int, "interval_conflicts": list[dict], "missing_required": list[str], "invalid_rows": list[dict]}`；发现硬错误时由导入层转换为 `ValueError`。

- [ ] **Step 1: Write the failing tests**

```python
def test_history_schema_accepts_adjacent_intervals_and_normalizes_dates():
    frame = pd.DataFrame([
        {"market": "a_share", "symbol": "000001", "effective_from": "2020-01-01", "effective_to": "2022-01-01", "status": "active", "source": "vendor", "snapshot_id": "s1"},
        {"market": "a_share", "symbol": "000001", "effective_from": "2022-01-01", "effective_to": None, "status": "active", "source": "vendor", "snapshot_id": "s1"},
    ])
    result = normalize_history_frame(frame, source=None, snapshot_id=None)
    assert result["effective_from"].dtype == "datetime64[ns]"
    assert result["effective_to"].isna().sum() == 1


def test_history_schema_rejects_overlapping_intervals():
    frame = pd.DataFrame([
        {"market": "a_share", "symbol": "000001", "effective_from": "2020-01-01", "effective_to": "2022-06-01", "status": "active", "source": "vendor", "snapshot_id": "s1"},
        {"market": "a_share", "symbol": "000001", "effective_from": "2022-01-01", "effective_to": None, "status": "active", "source": "vendor", "snapshot_id": "s1"},
    ])
    report = validate_history_frame(frame)
    assert report["interval_conflicts"]


def test_market_database_creates_history_table(tmp_path):
    con = connect_market_db(tmp_path / "market.duckdb")
    assert "market_universe_history" in {row[0] for row in con.execute("SHOW TABLES").fetchall()}
    con.close()
```

- [ ] **Step 2: Run the focused tests to verify they fail**

Run: `./.venv/bin/python -m pytest etf_strategy/tests/test_universe_history.py -q`

Expected: FAIL because the history table and normalization/validation functions do not exist.

- [ ] **Step 3: Implement the minimal schema and validator**

Create the table with columns `market`, `symbol`, `effective_from`, `effective_to`, `status`, `name`, `industry_level1`, `industry_level2`, `industry_level3`, `source`, `snapshot_id`, `source_recorded_at`, and a primary key over `(market, symbol, effective_from, source, snapshot_id)`. Normalize dates to timezone-naive midnight, require non-empty market/symbol/status/source/snapshot_id, reject `effective_to <= effective_from`, and report interval overlaps within the same `(market, symbol, source, snapshot_id)` group.

- [ ] **Step 4: Run the focused tests to verify they pass**

Run: `./.venv/bin/python -m pytest etf_strategy/tests/test_universe_history.py -q`

Expected: PASS.

- [ ] **Step 5: Run existing market-store tests**

Run: `./.venv/bin/python -m pytest etf_strategy/tests -q`

Expected: existing market database and universe tests remain green.

### Task 2: Add explicit history import, dry-run, and coverage report

**Files:**
- Modify: `etf_strategy/src/universe_history.py`
- Create: `etf_strategy/scripts/import_universe_history.py`
- Test: `etf_strategy/tests/test_universe_history.py`

**Interfaces:**
- Produces `upsert_history(frame, db_path, *, replace_snapshot=False) -> dict` and `load_history_as_of(db_path, as_of, market, symbols=None) -> pd.DataFrame`.
- CLI accepts `--input`, `--db`, `--source`, `--snapshot-id`, `--market`, `--dry-run`, and `--replace-snapshot`; it never contacts a network source.

- [ ] **Step 1: Write failing importer and query tests**

```python
def test_import_history_writes_snapshot_and_as_of_query_returns_latest_interval(tmp_path):
    input_frame = pd.DataFrame([
        {"market": "a_share", "symbol": "000001", "effective_from": "2020-01-01", "effective_to": "2023-01-01", "status": "active", "industry_level1": "金融", "source": "vendor", "snapshot_id": "2026-01"},
        {"market": "a_share", "symbol": "000001", "effective_from": "2023-01-01", "effective_to": None, "status": "active", "industry_level1": "科技", "source": "vendor", "snapshot_id": "2026-01"},
    ])
    result = upsert_history(input_frame, tmp_path / "market.duckdb")
    assert result["rows"] == 2
    loaded = load_history_as_of(tmp_path / "market.duckdb", date(2024, 1, 2), "a_share")
    assert loaded.loc[0, "industry_level1"] == "科技"


def test_importer_dry_run_does_not_create_database(tmp_path):
    source = tmp_path / "history.csv"
    pd.DataFrame([{
        "market": "a_share",
        "symbol": "000001",
        "effective_from": "2020-01-01",
        "effective_to": None,
        "status": "active",
        "source": "vendor",
        "snapshot_id": "s1",
    }]).to_csv(source, index=False)
    assert main(["--input", str(source), "--db", str(tmp_path / "market.duckdb"), "--dry-run"]) == 0
    assert not (tmp_path / "market.duckdb").exists()
```

- [ ] **Step 2: Run the focused tests to verify they fail**

Run: `./.venv/bin/python -m pytest etf_strategy/tests/test_universe_history.py -q`

Expected: FAIL because import and as-of query functions are not implemented.

- [ ] **Step 3: Implement import and query behavior**

Read `.csv` with pandas and `.parquet` with `read_parquet`, apply the validator, attach CLI-provided source/snapshot values only when the input omits them, and write through a temporary registered relation. `replace_snapshot=True` deletes only the exact `(source, snapshot_id, market)` rows before inserting. The as-of query selects intervals covering the requested date and uses `ROW_NUMBER() OVER (PARTITION BY market, symbol ORDER BY effective_from DESC)` to return one row per instrument.

- [ ] **Step 4: Implement the CLI output**

Print input row count, market/symbol counts, interval conflicts, missing industry count, and the exact database/snapshot written. In dry-run mode print the report and exit without opening a writable database.

- [ ] **Step 5: Run importer and store tests**

Run: `./.venv/bin/python -m pytest etf_strategy/tests/test_universe_history.py -q`

Expected: PASS.

### Task 3: Connect point-in-time snapshots to the research adapter

**Files:**
- Modify: `alphalab/research/engine.py:DuckDBMarketDataAdapter, InMemoryMarketDataAdapter, HistoricalResearchLab.run`
- Modify: `alphalab/research/review.py:ReviewState.summary`
- Test: `alphalab/tests/integration/test_research_pipeline.py`
- Test: `alphalab/tests/integration/test_research_review.py`

**Interfaces:**
- Adds `ResearchDataAdapter.load_universe_as_of(as_of: date, market: str, symbols: Sequence[str] | None = None) -> pd.DataFrame`.
- `DuckDBMarketDataAdapter.load_universe_as_of` reads `market_universe_history`; `InMemoryMarketDataAdapter.load_universe_as_of` exposes temporal columns already present in its fixture.
- `HistoricalResearchLab.run` merges the selected snapshot before scoring and records `universe_snapshot_id`, coverage counts, and missing metadata in diagnostics and manifest.

- [ ] **Step 1: Write failing integration tests**

```python
def _write_history_db(tmp_path, *, include_history):
    db_path = tmp_path / ("history.duckdb" if include_history else "without-history.duckdb")
    con = duckdb.connect(str(db_path))
    con.execute("""
        CREATE TABLE market_ohlcv (
            market VARCHAR, symbol VARCHAR, timeframe VARCHAR, ts TIMESTAMP,
            trade_date DATE, open DOUBLE, high DOUBLE, low DOUBLE, close DOUBLE,
            volume DOUBLE, amount DOUBLE, adjusted BOOLEAN, adjustment VARCHAR
        )
    """)
    con.register("bars", _bars())
    con.execute("INSERT INTO market_ohlcv SELECT * FROM bars")
    if include_history:
        con.execute("""
            CREATE TABLE market_universe_history (
                market VARCHAR, symbol VARCHAR, effective_from DATE, effective_to DATE,
                status VARCHAR, name VARCHAR, industry_level1 VARCHAR,
                industry_level2 VARCHAR, industry_level3 VARCHAR,
                source VARCHAR, snapshot_id VARCHAR, source_recorded_at TIMESTAMP
            )
        """)
        con.execute("""
            INSERT INTO market_universe_history VALUES
            ('a_share', 'STK011', '2020-01-01', NULL, 'active', '示例股份', '科技', NULL, NULL, 'vendor', 'vendor-2026-01', '2026-01-01')
        """)
    con.close()
    return db_path


def test_point_in_time_run_uses_history_table_industry_and_snapshot_id(tmp_path):
    db_path = _write_history_db(tmp_path, include_history=True)
    report = HistoricalResearchLab(DuckDBMarketDataAdapter(db_path), runs_dir=tmp_path / "runs").run(
        ResearchSpec(requested_date="2025-07-01", universe_mode="point-in-time")
    )
    assert report.candidate_table.set_index("symbol").loc["000001", "industry"] == "科技"
    assert report.diagnostics["universe"]["snapshot_id"] == "vendor-2026-01"


def test_point_in_time_run_fails_closed_when_history_coverage_is_missing(tmp_path):
    db_path = _write_history_db(tmp_path, include_history=False)
    with pytest.raises(ValueError, match="point-in-time universe"):
        HistoricalResearchLab(DuckDBMarketDataAdapter(db_path), runs_dir=tmp_path / "runs").run(
            ResearchSpec(requested_date="2025-07-01", universe_mode="point-in-time")
        )
```

- [ ] **Step 2: Run the focused research tests to verify they fail**

Run: `./.venv/bin/python -m pytest alphalab/tests/integration/test_research_pipeline.py -q`

Expected: FAIL because the research adapter has no as-of universe method and does not consume `market_universe_history`.

- [ ] **Step 3: Implement adapter and engine integration**

For point-in-time runs, query the history table using the resolved signal date, require at least one valid active snapshot row, merge `name` and industry fields into the bars, and apply the interval/status eligibility before invoking the plugin. Preserve the existing fixture path where temporal listing columns are attached directly to in-memory bars. Keep observed-history behavior unchanged.

- [ ] **Step 4: Record diagnostics and review summary fields**

Add `snapshot_id`, `history_rows`, `eligible_symbols`, `missing_symbols`, and `industry_missing_symbols` under `diagnostics["universe"]`; expose `universe_mode` and the same snapshot summary from `/api/summary`. Never include future intervals in the returned candidate frame.

- [ ] **Step 5: Run research and review tests**

Run: `./.venv/bin/python -m pytest alphalab/tests/integration/test_research_pipeline.py alphalab/tests/integration/test_research_review.py -q`

Expected: PASS, including the existing observed-history and inline listing-date tests.

### Task 4: Document the iteration prerequisite and verify the full workflow

**Files:**
- Modify: `alphalab/README.md`
- Modify: `AlphaLab 历史截面因子研究与组合回测 Spec.md`
- Test: `alphalab/tests/integration/test_research_pipeline.py`

**Interfaces:**
- Documents the exact import command, required input columns, interval semantics, and the distinction between exploratory observed-history and formal point-in-time runs.

- [ ] **Step 1: Add CLI contract and command-level test**

```python
from etf_strategy.scripts.import_universe_history import main as import_history_main


def write_valid_history_csv(tmp_path):
    source = tmp_path / "history.csv"
    pd.DataFrame([{
        "market": "a_share",
        "symbol": "000001",
        "effective_from": "2020-01-01",
        "effective_to": None,
        "status": "active",
        "source": "vendor",
        "snapshot_id": "vendor-2026-01",
    }]).to_csv(source, index=False)
    return source


def test_import_history_cli_reports_snapshot_and_coverage(tmp_path, capsys):
    source = write_valid_history_csv(tmp_path)
    assert import_history_main(["--input", str(source), "--db", str(tmp_path / "market.duckdb"), "--source", "vendor", "--snapshot-id", "vendor-2026-01"]) == 0
    assert "vendor-2026-01" in capsys.readouterr().out
```

- [ ] **Step 2: Run the command-level test to verify it fails**

Run: `./.venv/bin/python -m pytest alphalab/tests/integration/test_research_pipeline.py::test_import_history_cli_reports_snapshot_and_coverage -q`

Expected: FAIL until the documentation-facing import path is wired and the test fixture is added.

- [ ] **Step 3: Update the runbook**

Document a sample schema with concrete columns, show `--dry-run` before writing, show the `research run --universe-mode point-in-time` invocation, and state that the current production database has no historical interval rows until an external source is imported.

- [ ] **Step 4: Run all tests and static checks**

Run: `./.venv/bin/python -m pytest -q && ./.venv/bin/python -m compileall -q alphalab etf_strategy && node --check alphalab/research/static/app.js`

Expected: all tests pass and both Python/JavaScript checks exit successfully.

- [ ] **Step 5: Perform a real read-only smoke check**

Run the importer in `--dry-run` mode against a supplied fixture, query the resulting temporary database with `load_history_as_of`, and run one point-in-time research experiment. Confirm the manifest contains the snapshot identity and that an observed-history run still reports its exploratory label.

## Known Scope Gaps After This Plan

- The plan creates a trustworthy ingestion/query path but does not invent a historical source dataset. A formal point-in-time study remains unavailable until a vendor or exported CSV/Parquet snapshot is supplied.
- Industry classification is versioned only when the input source provides effective intervals; current industry fields are not retroactively treated as historical truth.
- Generic factor plugin loading, benchmark instrument selection, and UI run selection remain separate follow-up work.
