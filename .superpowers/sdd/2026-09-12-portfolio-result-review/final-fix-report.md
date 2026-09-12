# Final fix wave — Portfolio result review

## Scope

This bounded wave addresses the four Important findings in `final-review.md`.
The saved run, manifest, NAV artifacts, provider data, financial engine, and
backtest paths were not changed. The untracked `docs/acceptance/` and
`alphalab/reports/` trees were left untouched.

## Public contract changes

- Summary fallback now reads `task.result.manifest.portfolio_performance` by
  the selected `portfolio_id` and `horizon`. An absent selected identity
  yields an unavailable model; it cannot inherit the primary summary. A
  completed fallback is labelled liquidated only with `LIQUIDATED`, or with
  affirmative ending evidence when the saved ending holdings list is empty.
- Ending holdings now use their saved `shares`, `market_value`, and
  `unsettled` fields in a separate table schema. Empty ending holdings with
  unknown evidence say that the ending holdings/settlement evidence was not
  provided and do not claim post-liquidation cash semantics.
- The chart controller caches period aggregation per metric and period and
  uses controller-local date indexes for NAV rows, events, and period bars.
  Weekly/monthly hover callbacks therefore reuse the frozen transform rather
  than aggregating the full NAV series for each pointer event.
- The candidate endpoint now returns `industry_info` together with lazy
  enriched `industries`; `app.js` applies both after the candidate response,
  refreshing the existing industry options and quality badge while retaining a
  valid selected filter.

## Verification

Focused RED regressions were observed before the corresponding fixes:

```text
node --test alphalab/tests/integration/test_portfolio_review_ui.cjs alphalab/tests/integration/test_portfolio_review_charts.cjs
24 passed, 4 failed
```

The candidate API regression then passed after its endpoint contract was
added:

```text
/opt/miniconda3/bin/python -m pytest -q alphalab/tests/integration/test_research_review.py -k candidates_endpoint_returns_lazy_industry_options_and_quality
1 passed, 15 deselected in 1.10s
```

The final affected JavaScript check was run once after all changes:

```text
node --check alphalab/research/static/app.js
node --check alphalab/research/static/portfolio-review.js
node --check alphalab/research/static/portfolio-review-charts.js
node --test alphalab/tests/integration/test_portfolio_review_ui.cjs alphalab/tests/integration/test_portfolio_review_charts.cjs alphalab/tests/integration/test_review_ui.cjs alphalab/tests/integration/test_wizard_ui.cjs
62 passed, 0 failed in 935ms
```

The existing full Python suite had already passed before this JavaScript and
review fix wave (`412 passed`); this wave ran only the focused candidate API
regression as requested.

## Follow-up bounded correction

The final scoped review identified one residual predicate: affirmative ending
evidence must not override a nonempty saved ending holdings list. The public
status contract now maps `COMPLETE_LIQUIDATED` only when
`liquidation_status === "LIQUIDATED"` or when ending evidence is affirmative
and `ending_holdings.length === 0`. A nonempty ending list with raw
`COMPLETE` and no explicit liquidation status remains `COMPLETE`.

The focused regression was red before the predicate correction:

```text
node --test alphalab/tests/integration/test_portfolio_review_ui.cjs
✖ fallback keeps nonempty ending holdings from being labelled liquidated by evidence alone
  AssertionError [ERR_ASSERTION]: Expected values to be strictly equal:
  + actual - expected
  + 'COMPLETE_LIQUIDATED'
  - 'COMPLETE'
ℹ pass 11
ℹ fail 1
```

After the correction, the required UI test file passed:

```text
node --test alphalab/tests/integration/test_portfolio_review_ui.cjs
ℹ tests 12
ℹ pass 12
ℹ fail 0
ℹ duration_ms 306.827097
```
