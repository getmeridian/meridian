# src/meridian — Python CLI package
## Design decisions
- `cluster.yml` version 5 owns topology, bindings, allocations and checkpoints; the service owns every person and all live account data.
- `cluster_persistence.py` lifts legacy users, names and creation settings into typed `people_adoption`; unknown keys remain inert `_extra` data.
- Adoption is disclosed in the next reviewed apply. Only adopted account bindings are forgotten; vanished pre-upgrade accounts still retire.
- `people_adoption.py` creates missing committed seeds, fills empty names and clears the record only after a successful save. Applies and people commands resume interrupted finishes.
- `control/client.py` uses HTTPS with the admin token; people commands use `control_access.access_panel()`, fleet status uses `fleet_panel()`.
- `service_fence.py` keeps the reviewed apply revision and compare-and-swap lease in the service; handed-over `fence.json` prevents older or stale copies bypassing it.
- Shared logic belongs in libraries; libraries raise `MeridianError`, and commands translate it with `console.fail()`.
- `resolve.py` resolves saved servers and SSH targets; `ssh_keys.py` owns key installation, and `facts.py` caches host facts.
- `config` and `contexts.py` isolate fleets, while `fleet_contexts` refuses shared servers and mutations from superseded contexts.
- `host_runtime.py` owns neutral runtime names; binary pins and workload keys are typed, persisted deployment state.
- Legacy Remnawave fleets are read only by `migrate`; all other commands provide migration guidance.
## What's done well
- Ownership is explicit: topology never recreates a removed person or overwrites service names, limits, privacy or membership.
- `adapters/cluster.py` projects topology once; gateways never appear as public exits, and restricted relay hops stay internal.
- Compiled-resource kinds are explicit and retirement fails closed for an unsupported kind.
## Pitfalls
- Never save forgotten bindings or edited topology from a declined or dry-run review.
- A crash after service commit can leave a pending adoption record; verify the committed fleet, generation, plan and revision before finishing it.
- Adoption skips conflicting legacy display names with one warning; scope-refused recovery stays pending, and a failed finish never blocks list/show.
- Never put `control_user` in `FORGET_ONLY`; that cancels pending removals, including saved cut-offs.
- Empty fleets are valid. Missing people do not degrade fleet health; `test` without a usable person is unavailable evidence.
- An unreachable handed-over service never falls back to SSH fencing; otherwise a stale copy can apply around the lease.
- Only a missing local file means fresh state; malformed YAML, unknown load-bearing fields or partial keys must fail closed.
- Snapshot-derived models must retain the destination file's external-edit guard before saving.
- Server ownership compares canonical addresses; unreadable contexts refuse rather than releasing claims.
- Print `config.HOME_DISPLAY`; read `config.NAME` at call time so context switching takes effect.
- Quote every `conn.run()` interpolation with `shlex.quote()`, and validate every rendered hostname, SNI, port and path.
- Keep Reality public/private/short-ID material atomic and preserve keys through server replacement.
- A one-time SSH password reaches only a trusted host key; sudo must pass `sudo -n true` first, and ControlPath must fit Unix socket limits.
- Local mode follows the saved machine profile, never the outbound address a VPN can share.
- Build shared links with `PanelConfig.subscription_url()`; admin paths never travel in end-user URLs.
- Use IP-addressed DoH for exits and gateways; gateway IP/country routes need `IPOnDemand` before the catch-all.
- Refuse unsupported camouflage hosts before mutation, and register dynamically loaded PWA helpers as shared package assets.
- Readiness retries only named transient errors; a failed live agent is failed evidence, and refused service credentials stop status with exit 2.
- Local Xray is optional verification evidence; never execute an archive whose pin does not match.
- Build programmatic CLI options via Typer (`typer.Option` + `get_command`), never raw `click.Option(...)`; Typer 0.29+ requires internal descriptors. Raise the `typer` ceiling only after `meridian --help` passes on the new release.
- `--prerelease allow` takes every dependency's pre-release: cap a dependency whose next major is in pre-release (httpx below 1), and document beta installs with an explicit pre-release specifier.
