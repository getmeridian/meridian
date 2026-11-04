# core — Transport-neutral Meridian contracts
## Design decisions
- Core never imports CLI globals, Typer, Rich, commands, console or config. Requests and results use immutable, strict Pydantic contracts.
- `topology.py` owns finite reviewed roles, paths, routes and delivery. `AccessIntent` retains only the legacy squad label; people never enter compiler input.
- `people_creation.py` owns shared service requests; `people_seeds.py` owns exact legacy seeds and pending/committed adoption. Setup drafts keep people outside topology and validate initial profiles against delivery defaults.
- Usernames identify service accounts; live display names are public fields. Exact username wins before folded username/display-name matching; ambiguity refuses.
- Named access grants belong to topology; membership belongs to the service. The built-in default grants every managed inbound, while public aliases share enforcement.
- Profile declaration fingerprints hash intent before expansion, so protocol changes do not silently accept a profile edit.
- One delivery resolver orders person overrides, reviewed app rules, profile defaults, named fleet defaults and presets. Unsupported formats remain explicit.
- People queries have one typed selection; paging never changes matching people. Bulk before/after and receipts omit credentials and private notes.
- Token schemas freeze explicit cataloged capabilities independently of compatible presets, with private metadata and credential permissions kept separate.
- Account schedules share UTC calendar arithmetic, first-use activation, clamped renewal anchors and bounded future timestamps.
- Command envelopes compose registered output families; command metadata is schema-neutral and events are a fixed typed set.
- Verification is four-state; unavailable evidence is skipped, never a pass. Empty fleets are valid and have no degraded missing-person count.
- Internal agent contracts stay apart from public core schemas and are extended only behind negotiated capabilities; version skew is defined in `version_skew.py`.
- Operator settings each have one owning contract with explicit-null clearing and omitted-field preservation; core never chooses human presentation wording.
- Capacity, traffic and attention are shared aggregate evidence contracts; missing history remains unknown, while byte counts and derived ratios stay integer.
## What's done well
- New default intent/settings fields omit their defaults, preserving existing payload and plan hashes when no change was requested.
- Typed results are redacted before rendering; no barrel re-exports obscure ownership.
- Credential rotation composes the existing client status, generation and per-node outcome contracts without returning keys.
- Fleet documents compare every retained topology field; unedited round trips preserve semantic intent and ordering changes remain visible.
## Pitfalls
- People have one service owner; only 4.x service query rows retain deprecated `declared: false` for old readers. Never export topology-removal proposals.
- Keep creation seeds outside topology; unknown initial profiles and conflicting delivery defaults must fail before an apply promises them.
- Redact with `redact_string()` and reveal exact credential paths only; container reveals preserve scalar types and never expose neighboring secrets.
- Never publish SSH commands, tokens, database URLs, private keys or subscription secrets in ordinary results.
- Public schemas preserve `properties`; `WithJsonSchema` retains readable runtime validators and their actual bounds.
- Build request models before I/O and wrap validation failures at the adapter boundary; unchecked model-copy updates are not validation.
- Receipt identities bind credential generations and causes; a confirmed receipt never widens the next key's cut-off scope.
- Person traffic keeps lifetime figures beside period use; unattributed bytes are part of download, not a third direction.
- Never-used and stopped-use evidence are disjoint; quota percentages floor exact byte ratios once across Python and browser views.
- Device cap zero is unlimited; null inherits. Missing capacity evidence stays null rather than becoming zero.
- Timestamps retain their validated input precision. Validate domains, hostnames, SNI and topology IDs with their owning input types.
- Geodata decodes lazily to avoid exhausting service memory; geosite attributes are literal keys, and routing explanations follow the real first-match name pass.
- Negotiated capacity and command kinds must be present before new agent fields are used; shared refusal codes and protocol schemas cover the Rust implementation too.
- Server metadata names physical topology servers; fleet health keeps reachability, runtime freshness and convergence separate.
- Interrupted commands have no success envelope; cooperative cancellation is a typed exit-5 outcome.
