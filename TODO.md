# TODO

## Meridian Direction

Meridian is moving from "a CLI that deploys VPN infrastructure" to a structured
installation and control API with multiple clients on top.

- **meridian-core** is the source of truth for typed requests, typed results,
  JSON Schemas, redaction, validation, planning, errors, and events.
- **CLI** is one client: it parses arguments, collects prompts, renders Rich/text
  output, maps exits, and calls core/Engine contracts.
- **Meridian Studio** is another client: first a static contract-driven request
  builder, later a localhost control surface.
- **Engine** is only justified for executable local mode. Static Studio does not
  need it. Engine exists when a browser UI needs local SSH, filesystem state,
  secrets, operation execution, cancellation, and event streaming.

The near-term product proof is **deploy + verify + recover**, not a generic admin
panel and not a remote daemon.

## Completed Current Slice

- Exported deterministic meridian-core contracts under `contracts/meridian/v1`.
- Generated Studio TypeScript contract types under `website/src/studio/generated`.
- Added deploy workflow, command catalog, event catalog, deploy fixtures, and
  contract drift checks.
- Switched website contract tooling to pnpm with one-week release-age guardrails,
  blocked exotic transitive sources, and explicit build-script approvals.
- Added an initial `meridian.engine` deploy dry-run/planning boundary that does
  not import command modules or renderers.
- Moved deploy dry-run and request-file paths toward typed `DeployRequest`,
  `DeployPlan`, and output envelopes.
- Added reusable Pydantic input models for IPs, selectors, SSH users, names, and
  ports.
- Wrapped Pydantic validation into readable operator-facing hints at CLI
  boundaries.
- Exposed validation constraints in generated JSON Schemas without changing
  runtime error text.
- Verified backend tests, ruff, mypy, contract checks, Astro check, and website
  build after the contract/Pydantic slice.

## Next Milestones

### 1. Static Studio Prototype

Build `/studio/` inside the Astro website as a real first screen, not a landing
page.

- Render `deployWorkflow` sections and fields from generated contracts.
- Build a `DeployRequest` JSON object from form state.
- Map the confirmation field to `yes`.
- Validate required fields and schema-exposed IP/name/user/port constraints.
- Export `deploy.json`.
- Copy equivalent dry-run and deploy CLI commands.
- Render fixture-backed dry-run output and event timeline.
- Parse pasted `--json` envelopes and `--events=jsonl` streams into a status
  and timeline view.
- Stay static: no localhost API, cookies, CSRF, SSE, SSH, file I/O, or operation
  registry in this slice.

### 2. Operation Runtime

Only after the static UI proves the workflow:

- Add in-memory operation IDs, snapshots, replayable event streams, terminal
  result cache, and best-effort cancellation at step boundaries.
- Keep final JSON envelopes on stdout/process results; event streams should carry
  progress data and only embed terminal envelopes if that is made an explicit
  future contract.
- Add reporter hooks for apply/provision action start, completion, failure, and
  redaction.

### 3. Local Executable Studio Mode

If Studio needs "click Deploy", add a localhost-only Engine API.

- Bind to `127.0.0.1` on a random high port.
- Serve bundled Studio assets from the Python package.
- Expose typed `/api/v1/*` endpoints for schema/workflow discovery, validation,
  dry-run, start operation, operation status, event replay/SSE, result, and
  cancel.
- Use exact Host allowlists, strict Origin/Sec-Fetch checks, no permissive CORS,
  CSRF protection for mutating endpoints, no-store API responses, and CSP.
- Do not expose raw shell, arbitrary file read/write, generic SSH, proxy
  endpoints, or unredacted secrets.

### 4. Product Expansion

- Add verification evidence: port checks, Reality reachability, SNI/cert
  assumptions, domain/CDN warnings, leak-risk diagnostics.
- Add recovery flows for partial deploys, missing local state, stale SNI, backups,
  and safe retry guidance.
- Model topology as servers with roles (`panel`, `exit`, `relay`) before adding
  graph UI, split routing, relay fan-out, or multi-exit policy.
- Make client handoff first-class: create clients, preview pages, QR/deeplinks,
  import guidance, rotation, disable, and update flows.

## Standing Engineering Rules

- Keep `meridian.core` free of Typer, Rich, prompt, process exit, and command
  module imports.
- Keep Pydantic models as the source of truth for public contracts and generated
  schemas.
- Keep generated TypeScript as consumer output only; never hand-edit
  `website/src/studio/generated`.
- Keep JSON/API modes non-interactive by default. Missing input returns a
  structured user error.
- Keep secrets out of JSON, JSONL, generated contracts, logs, screenshots, browser
  storage, and events.
- Keep runtime Studio self-hosted: no external assets, CDNs, fonts, telemetry, or
  public website dependency.
- Keep every mutating executable flow behind a plan, confirmation boundary,
  operation ID, event stream, terminal envelope, retry guidance, and CLI
  equivalent.

## Backlog Constraints To Preserve

- Recovery is product-critical because partial deploys and lost local state are
  normal, not edge cases.
- Topology must become a graph because relay paths, multiple roles, split
  routing, IPv6, and health-aware policy do not fit a single-server recipe.
- Verification must show evidence, not optimism.
- Client handoff is part of the product; deploy success is not enough.
- Security posture must be explicit for supply chain, plugins, firewall choices,
  DNS sidecars, and advanced SSH escape hatches.
- Onboarding must absorb real platform friction: Windows/WSL, password-only VPS
  access, custom SSH ports, domains, nginx, and fallback explanations.
- AI/MCP automation should be adapters over narrow typed operations, not generic
  shell access.

## Open Product Questions

- Is `plan` exit code `2` for changes-pending sacred, even though `2` also means
  user errors elsewhere?
- Which commands are contract-stable in v1, and which stay best-effort/internal?
- Should API fields optimize for shell/JQ consumers, Python SDK consumers, or
  both equally?
- What advanced SSH operations should become public escape hatches, and what
  guardrails do they require?
- Which routing policy scope is v1: per-domain category, per-country domain
  lists, per-node default egress, or all of these later?
- Should role terminology in config become explicit now (`panel`, `exit`,
  `relay`) or wait until topology work begins?
