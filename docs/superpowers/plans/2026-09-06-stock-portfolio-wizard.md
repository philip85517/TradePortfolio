# Stock Portfolio Wizard Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [x]`) syntax for tracking.

**Goal:** Execute Issue #5 with a recoverable data-first stock portfolio creation flow.
**Architecture:** A persistent workflow service coordinates drafts/jobs and delegates data/interval/portfolio semantics to an adapter around HistoricalResearchLab. A native HTML/JS wizard calls this service; read-only review remains separate and is served under run-specific URLs.
**Tech Stack:** Python stdlib HTTP/threading/SQLite, existing pandas/DuckDB/research engine, native browser JavaScript.
**Spec:** docs/specs/2026-09-06-stock-portfolio-data-first-wizard.md

## Global Constraints

- A 股日线、买入持有、不可变运行、独立本金、整手和成本；不写模拟交易账本。
- 日期/范围在先，数据 READY 才可配置；首版不承诺数据源一定完整。
- No silent data fabrication, date truncation or strict-to-exploratory fallback.
- Draft persistence, revision checks, readiness invalidation and idempotent jobs are required.

## Shared contracts

Scope JSON: market, start_date, end_date, selection_mode (manual/rule), symbols (list of codes), rule_version (fixed_v0), top_n, quality_mode (strict/exploratory).
Portfolio JSON: name, initial_cash, weighting (equal/score/custom), weights (code -> fraction), commission_rate, slippage_rate, max_single_weight, max_industry_weight, min_holdings.

Backend `WizardResearchBackend(db_path='auto', cache_dir=...)`:
- `inspect(scope) -> dict`: status READY/BLOCKED; issues list of {code,message,action}; dates; coverage; data_identity; serializable internal binding for execution.
- `prepare(scope, progress, cancelled) -> dict`: provision missing data then inspect, progress(message) records a stage, cancelled() permits cooperative cancellation.
- `preview(scope, portfolio, readiness) -> dict`: external holdings/cash/cost/diagnostic summary, raises ValueError on invalid configuration.
- `run(scope, portfolio, readiness, runs_dir) -> dict`: run_id plus saved manifest/summary; verify frozen data identity.

Workflow draft: id, revision, scope, portfolio, readiness (nullable), preview (nullable), task_id (nullable), created_at, updated_at. Task: id, draft_id, kind (prepare/run), status, stage, error, result, created_at, updated_at.
HTTP prefix `/api/wizard`: GET /drafts, POST /drafts, GET/PATCH /drafts/{id}, POST /drafts/{id}/check, /prepare, /preview, /run, GET /tasks/{id}, POST /tasks/{id}/cancel, GET /runs. Mutations send revision; run also idempotency_key. Responses are draft/task envelopes; errors {error,code,step}. GET /wizard serves wizard; GET /research/review/{run_id}/ serves frozen review with scoped relative APIs/assets. Portfolio preview and run never authorize via client-provided readiness.

### Task 1: Research adapter and interval semantics
Files: create alphalab/research/wizard_backend.py; modify engine only where necessary; create integration/test_wizard_backend.py.
- [x] Write and run deterministic tests of manual 2021–2025 interval, weekend bounds, per-stock gap and strict blockers before implementation.
- [x] Reuse existing data binding/provider seams, factor validation, portfolio/cost engine and frozen run persistence; implement the shared backend contract.
- [x] Test preview, execution, identity mismatch and explicit custom weight residual cash.

### Task 2: Persistent workflow orchestration and HTTP
Files: create alphalab/research/workflow.py, workbench.py; create integration/test_workflow.py; modify dashboard and CLI entrypoints.
- [x] Write failure tests for revision conflict, readiness gate, stale async completion, persistence/restart and idempotent submit.
- [x] Implement SQLite-backed draft/task store, serialized preparation, cancellable background tasks and backend injection; implement shared HTTP contract.
- [x] Serve wizard on current dashboard port and standalone `research workbench`; mount existing read-only review under a run URL.
- [x] Run real backend integration tests through HTTP in temporary data/run stores.

### Task 3: Wizard UI and review safety
Files: create alphalab/research/static/wizard.html, wizard.js, wizard.css; modify existing review app.js and dashboard homepage CTA.
- [x] Implement labeled steps, early scope inputs, explicit strict/exploratory state, data gates, stage polling, retry/cancel and draft recovery using shared HTTP contract.
- [x] Build executable preview, final summary, task progress and saved results navigation; no placeholder action buttons.
- [x] Guard old responses, immediately clear future views on selection mode switch, repair candidate selection/count and Forward Test navigation.
- [x] Verify JS syntax and perform browser testing against live workflow service.

### Task 4: Verification and documentation
- [x] Run full pytest suite, JS syntax and diff checks; independent final review and fix actionable findings.
- [x] Browser: empty-data gate, prepared fixture through run/save/reopen, refresh recovery, modified range recheck, failing provider feedback.
- [x] Real-source bounded check where available, recording source blockers separately from synthetic pass.
- [x] Update README startup instructions and report actual coverage/limitations.
