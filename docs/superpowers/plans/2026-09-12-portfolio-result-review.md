# Portfolio result review implementation plan — Issue #7

Spec: docs/specs/2026-09-12-portfolio-result-review.md (all P0/P1 approved).
Branch: codex/portfolio-result-review. Base: eab89cb.
Workspace: existing 0ec3 linked worktree. Implementers: Luna max; controller reviews and performs browser acceptance.

## Global Constraints
- Original 8787 wizard, original persistent 1deb data. Never touch wrong Portfolio matches.
- Read-only frozen runs; no backtest, source refresh, financial logic change, or frozen file writes.
- Day-close NAV / own initial cash; no fabricated daily OHLC. Week/month candles are first/last/max/min observed day-close NAV, explicitly labelled.
- Native Lightweight Charts 4.2.3, locally served with license/notice; accessible/offline fallback.
- Task boundaries below cover all spec stories; no pushes or merges in implementation. Commit only owned files after appropriate tests, excluding alphalab/reports.
- Prefer existing ReviewState.portfolio_detail HTTP boundary. No second results database.

### Task 1: Frozen portfolio review projection and read-only HTTP contract

Files: add alphalab/research/result_projection.py; modify alphalab/research/review.py and workbench.py; add alphalab/tests/integration/test_result_projection.py and relevant HTTP tests. Do not edit UI files.

Read the full approved spec, especially data semantics, execution events, states and testing. Implement these as one additive versioned `review` object in existing `portfolio_detail` output; keep all previous fields intact.

Contract (plain JSON):
- `review.schema_version=1`, `run_id`, `portfolio_id`, `name`, `initial_cash`, `scope`, `quality_mode`, `horizons`, `by_horizon` keyed by horizon string.
- Each horizon: `summary` (existing frozen metrics), `status` (code, Chinese label, explanation), `nav` (date, equity, unit_nav, cumulative_return, daily_return, drawdown, optional stale flags), `events`, `chains`, `initial_holdings`, `ending_holdings`, `capabilities`, `unavailable_reasons`.
- `capabilities` truthfully describes daily NAV, weekly/monthly aggregation, transaction evidence, comparable frozen benchmark and available fields; no invented evidence.
- Events: stable `id`, nullable `chain_id`, date, action, symbol, name, filled flag, nullable shares/price/commission/slippage/net_cash/budget, reason_code/reason_text, rank_cutoff, source_url, evidence/source kind and price_basis. Preserve original fields where useful, no hidden rounding. Chains group same triggering event across sell/select/cash/defer, summary explains trigger→execution→replacement outcome. Do not infer causality from mere date proximity.
- Normalize all stored event types with Chinese explanations. Fill counts exclude decisions/defer/cash. Existing initial portfolio rows with positive actual shares can reconstruct initial fills, explicitly source `derived_frozen_entry`; deduplicate if explicit initial fills already exist. Never turn candidates/target weights/non-fill rows into buys. Recover initial non-fills from frozen diagnostics/preview only if actually persisted; do not query latest sources.
- Initial entry price already includes buy slippage: do not apply it again. Derive cash/fees only when frozen cost contract proves them; otherwise null with reason. Existing event sell prices are research total-return prices, clearly labelled.
- Week/month aggregation need not be Python: Task 2 owns the single pure JS transform. Supply trusted session list (from frozen metadata when available), valid date range and missing/stale metadata needed to identify holes. Unknown session coverage => disclose unknown completeness, do not assert complete candles.
- Preserve null NAV and unknown intervals so charts do not connect across them. No rebasing to first close. Do not recalculate frozen risk metrics.
- Status covers complete/liquidated, open valued positions, unsettled incomplete result, empty/insufficient evidence. Missing metric is null+reason, not zero. Ending holdings only from frozen ending evidence; original portfolio is initial holdings.
- Portfolio-only result/summary/static routes must not require market OHLC DB when existing frozen NAV/manifest suffice. Stock price endpoints still reject missing DB appropriately. Path validation/traversal protections remain.
- Do not load data unnecessarily for static assets; no user-controlled arbitrary filesystem serving.

TDD and acceptance:
1. Add focused tests that fail for new projection and missing-DB portfolio HTTP access before implementation.
2. Test distinct portfolios/horizons, initial cash != first close, nullable gaps/stale/unsettled, true zeros, no events legacy runs, derived initial fills, non-fill exclusion, stable IDs, chain grouping, nullable cost/evidence, names safe strings.
3. Test HTTP reads use saved results only and frozen manifest/NAV hashes unchanged; no provider calls, no DB required for summary/NAV, DB still required for stock prices.
4. Real read-only fixture when available: run research-20260910T182300883182Z-f5a894775d has 707 points, initial cash100000, first equity102748.94841765001, last62158.38008203717, return-.3784161991796283, maxdrawdown-.5242825712289458. Nine initial fills; nine stored sells; no replacement BUY; SELECT300204 then CASH lot_budget. 002336 sale500 on2025-06-13 net304.75604575. Do not make tests require user's real data in CI.
5. Run focused tests then alphalab/tests once before own commit. Report API specimen and any limitations to Task 2/3 consumers.

### Task 2: Shared chart transforms and local TradingView-style chart component

Files: add alphalab/research/static/portfolio-review-charts.js, vendor/lightweight-charts.standalone.production.js + LICENSE/NOTICE; minimally extend static route allowlist in workbench.py/review.py for these named assets; add alphalab/tests/integration/test_portfolio_review_charts.cjs. Do not replace wizard rendering yet.

Consumes Task1's `review.by_horizon[h]` data; read its report for exact output shape. Public browser global/CommonJS module `PortfolioReviewCharts`:
- Pure transforms for date-range filtering, unit/equity/return data, weekly/monthly observed-close OHLC, grouped markers. Export for deterministic tests.
- `create(container, {data, initialCash, benchmark, onSelectEvent, onHover})` returns controller with `update({metric,period,range,selectedEventId})`, `locateEvent(eventId)`, `resize()`, `destroy()`; if adjustment is necessary, record and provide precise consumer contract.
- Component owns main and drawdown child containers and optional fallback state; UI Task3 owns controls and passes updates. One shared time range, guarded bidirectional sync, safe cleanup/ResizeObserver; hidden-panel reappearance resizes. No global data cache that crosses runs/portfolios.
- Defaults metric unit_nav, period1D, range all. Support equity and cumulative_return, range bounds, 1W/1M candle mode for unit/equity, return always line. 1D is line/area, not candles.
- Weekly/monthly grouping by actual calendar periods, open first observed close, close last, high/low over observed closes. Include gap/completeness warning when expected session missing. Last partial period labelled, do not label every ordinary Friday/month-end as partial. Do not fabricate calendar sessions.
- Retain holes/nulls: split line segments or otherwise prevent bridging unknown values. Known stale valuations visibly annotated. Unsettled end has no fake zero/suffix.
- Initial reference1.0/cash/0 not fake extra NAV date. Daily tooltip date + available equity/NAV/return/drawdown/events. Do not recompute full-horizon summary on zoom.
- Mark actual fill BUY/SELL distinctly from decisions/defer/cash. Same date/period grouped; selecting one resolves stable event ID, multiple events expose accessible selector/list usable by Task3. Non-trading announcement dates stay factual; event trace never relocates a transaction to a wrong session.
- Local official Lightweight Charts4.2.3 API only (do not use v5). Source via known npm/jsdelivr vendor endpoint, verify version/content, retain relevant license and NOTICE/TradingView attribution. Network system proxy may be http://127.0.0.1:33210. Do not install a new commercial chart provider.
- If library unavailable, readable SVG/table fallback with gap semantics, event selection and core values; never blank. No data URLs needing external providers, no request of latest market data.

TDD and acceptance:
- Focused red/green tests for first-close normalization, weekly/monthly candles, partial/missing periods, real zero vs null, date alignment, event grouping/selection, cumulative-return line mode, different run data isolation.
- Use a meaningful chart API test double for lifecycle callbacks/sync loops/cleanup; do not assert only library existence or source substrings.
- Verify vendor route loads with no frozen OHLC and blocks path traversal. Existing wizard/review JS tests remain green.
- Chart performance fixture5000 points/1000events: avoid repeated full renders on hover; controller owns bounded list/group events. Report measuring approach for later browser acceptance.
- Commit own files and precise public API docs in report. No service restart/browser operation; controller performs that after Task3.

### Task 3: Original wizard portfolio review UI, event workflow, exports and acceptance

Files: add alphalab/research/static/portfolio-review.js and portfolio-review.css; modify wizard.html/wizard.js/wizard.css and named static allowlist as needed; add alphalab/tests/integration/test_portfolio_review_ui.cjs; extend existing UI/HTTP tests only where behavior changes. Add documented acceptance evidence after tests. Consume Task1/Task2 contracts, do not duplicate projection/chart financial semantics.

Implement ALL remaining approved spec user-visible behavior:
- At original fifth step load frozen portfolio detail from existing read-only run endpoint and render review in place. Prior stages unchanged. Use result fallback summary if detail load fails; distinct retry-results button never reruns strategy. Switching task/run/portfolio/horizon must invalidate stale requests/selections. Preserve original draft refresh restoration.
- Completed result mode has compact header/steps/status (old layout consumes330px header+230px completion). First screen at1440x900 must show title, KPIs and main chart. Do not merely append chart below old notices. Keep main chart >=360px desktop/drawdown~120px, use space efficiently;390px layout has no page overflow.
- Main title portfolio/run interval, compact status separate task success vs liquidation. Main KPIs totalreturn/profitloss/endingequity/maxdrawdown; secondary initialcash/annualizedreturn/costs/cash/valuation, missing reasons instead of unexplained dashes. Full horizon clearly labelled despite zoom.
- Factual summary derived only from events. Highlight key delisting chain and cash outcome. Expanded “research settings/evidence” holds raw diagnostics rather than front-page JSON. Include quality mode, requested/actual dates, price basis/cost assumption, relevant limitations.
- Toolbar metric unitNAV/equity/return, periods1D/1W/1M, all/last1/3/6months/1year relative to research end, custom dates clipped, reset. Mark aggregated day-close candle semantics persistently. Benchmark switch only if compatible frozen data exists; else explanation. Multi-portfolio/horizon explicit selector when present.
- Below charts tabs “交易与调仓”“初始与期末持仓”“研究设置与证据”. Accessible keyboard behavior. Event list filters symbol/action/reason/date, virtual or bounded/paginated render for1000events. True fills distinct from decisions, terminal exits distinct from rebalances. No invented win-rate/contribution.
- Chain summary trigger→constraints→execution→selection→outcome. Continuous defer collapsed with date interval/count, expandable detail. Side detail desktop/below mobile. Table→marker and marker→row selection must both work, same-date chooser, clear selection/Escape. Details include actual/source dates, shares, price4decimals, money2decimals, costs/netcash/budget/rankcutoff/source link/evidence; no inferred fresh prices. Unknown evidence explained.
- Show initial holdings (not current) and ending positions/cash/unsettled semantics, including initial non-fill when evidence available. Existing actual fixture should explain002186 initial nonfill,002336500sell,300204notbought. If legacy evidence unavailable state why instead of inventing.
- Offline fallback accessible chart/table; independent loading/empty/failure/retry states; charts resize when panels shown; no blank result if vendor fails. All dynamic strings safe text/HTML escaping.
- NAV and event CSV downloads from same frozen projection; include run/portfolio/horizon/basis/filter scope and original precision. Filtered vs all export explicit. Prevent formula injection in free-text cells. Pure exports tested; real numeric negatives remain numeric values not misleading text.
- Proper local asset routing and attribution. No current data network dependencies. Focus styles, colour+text signs/actions, readable names/code, no horizontal page overflow.

TDD and acceptance:
1. UI tests assert observable state transitions and rendered values, not only substrings or mocked function calls. Cover stale request, reload restoration, filter→selection, failed retry not rerun, full summaries unchanged by chart range, null+reason, all states, export scope/safety and frozen identities.
2. Run all alphalab Python tests and all affected JS tests once after focused tests. Controller will verify real original UI via CUA, not a separate Portfolio app.
3. Browser checklist for controller: original run loads; firstscreen screenshot; metric/period/range; marker/list selection; chain expand; initial/ending positions; exports; refresh;390px; vendor disabled fixture; no console errors; immutable source hashes same.
4. Real expected values in spec remain unchanged. No backtest required. Provide exact test commands and report consumer/rulings, no push/merge.
5. Performance measure:707points15stored events APIresponse→interactive target<=1s and local totalread target<=3s, plus synthetic5000points1000events; disclose actual environment/results rather than guess.
