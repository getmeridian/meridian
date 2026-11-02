# demo — Public console adapter

## Design decisions
- **Actions follow owner rules** — validate person edits and project schedules before writes; remote actions return the local translated demo refusal.

## What's done well
- **DOM replies use current contracts** — showcase scenarios load the unchanged console with the adapter and validate successful replies against the service models.

## Pitfalls
- **Undo compares enforced reviewed fields** — restore only changed rows and compare their complete before/after projections; limited or expired status is valid, and unchanged people must never fence undo.
