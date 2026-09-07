# Data Recovery Phase 2 Implementation Plan

Use superpowers:subagent-driven-development and test-driven development. User approved docs/specs/2026-09-07-current-data-blockers-design.md.

Goal: fast cancellable inspection, explicit progress, phase-aware suspension research, and persistent unsupported-provider diagnostics.

Tasks:
- [x] Performance/backend: group data once; indexed history; streaming stable fingerprints; progress and cancellation callbacks; benchmark actual 4906-stock read-only scope and reuse version-guarded inspection.
- [x] Workflow/UI: async check task, cancel/recover check or prepare, heartbeat and actual elapsed time, distinct blocking versus informational diagnostics.
- [x] Suspension policy/engine: versioned policy, known pre-signal suspensions interpreted as zero activity in research view; no purchase at suspended entry (reserve weight as cash); holding stale valuation without fake fills; suspended exit retains unrealized valuation without sale costs.
- [x] Provider limitations: preserve requested/returned adjustment evidence, suppress ordinary repeat repair for same source identity, explicit reverify/reset mechanism.
- [x] Integrate, run regression suite and real scope benchmark, review, gracefully reload service and recheck existing draft. Preserve immutable source data and old results; retain formal PIT/delisting blockers.
