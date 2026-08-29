# Wayfinder local tracker

This repository has no configured issue-tracker integration for the current
planning session, so this directory is the local Markdown tracker for the
historical factor research closure effort.

- `.wayfinder/map.md` is the canonical map (`wayfinder:map`).
- `.wayfinder/tickets/*.md` are child decision tickets.
- `status: open` means the ticket is on the route; `status: closed` means its
  resolution must be recorded in the ticket and indexed from the map.
- `blocked_by` is the local fallback for tracker-native dependencies. It uses
  ticket filenames, while all human-facing references use ticket titles.
- An assignee line is the claim mechanism when a ticket is being resolved.

This tracker is planning-only. It does not replace the implementation plans
under `docs/superpowers/plans/`.
