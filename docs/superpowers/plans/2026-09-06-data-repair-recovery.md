# Data Repair Recovery Implementation Plan

> Use superpowers:subagent-driven-development to implement independent tasks and review results.

**Goal:** Make data repair actionable and recoverable without relaxing research gates.
**Architecture:** Structured diagnostics generate server-owned repair actions; backend executes validated shards; workflow retains immutable attempt evidence; UI groups issues and derives actions from repair plans.
**Tech Stack:** Python/pandas/DuckDB/SQLite, vanilla JavaScript, pytest and node:test.
**Spec:** docs/specs/2026-09-06-data-repair-recovery-design.md (approved).

## Constraints
Preserve original databases and frozen runs. Unknown trading status stays unknown. No automatic exploratory downgrade or candidate exclusion. Do not implement suspension valuation or delisting settlement.

## Tasks
- [x] Backend diagnostics: add data_readiness.py; test date-specific missing/zero/status classifications, stable identities and repair plans using real backend inspect fixtures, then implement. inspect returns repair_plan {plan_id, actions:[{symbol,kind,start,end}], executable_count}, issues with resolution (download/verify/user/unsupported), symbol, phase, date_ranges, evidence. Legacy issue fields remain.
- [x] Repair execution: add data_repair.py helpers for validated atomic publication and attempt journals; backend executes only actionable symbols, metadata bootstrap remains available. Tests cover invalid response not replacing valid cache, repeated unchanged data, partial retries and plan staleness.
- [x] Frontend: grouped issues, paged coverage, export, action labels and recovery; node tests first, then browser verification. Use repair_plan executable_count; absent plan retains legacy compatibility.
- [x] Workflow: enforce plan identity when provided; reject empty repair tasks for new schema; preserve partial progress and structured failures. Test terminal retry and stale submissions.
- [x] Verify: targeted backend/workflow/UI tests, broader suite, independent review, record real-provider/network limitations and final status.

## Execution record
- Baseline remained e5c2339; remote fetch was retried but GitHub 443 remained unreachable.
- Independent frontend and publication-helper implementation followed by independent backend review.
- Review corrections: preserve quantities both in existing cache and original-source overlays; persist action records before next action; retry/audit bootstrap; preserve child transport classification.
- Ruling: missing quantity values coalesce only from the same stock/date's verified finite values; finite zero remains authoritative. This preserves original facts without inventing trading activity.
- Original-source status smoke and full-report data repair are distinct from deterministic fixture acceptance. No user database or active service was modified.
