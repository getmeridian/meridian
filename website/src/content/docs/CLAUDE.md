# content/docs — Localized Markdown guides
## Design decisions
- Every public guide has en/ru/fa/zh parity, matching frontmatter and the shared sidebar. All command names and API operation tables are validated.
- People are service-owned in every locale: CLI add/remove are immediate, all commands read current display names, zero people is allowed, and `--proposal` is retired.
- The next reviewed apply discloses adoption of old version 4 people, preserves accounts and completes missing seeds after commit.
- Client guides distinguish single-person session cut-off from atomic bulk preview/commit and durable receipts; earlier transfers can continue without a cut-off.
- Setup drafts keep initial people outside topology; deploy ensures them through the service before verification and handoff, and restores print every changed service link.
- Architecture separates reviewed roles, grants, delivery and deployment state from live people, membership, private metadata and operator settings.
- Profile filters name saved membership; aliases sharing an inbound share enforcement, and gain/loss reviews explain flow-block boundaries.
- Delivery policies and app rules describe finite literal matches, ordered resolution, private person overrides, unsupported formats and transient testing without request storage.
- Tokens describe frozen explicit grants and presets, protected metrics, conditional cut-off rights, credential views and private metadata separately.
- Capacity remains bounded per-node evidence with receipt freshness and null/zero distinctions; personal privacy controls govern device/history storage independently.
- Account schedule guides distinguish first-use durations, rolling reset anchors, bounded renewals and absolute expiry without activation on page reads.
- Panel guides describe contextual record lists, person summary/tabs, guarded inline edits, service-rendered previews and Simple/Detailed navigation.
## What's done well
- CLI catalogs, command examples and four localized API tables derive from the current public contracts.
- Connection-page instructions describe the real subscription methods and shared assets without remote templates or automatic external requests.
- Privacy guides disclose what is stored and deleted, with counts-only anonymous views and content-free administrative events.
## Pitfalls
- Never describe people as declarations or tell users that add/remove need apply; older configuration ownership exists only in the disclosed adoption transition.
- Do not document removal proposals as actionable; retired flags provide the replacement command and batch previews write nothing.
- Describe exact-username precedence and ambiguity refusal against current service names, including panel renames and non-Latin display names.
- CLI and console are two surfaces of the same accounts; no console-only ownership badge belongs in current backend descriptions.
- Keep localized headings and links aligned; translated headings require translated anchor links.
- Regenerate API operation tables when route metadata changes, and validate them against the committed OpenAPI document.
- Names and subscriptions are distinct identifiers; shared links use the public page path, never the administrator's secret ingress.
- Reissue invalidates previous pages, subscriptions and credentials; rotation preserves subscription identity and registrations but changes connection keys.
- A test without usable people is unavailable evidence (exit 3), not a failed network test or a reason to degrade fleet health.
- Use documented scopes and conditional grants rather than inventing requirements from a UI control's visibility.
- Fleet import describes topology only in this phase; people export/import seeds have separate design decisions.
- Mermaid sources require generated SVG updates before changing diagrams; do not change a block without rendering its checked-in artifact.
