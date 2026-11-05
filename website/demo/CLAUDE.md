# demo — Public console adapter

## Design decisions
- **Actions follow owner rules** — validate person edits and project schedules before writes; remote actions return the local translated demo refusal.

## What's done well
- **DOM replies use current contracts** — showcase scenarios load the unchanged console with the adapter and validate successful replies against the service models.

## Pitfalls
- **Names fold upper-then-lower** — JS has no casefold, and plain lowercasing lets Straße and STRASSE coexist where the service refuses them; the fleet name map is computed from people so renames reach every screen.
- **Refill boundaries use calendar rebasing** — shift recorded `next_reset_at` with traffic periods, or direct person and page reads drift by elapsed days and hours before enforcement runs.
- **Schedule validation precedes preview and writes** — refuse unknown first use and activation or renewal beyond the generated finite year; an exhausted renewal cap may legitimately have no next renewal.
- **Token preview and issuance share validation** — use generated request bounds and the catalog's owner delegation flag; preview permissions contain only capability and labels, and refusals create no token or event.
- **Share assets never own revocation history** — start from the recorded inactive view, keep status placeholders outside access-name filters, and carry receipts across key changes while clearing explicitly restored credentials.
- **Undo compares enforced reviewed fields** — restore only changed rows and compare their complete before/after projections; limited or expired status is valid, and unchanged people must never fence undo.
- **Recording lookups use own names** — prototype names such as `constructor` and `toString` are valid people; ignore inherited properties when choosing credential templates and keep rotation flags in a null-prototype map.
