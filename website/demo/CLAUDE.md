# demo — Public console adapter

## Design decisions
- **Actions follow owner rules** — validate person edits and project schedules before writes; remote actions return the local translated demo refusal.

## What's done well
- **DOM replies use current contracts** — showcase scenarios load the unchanged console with the adapter and validate successful replies against the service models.

## Pitfalls
- **Share assets never own revocation history** — start from the recorded inactive view, keep status placeholders outside access-name filters, and carry receipts across key changes while clearing explicitly restored credentials.
- **Undo compares enforced reviewed fields** — restore only changed rows and compare their complete before/after projections; limited or expired status is valid, and unchanged people must never fence undo.
- **Recording lookups use own names** — prototype names such as `constructor` and `toString` are valid people; ignore inherited properties when choosing credential templates and keep rotation flags in a null-prototype map.
