# TODO

## Meridian Direction

Meridian is becoming a structured installation and control system with multiple
clients on top, not just a CLI.

- **meridian-core** owns typed requests/results, schemas, validation, planning,
  redaction, events, and readable errors.
- **CLI** is one client. It parses arguments, prompts, renders terminal output,
  maps exits, and calls core/Engine contracts.
- **Meridian Studio** is the beginner-first client. It should guide a non-IT user
  through VPS setup, SSH key preparation, deploy, verification, and recovery.
- **Engine** exists only for executable localhost Studio: SSH, filesystem state,
  one-time secrets, operation execution, cancellation, and event replay.
- **UX is authoritative.** YAML, Pydantic models, schemas, and flags must adapt
  to the ideal user journey, not force users through storage-shaped forms.

The near-term proof is **deploy + verify + recover**.

## Current State

- Contracts export from Pydantic/core to `contracts/meridian/v1` and generated
  Studio types.
- pnpm is the website package manager, with one-week release-age guardrails and
  blocked exotic transitive dependencies.
- Pydantic input types validate IPs, server references, SSH users, ports, names,
  and human-readable API/CLI errors.
- Server onboarding supports title, IP, SSH user, SSH port, saved profiles,
  validation, and password-assisted key bootstrap through Local Engine.
- Deploy can target saved server profiles by ID/title/IP with SSH port metadata.
- Studio now uses a guided cockpit shell with a journey rail, focused task
  canvas, contextual help rail, command sheet for technical artifacts, accessible
  help popovers, static command fallback, and non-secret localStorage drafts.
- `meridian studio` now has a package-resource asset path for built Studio
  assets, and website builds fail on external runtime assets.
- Local Engine exposes localhost-only health, contracts, workflows, server
  actions, deploy dry-run, deploy operation start, operation status, event replay,
  result, and best-effort cancel.
- Topology direction is capabilities plus routing policy: a server may relay and
  also be an exit for regional traffic such as RU routes.

## Next Global Steps

### 1. Make Deploy Evidence Real

- Add post-deploy verification operations: SSH health, service status, open ports,
  generated connection page, panel reachability, Reality/Xray checks, and clear
  failure evidence.
- Show verification evidence in Studio as facts, not optimistic success text.
- Add retry guidance for common failures: DNS missing, firewall closed, sudo
  missing, stale host key, partial container setup, panel API unavailable.

### 2. Recovery And Safe Retry

- Model partial deploy states and recovery actions as typed operations.
- Add Studio recovery entry points: “deploy failed”, “lost local state”,
  “server exists but Studio does not know it”, and “connection page broken”.
- Keep every mutating recovery action behind a review step, operation ID, events,
  terminal result, and CLI equivalent.

### 3. Client Handoff

- Make client creation and sharing first-class after deploy: QR codes, deeplinks,
  hosted page preview, import instructions, rotation, disable, and update flows.
- Keep handoff self-hosted with no external runtime assets or telemetry.

### 4. Multi-Server Topology

- Replace one-purpose server thinking with capabilities and route policy.
- Add flows for adding relays, exits, and regional route exits by reference.
- Support examples like “RU traffic exits through this RU-capable relay” without
  exposing raw YAML as the primary UI.

### 5. Studio Product Polish

- Continue testing and polishing Studio as a low-stress cockpit: one current
  decision, contextual help, command sheet for JSON/CLI/parser tools, and clear
  recovery states.
- Add stronger browser tests for the full beginner journey, localStorage restore,
  static/local parity, and operation polling.
- Package built Studio assets with the Python distribution so `meridian studio`
  works without passing `--assets-dir`.

## Eight-Cycle Execution Harness

This section is the resume point for the current autonomous improvement loop.
Each cycle runs three repo-specific reviewers, one challenger, and one ambitious
UX brainstormer. Save P0/P1 findings here, execute the highest-leverage subset,
then advance the active cycle.

**Context7 status:** available through the installed `ctx7` CLI. Cycle 4 used
Pydantic docs; cycle 5 used FastAPI background-task/operation docs.

### Cycle 1 — Studio Information Architecture

Status: completed.

Theme: make Studio feel like a guided product surface instead of a dressed-up
form, while keeping static fallback and localhost Engine parity.

P0 feedback:

- Mode entry is unclear: Local Studio is required for save, validate, key
  bootstrap, and deploy, but the first screen does not make that boundary
  obvious.
- Journey progress is not evidence-gated: users can advance to deploy without
  valid details, SSH validation, key readiness, or a real dry-run.
- Static Studio fakes dry-run evidence by rendering generated fixtures as if
  they came from the current request.
- Saved-server deploy is broken: Studio sends `requested_server` plus empty
  `ip`/`user`, so Pydantic rejects the request before Engine can resolve the
  saved profile.
- The final step is operation progress, not real verification and client
  handoff.
- Cancellation is currently only a state flag; Studio presents it as stronger
  than Engine can guarantee.
- Engine errors lose structured recovery details when they cross the Studio
  adapter.

P1 feedback:

- Replace page-driven navigation with primary actions such as “Save and check
  SSH”; advance automatically only on success.
- Show server validation results as fact cards: reachability, authentication,
  sudo, detected OS, and next action.
- Make dry-run mandatory before Local Studio deploy; review should render the
  computed plan snapshot, not only form fields.
- Keep commands, schema names, event types, operation IDs, and fixtures in the
  command/debug sheet instead of the beginner path.
- Render human labels instead of raw field IDs such as `ssh_user` and
  `geo_block`.
- Add current-server identity and saved-server reuse earlier in the flow.
- Add paste-SSH-command autofill to reduce typing mistakes.
- Surface Engine health as capabilities, not only Local vs Static.
- Package built Studio assets with the Python distribution before calling Local
  Studio shippable.

Execution target:

- Completed first slice: fixed saved-server deploy request shape; added
  non-secret readiness flags; made static dry-run honest; added visible status
  cards, validation fact cards, typed Engine request errors, readable validation
  labels, SSH-command paste autofill, Local/Static mode gate, trust strip, dry-run
  gate before review/start, and cancellation state that no longer flips to
  success after a cancel request.
- Defer to later cycles: real process cancellation, packaged assets, operation
  resume/listing, and verified client handoff.

### Cycle 2 — Beginner SSH Onboarding

Status: completed.

Theme: reduce SSH/password/key setup confusion for non-IT users; make server
connection, key bootstrap, validation, and saved-server reuse feel obvious.

P0 feedback:

- Password-assisted key bootstrap can silently trust unknown SSH host keys before
  sending the provider password. Password auth must require an already trusted
  host key or an explicit fingerprint confirmation flow.
- Generated Meridian SSH keys can be verified during bootstrap but not used by
  CLI deploy, because profile `key_path` is lost when resolving saved servers.
- Password-only beginners are blocked before the password flow because the key
  step is gated behind passwordless SSH validation.

P1 feedback:

- Collapse save/validate into one “Check SSH access” action and move saved
  servers to the first screen.
- Make paste-SSH-command onboarding the default and parse more provider command
  shapes.
- Auto-generate server titles and avoid default-title collisions when adding
  multiple servers.
- Make key setup conditional: if SSH and sudo work, continue to deploy; if the
  user only has a password, show the one-time key install path.
- Remove or relabel password-auth disabling until deploy actually performs it.
- Render recovery actions from validation hints, not only free-text messages.
- Hide Local Engine manual bypasses or require evidence; static mode can keep
  explicit “I ran this command” confirmations.
- Preserve selected saved-server state across initial Engine list load.

Execution target:

- Completed first slice: password bootstrap now requires a trusted known_hosts
  entry before using the one-time password; password SSH and explicit identity
  SSH do not create reusable ControlMaster sockets; verified `key_path` is
  preserved through server profiles, registry compatibility, and CLI deploy
  resolution; missing sudo no longer marks a profile deploy-ready; the key step
  is reachable for password-only beginners after valid server details; Local
  mode hides manual bypasses; SSH command paste supports more provider formats;
  blank titles become safe auto-generated titles; saved servers appear on the
  first screen and survive initial Engine list loading.

### Cycle 3 — Engine Operations And Observability

Status: completed.

Theme: make every local Engine operation inspectable, cancellable where safe,
recoverable, redacted, and explainable after failure.

P0 feedback:

- Cancel is still advisory for the deploy subprocess; presenting it as stopped is
  misleading during mutating server work.
- Engine operation history is process-local only; Engine restart loses evidence.
- Studio must actually resume polling an active operation after page reload, not
  only store the operation ID.
- Repeated deploy starts can create multiple concurrent mutating deploys for the
  same target.

P1 feedback:

- Add `GET /api/v1/operations`, incremental event replay, source labels, and
  operation snapshots with phase, event counts, warnings, and errors.
- Replace Engine-local redaction with core redaction for free-form messages,
  hints, URLs, stderr, JWTs, and secret-looking fields.
- Add one-click redacted diagnostics for operation support/debugging.
- Route Studio failures to the relevant step surface and make status cards
  visually distinguish running, warning, failed, cancelled, and ok states.
- Parse pretty-printed JSON envelopes before falling back to JSONL.
- Operation endpoints should become typed/exported contracts, and incoming child
  events should be validated against `Event`.
- After deploy success, show verification facts instead of treating process
  completion as proof that VPN service works.

Execution target:

- Completed first slice: added operation listing, richer snapshots, incremental
  event replay, active-operation resume in Studio, duplicate running deploy guard
  per request target, core redaction for Engine events/results/errors, redacted
  operation diagnostics endpoint, visual status tones, correct failure surfaces,
  pretty-printed JSON paste support, richer timeline details, terminal operation
  cleanup, and targeted tests.
- Deferred: true child-process termination, durable operation journal, exported
  operation contract schemas, and post-deploy verification facts.

### Cycle 4 — Core Contracts And Validation

Status: completed.

Theme: tighten Pydantic/domain models so validated objects move through the
system, duplicate checks disappear, and user-facing errors remain readable.

P0 feedback:

- None found by the cycle reviewers.

P1 feedback:

- Engine operation responses were implemented before their schemas were exported,
  leaving Studio without generated types for operation list/status/events/result,
  diagnostics, cancel, and deploy operation results.
- Bootstrap-key validation duplicated core rules in the Engine-only request model
  and could either leak Pydantic noise or return a different error shape from the
  rest of the Engine API.
- Duplicate deploy protection compared raw request text, so the same saved server
  referenced by ID and title could start two concurrent mutating deploys.
- Studio had drift-prone contract copies: color options and form defaults were
  partly hard-coded while core workflows already described them.
- Studio static validation did not enforce generated schema `maxLength` limits for
  fields such as server titles and saved-server references.
- Cancelled operations could store a tagged deploy-operation result without
  validating it against the exported schema.
- Several Engine HTTP envelopes are still returned by hand and need first-class
  core response models before the Local Engine surface can be called fully typed.

Execution target:

- Completed first slice: added exported core operation contracts and deploy
  operation result schema; generated Studio runtime contract manifests and d.ts
  files from the same contract source; moved Studio defaults/options to workflow
  helpers; enforced generated `maxLength` in static validation; made bootstrap-key
  shape errors use the common readable 422 request contract; canonicalized active
  deploy identity through resolved target IP/user/port; validated tagged deploy
  operation results even when cancellation has been requested; and added targeted
  API/contract/Studio tests.
- Deferred: typed core envelopes for every Local Engine response (`server-profile`,
  `server-validate`, `server-bootstrap-key`, and `deploy-dry-run`), plus a
  generated Engine client for Studio.

### Cycle 5 — Deploy Evidence And Recovery

Status: completed.

Theme: replace optimistic deploy success with real verification evidence,
common failure diagnosis, and safe retry/recovery actions.

P0 feedback:

- Deploy could report success when the Remnawave node container never became
  usable, because `_deploy_node_container()` returned `False` on pull/start/health
  failures while first-deploy and redeploy ignored that evidence.

P1 feedback:

- Studio progress was a raw event timeline; it should make the current phase and
  provision step the primary surface for non-IT users.
- “Cancel operation” overstated Engine behavior. The subprocess may complete
  after a stop request, so the UI must say “stop after current step” and still
  show the result if work completed.
- Studio success needs to become an action center: copy invite link when
  available, copy connection-test command, add another device, and postpone relay
  suggestions until the first device works.
- Failure handling dropped retryability, operation ID, and diagnostics; recovery
  should show the safest next action plus one-click redacted diagnostics.
- Pasted static dry-run output could unlock review for a different target.
- Static Studio asked users to run `deploy.json` commands before offering a
  deploy JSON download in the command sheet.
- Recovery still has deeper correctness risks: recovered panel-host nodes can use
  Docker gateway addresses as public server IPs; redeploy does not update existing
  Remnawave config profiles; node removal can erase local recovery state when
  remote deletion failed.

Execution target:

- Completed first slice: deploy now fails with a system error if the required
  node container does not become healthy; Engine distinguishes
  `completed_after_cancel` from true cancellation; Studio labels cancellation as
  “Stop after current step”; completed-after-stop results are shown instead of
  hidden; Studio has a friendly live progress card, success action center, retry
  and diagnostics recovery card, duplicate-running deploy resume, safer static
  dry-run target checks, command-sheet `deploy.json` download, and generated
  contract updates for the enriched deploy result.
- Deferred: true subprocess termination, durable post-deploy verification
  operation, Remnawave config-profile update/replace, recovery public-IP repair,
  node removal safety, and a full generated Studio controller test harness.

### Cycle 6 — Multi-Server Topology

Status: completed.

Theme: make capabilities and routing policy beginner-safe, including relays that
also act as exits for regional traffic such as RU routes.

P0 feedback:

- Studio cannot author a routing policy yet. Core can model a server that is both
  relay and exit for RU traffic, but Studio still exposes only server onboarding
  and deploy.
- Route-policy tests did not prove entry relay capability enforcement or invalid
  country/default route shapes.

P1 feedback:

- Saved servers need a topology-aware shelf: validated server cards with
  capabilities, region, SSH metadata, and actions such as “use as relay” or
  “use as exit”.
- The old `geo_block` control looked like “route RU traffic” even though it only
  blocks RU destinations in Xray.
- Relay identity is unsafe when two relays share the same sanitized name: nginx
  file names and Remnawave host remarks can collide.
- Declarative relay YAML can put a relay on the same host as an exit node through
  the old desired-relay model, even though same-server relay+exit must be modeled
  as capabilities, not two independent destructive resources.
- Reconciler coverage did not pin that desired relay `exit_node` names resolve to
  actual exit IPs without creating false drift.
- The deeper executable gap remains: `RoutingPolicyDraft` is contract-only until
  cluster persistence, plan/apply, Engine, and Studio compile it into live Xray
  routing.

Execution target:

- Completed first slice: added topology builder contracts, server shelf item,
  regional traffic decisions (`block`, `regional_exit`, `default_exit`), explicit
  route actions (`route`, `block`), and beginner-readable route cards; exported
  and generated schemas for those contracts; added tests for separate relay+exit,
  same-ref relay+exit, blocked RU decisions, route-shape validation, duplicate
  topology identities, and reconciler exit-name resolution; rejected duplicate
  relay labels and legacy desired-relay same-host node collisions; relabeled the
  Studio deploy boolean to “Block RU traffic” so it no longer implies regional
  exit routing.
- Deferred: persisted capability/routing policy in `cluster.yml`, Engine/Studio
  topology authoring, generated topology review/action flow, route simulator,
  Remnawave/Xray route-policy execution, and lifecycle-safe capability removal.

### Cycle 7 — Visual System And Accessibility

Status: in progress.

Theme: evolve aesthetics, density, contrast, focus states, keyboard behavior,
and responsive layout across the Studio surface.

P0 feedback:

- None found by the cycle reviewers.

P1 feedback:

- Studio still had too many visible choices per step. Server onboarding should
  prefer one primary path, make pasted SSH commands first-class, and move
  lower-level save/debug actions out of the beginner path.
- The visual system had contrast and density regressions: Studio primary buttons
  used low-contrast white-on-gold text, global `section` padding leaked into the
  cockpit, and the Studio topbar could slide under the global nav.
- Accessibility needed a hard pass: the whole task surface was an `aria-live`
  region, locked journey steps were only visually locked, step changes did not
  move focus, and help icons were unnamed focus stops.
- Help popovers could be clipped by the task container.
- The review boundary language was over-broad because key setup can already
  install a public SSH key; only deploy/service/firewall mutations wait for the
  deploy review.
- The deploy review showed settings, not impact. It needs to become impact cards
  and later an actual operation-level plan.
- Pasted dry-run evidence is still weaker than Engine evidence because current
  deploy-plan envelopes do not include the request hash/options.
- Production Studio exposed fixture buttons in the command sheet, making demo
  evidence look like a real user tool.

Execution target:

- Completed first slice: made the server step paste-first with live SSH command
  chips; collapsed the main server action to “Check server access”; turned the
  Local Studio gate into a direct `meridian studio` launcher; added honest deploy
  review language; replaced review rows with impact cards; removed production
  fixture buttons; required loaded plan evidence before deploy unlock; cleared
  stale plan evidence when options change; added named help buttons, focus
  restoration for the command sheet, focus movement on step changes, ARIA
  disabled state for locked steps, status/alert regions instead of a page-wide
  live region, high-contrast Studio CTAs, topbar/nav offset, Studio section
  padding reset, reduced-motion handling, and source-level regression tests.
- Deferred: real browser/axe/screenshot tests, request-hash evidence in dry-run
  envelopes, and a deeper operation-level deploy plan that lists concrete
  firewall/SSH/nginx/Xray/panel mutations.

### Cycle 8 — Packaging, Tests, And End-To-End Hardening

Status: completed.

Theme: package Studio with the Python distribution, strengthen browser tests,
verify static/local parity, and leave a shippable v4 foundation.

P0 feedback:

- `meridian studio` was not shippable from a packaged install. Asset discovery
  only checked explicit paths, `MERIDIAN_STUDIO_ASSETS`, or checkout-local
  `website/dist`; wheels had no packaged Studio UI.
- The Studio review checkbox could deadlock the Local Engine deploy path because
  the dry-run signature included `yes`, then checking review confirmation made
  the preview look stale and disabled Start deploy.
- Docs shipped a remote Context7 widget script at runtime, violating the
  self-hosted/no external request rule for users in blocked networks.

P1 feedback:

- Packaging only `studio/index.html` is insufficient because the built page
  references root `_astro`, `img`, fonts, and version assets.
- Release publishing built Python artifacts without rebuilding the website, so
  packaged Studio assets could drift or be missing.
- Operation duplicate protection had a check-then-start race; admission needed
  to happen inside the operation registry lock.
- Engine health exposed the absolute local asset path.
- Browser-level coverage, axe checks, no-secret built-artifact scans, durable
  diagnostics, real cancellation, and post-deploy verification remain important
  hardening work.

Execution target:

- Completed first slice: packaged built `website/dist` into wheels/sdists as
  `meridian/studio_assets`; added `importlib.resources` discovery before the
  editable checkout fallback; updated release publishing and `make build` to
  rebuild Studio assets with pnpm before Python package builds; added asset
  discovery and Hatch config tests; wheel-built and verified packaged
  `studio/index.html`, `_astro`, `img`, and `version` assets; fixed the deploy
  review-confirmation gate by keeping dry-run signatures independent from
  `yes`; removed the remote Context7 docs widget; added a dependency-free
  generated-site scanner for external runtime assets; removed absolute asset
  paths from Engine health; made duplicate deploy target admission atomic inside
  `OperationManager`; and added targeted regression tests.
- Deferred: Playwright/axe browser tests, built-artifact secret scanner, durable
  redacted operation journal/support bundle, true deploy subprocess cancellation,
  and post-deploy verification operations.

## Standing Rules

- Keep `meridian.core` free of Typer, Rich, prompts, process exits, and command
  module imports.
- Keep Engine free of command modules, console/renderers, generic shell access,
  arbitrary file access, permissive CORS, and unredacted secrets.
- Keep secrets out of JSON, JSONL, generated contracts, logs, screenshots,
  browser storage, URLs, and events.
- Use RFC 5737 IPs in tests, examples, docs, and screenshots.
- Static Studio must remain feature-complete by producing commands and JSON files;
  Local Studio may run the same actions through localhost Engine.
- Do not hand-edit generated Studio files.
