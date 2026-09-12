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
  completed fallback is labelled liquidated only with `LIQUIDATED` or an
  affirmative ending-evidence flag.
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
