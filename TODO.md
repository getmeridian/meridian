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
- **Beginner onboarding starts with servers, not deploy flags.** A new user should
  add one server at a time, prove SSH connectivity, optionally bootstrap
  password-based SSH into key-based SSH, title the server, and reuse it by
  reference in later deploy/relay/recovery flows.
- **UX is authoritative.** YAML files, Pydantic models, generated schemas, and
  command flags are implementation contracts; they must bend to the ideal user
  journey, not force users through storage-shaped forms.

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
- Added server onboarding contracts for title, server IP, SSH user, SSH port,
  validation results, key-bootstrap metadata, and saved profiles.
- Added a static Studio server setup step with draft JSON, SSH test,
  `ssh-copy-id`, and legacy `meridian server add` commands without password or
  key material in the browser.
- Added a v2 server-profile store foundation and let deploy target resolution
  use saved profile titles/IDs/IPs with SSH port metadata.
- Removed single-purpose server purpose selection from onboarding and added initial
  topology contracts for capabilities plus routing policy, including routes
  where one regional server can be both relay entry and exit.
- Verified backend tests, ruff, mypy, contract checks, Astro check, and website
  build after the contract/Pydantic slice.

## Next Milestones

### 1. Studio Server Onboarding

Make server connection setup a first-class Studio journey before deploy.

- Add servers as individual setup steps: title, IP, SSH user, and SSH port.
- Validate reachability separately from deploy. Users should know whether SSH
  works before Meridian plans anything.
- Support password-based first connection for beginners, then guide them into
  proper key-based SSH.
- Generate or select an SSH key, install the public key on the server, verify
  key login, and clearly explain what changed.
- Keep password handling Engine-only when executable mode arrives; static Studio
  may model the flow and copy CLI commands, but must not store passwords.
- Save reusable server references so deploy/relay/recovery flows can target a
  server by title or IP instead of repeatedly asking for raw connection details.
- Make server connection errors friendly: wrong port, refused auth, missing sudo,
  first-login host key prompts, and blocked network should produce specific next
  actions.

### 2. Static Studio Deploy Assistant

Build `/studio/` into a real first-screen deploy assistant, not a landing page.

- Render `deployWorkflow` sections and fields from generated contracts.
- Build a `DeployRequest` JSON object from form state.
- Map the confirmation field to `yes`.
- Validate required fields and schema-exposed IP/name/user/port constraints.
- Prefer server references when available; raw IP/user/port entry is a fallback,
  not the main beginner path.
- Export `deploy.json`.
- Copy equivalent dry-run and deploy CLI commands.
- Render fixture-backed dry-run output and event timeline.
- Parse pasted `--json` envelopes and `--events=jsonl` streams into a status
  and timeline view.
- Stay static until executable mode: no localhost API, cookies, CSRF, SSE, SSH,
  file I/O, operation registry, password storage, or key material in the browser.

### 3. Operation Runtime

Only after the static UI proves the server/deploy workflow:

- Add in-memory operation IDs, snapshots, replayable event streams, terminal
  result cache, and best-effort cancellation at step boundaries.
- Keep final JSON envelopes on stdout/process results; event streams should carry
  progress data and only embed terminal envelopes if that is made an explicit
  future contract.
- Add reporter hooks for apply/provision action start, completion, failure, and
  redaction.
- Add operation types for server validation and SSH key bootstrap before deploy
  execution.

### 4. Local Executable Studio Mode

If Studio needs "click Deploy", add a localhost-only Engine API.

- Bind to `127.0.0.1` on a random high port.
- Serve bundled Studio assets from the Python package.
- Expose typed `/api/v1/*` endpoints for schema/workflow discovery, validation,
  dry-run, start operation, operation status, event replay/SSE, result, and
  cancel.
- Include server endpoints for add/list/validate/bootstrap-key so the UI can
  onboard multiple servers before choosing deploy targets.
- Use exact Host allowlists, strict Origin/Sec-Fetch checks, no permissive CORS,
  CSRF protection for mutating endpoints, no-store API responses, and CSP.
- Do not expose raw shell, arbitrary file read/write, generic SSH, proxy
  endpoints, or unredacted secrets.

### 5. Product Expansion

- Add verification evidence: port checks, Reality reachability, SNI/cert
  assumptions, domain/CDN warnings, leak-risk diagnostics.
- Add recovery flows for partial deploys, missing local state, stale SNI, backups,
  and safe retry guidance.
- Model topology as capabilities plus routing policy, not one fixed role per
  server. A machine may relay traffic and also be an exit for a regional route
  such as RU traffic.
- Make client handoff first-class: create clients, preview pages, QR/deeplinks,
  import guidance, rotation, disable, and update flows.

## Standing Engineering Rules

- Keep `meridian.core` free of Typer, Rich, prompt, process exit, and command
  module imports.
- Keep Pydantic models as the source of truth for public wire validation and
  generated schemas, not as the source of truth for product flow design.
- Keep YAML/config storage as persistence, not the UX model. If a better flow
  needs smaller request objects, add them and adapt to storage behind the scenes.
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
- Keep password and private key material out of static Studio. Executable Studio
  may handle them only through Engine-owned short-lived operations.

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
- Server setup must be teachable: password SSH, key bootstrap, SSH port changes,
  sudo checks, host key prompts, and multi-server naming are part of the product.
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
- Which routing policy scope is first-class in v1: per-domain category,
  per-country domain lists, per-node default egress, or a smaller first slice
  that maps cleanly to Remnawave/Xray without making beginners edit raw YAML?
- How much SSH key management should Meridian own: generate a dedicated key,
  reuse an existing user key, or support both with clear defaults?
- Should server references be stored as friendly titles, stable IDs, raw IPs, or
  all three with one display name?
