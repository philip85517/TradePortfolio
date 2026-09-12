# Task 1 report — frozen portfolio review projection

## Status

Implemented the additive, versioned `review` object on the existing read-only
portfolio detail response. The projection consumes the saved manifest and
CSV frames only. It does not open the market data adapter, recalculate frozen
metrics, write artifacts, or call a provider.

## TDD and verification

The focused test was first run before the projection module existed and failed
during collection with:

```text
ModuleNotFoundError: No module named 'alphalab.research.result_projection'
```

After implementation, the focused projection/HTTP test passed:

```text
/opt/miniconda3/bin/python -m pytest -q alphalab/tests/integration/test_result_projection.py
3 passed in 0.67s
```

The required full Python suite was then run once before commit:

```text
/opt/miniconda3/bin/python -m pytest -q alphalab/tests
406 passed in 98.26s (0:01:38)
```

The projection and modified Python modules also pass `py_compile`, and the
focused tests cover RED→GREEN for the projection, missing-DB portfolio HTTP,
portfolio/horizon isolation, first-close unit NAV, null and zero NAV values,
stale metadata, explicit event chains, derived initial fills, initial
non-fills, and JSON-safe output.

## API specimen for Task 2/3

`GET /api/portfolio?portfolio_id=strategy` keeps every existing response
field and adds `response.review`:

```json
{
  "schema_version": 1,
  "run_id": "research-20260910T182300883182Z-f5a894775d",
  "portfolio_id": "strategy",
  "name": "我的股票组合",
  "initial_cash": 100000.0,
  "scope": {
    "entry_date": "2023-01-03",
    "exit_date": "2025-12-03",
    "calendar_source": "baostock.query_trade_dates",
    "session_count": 707,
    "actual_date_range": ["2023-01-03", "2025-12-03"]
  },
  "quality_mode": "strict",
  "horizons": [706],
  "by_horizon": {
    "706": {
      "summary": "the complete frozen performance mapping",
      "status": {
        "code": "COMPLETE_LIQUIDATED",
        "label": "完成且清算",
        "explanation": "研究已完成，冻结证据显示期末持仓已全部清算。"
      },
      "nav": [
        {
          "date": "2023-01-03",
          "equity": 102748.94841765,
          "unit_nav": 1.0274894841765,
          "cumulative_return": 0.0274894841765,
          "daily_return": 0.0274894841765001,
          "drawdown": 0.0
        }
      ],
      "events": [
        {
          "id": "event-…",
          "chain_id": "chain-…",
          "date": "2025-06-13",
          "action": "SELL",
          "symbol": "002336",
          "name": "…",
          "filled": true,
          "shares": 500.0,
          "price": "frozen research total-return price",
          "net_cash": 304.75604575,
          "reason_code": "termination_decision",
          "reason_text": "正式退市决定触发退出",
          "price_basis": "adjusted_total_return_anchored_at_raw_entry",
          "source_kind": "frozen_execution_event"
        }
      ],
      "chains": [
        {
          "chain_id": "chain-…",
          "trigger_event_id": "cninfo:002336:2025-039",
          "event_ids": ["event-…"],
          "summary": "正式退市决定公开 → 公告明确复牌前等待 → 首个可成交日退出 → 选择替补 → 预算不足一手，保留现金"
        }
      ],
      "initial_holdings": "frozen positive-share portfolio rows",
      "ending_holdings": [],
      "capabilities": {
        "daily_nav": true,
        "weekly_monthly_aggregation": true,
        "session_list": "707 frozen sessions",
        "transaction_evidence": true,
        "comparable_frozen_benchmark": false,
        "missing_session_dates": []
      },
      "unavailable_reasons": []
    }
  }
}
```

The real fixture produced 707 NAV rows, nine derived initial `BUY` events,
the persisted `002186` `INITIAL_NOT_FILLED` diagnostic, nine persisted
`SELL` events, and no replacement `BUY`. The `300204` `SELECT` and
`CASH` events remain decisions linked to the explicit frozen trigger and
rank cutoff. A derived initial fill keeps its frozen entry price (which already
includes buy slippage) and leaves fees/net cash null with an unavailable reason
when the frozen cost contract is absent. Stored sale prices retain the
`adjusted_total_return_anchored_at_raw_entry` basis.

Task 2 should use `by_horizon[h].nav` as the daily source and consume
`capabilities.session_list`, `session_source`, `valid_date_range`,
`missing_session_dates`, and per-row stale fields for its single weekly/monthly
observed-close transform. No weekly/monthly candles are generated in Python.
`summary` is the original frozen mapping; the projection does not recompute
return, drawdown, or other risk metrics.

## Read-only and compatibility notes

- `portfolio_detail` retains the previous fields and appends `review`.
- Missing `nav.csv` and missing market DB no longer block portfolio/summary/
  static reads when manifest evidence is sufficient; missing NAV is surfaced
  as unavailable rather than zero.
- Stock chart requests still fail with a client error when no DB is bound.
- Artifact hash validation still rejects traversal/absolute paths and modified
  hashed files. The loader preserves leading-zero symbols with string dtypes.
- Existing initial portfolio rows are treated as initial holdings only when
  actual positive shares are present. Candidate rows, target weights, and
  non-fill rows never become buys.

## Files

- `alphalab/research/result_projection.py` — frozen projection, event/status/
  capability normalization, deterministic IDs and chains.
- `alphalab/research/review.py` — optional frozen NAV/DB loading, additive
  response projection, and DB-gated stock access.
- `alphalab/research/workbench.py` — permits saved portfolio review routes
  without a market DB while retaining the stock DB boundary.
- `alphalab/research/__init__.py` — exports the projection entry points.
- `alphalab/tests/integration/test_result_projection.py` — focused projection
  and HTTP coverage.
- `.superpowers/sdd/2026-09-12-portfolio-result-review/task-1-report.md` —
  this report.

## Limitations for downstream consumers

The projection reports capability and evidence metadata but intentionally does
not aggregate weekly/monthly candles, render charts, add exports, or modify
the wizard UI. Consumers must preserve null NAV rows and must not interpret a
derived initial event's missing cost fields as zero. A benchmark is marked
available only when a comparable frozen benchmark mapping is present; the
real fixture therefore reports it as unavailable.
