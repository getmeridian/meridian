# ruff: noqa: E501 — the page embeds its CSS, JS, and HTML as long literal lines.
"""Render one self-contained HTML page: Meridian's concepts with diagrams, and every command's help.

    uv run python scripts/cli_map.py                 # writes build/cli-map.html
    uv run python scripts/cli_map.py --out FILE --hidden

Command help comes from the live Typer tree (the same source as `meridian api commands`), so
the reference cannot drift; the concept notes and diagrams below are written by hand and are
the draft for the docs' visual explanations. The page makes no external requests.
"""

from __future__ import annotations

import argparse
import html
import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path

from meridian import __version__
from meridian import cli_types as click
from meridian.cli_catalog import cli_root

PANEL_ORDER = ["Get started", "Clients", "Servers", "Check & fix", "Advanced"]
HIDDEN_PANEL = "Control service (hidden until #95 ships)"
_MARKUP = re.compile(r"\[/?[a-z ]+\]")


@dataclass
class Param:
    name: str
    kind: str  # "argument" | "option"
    help: str = ""
    metavar: str = ""
    required: bool = False
    hidden: bool = False


@dataclass
class Command:
    path: str
    summary: str
    help: str
    panel: str
    hidden: bool
    usage: str
    params: list[Param] = field(default_factory=list)
    concepts: list[str] = field(default_factory=list)


# -- concepts (hand-written; the docs' visual explanations start here) ---------------------

CONCEPTS: list[dict[str, object]] = [
    {
        "id": "machine",
        "name": "This machine",
        "tag": "install home",
        "text": "Where you run <code>meridian</code>: a laptop or the server itself (<code>local</code>). "
        "The install home (<code>~/.config/meridian</code>) keeps what belongs to you, not to a fleet: "
        "Meridian's SSH key, preferences, the update cache, and the Xray used by <code>test</code>.",
        "commands": ["update", "help", "studio"],
    },
    {
        "id": "context",
        "name": "Context",
        "tag": "one fleet each",
        "text": "One machine can manage several fleets. Each context holds one fleet's files; the home "
        "itself is <code>default</code>, others live in <code>contexts/NAME/</code>. Pick one with "
        "<code>--context</code>, <code>MERIDIAN_CONTEXT</code>, or <code>context use</code>; "
        "<code>deploy IP --context NEW</code> creates one. A server belongs to one fleet at a time.",
        "commands": ["context"],
    },
    {
        "id": "fleet",
        "name": "Fleet",
        "tag": "cluster.yml",
        "text": "Every server and person you manage together, described as a reviewed <em>topology intent</em>: "
        "which server is the control server, which are exits, relays, and gateways, the routes, and "
        "who may connect. Commands edit the intent; <code>plan</code> shows the difference from the live "
        "servers and <code>apply</code> makes them match. <code>fleet export</code>/<code>import</code> "
        "edit it as one file.",
        "commands": ["fleet", "plan", "apply", "setup"],
    },
    {
        "id": "shelf",
        "name": "Saved servers",
        "tag": "servers.json",
        "text": "Servers you reach over SSH, saved by name so commands can say <code>main</code> instead of "
        "<code>root@198.51.100.10:22</code>. Saving a server gives it no role; the fleet does.",
        "commands": ["server"],
    },
    {
        "id": "control",
        "name": "Control server",
        "tag": "one per fleet",
        "text": "Holds the people, signs each server's configuration, and serves subscriptions and connection "
        "pages over HTTPS on 443, behind a secret path. Today that is a Remnawave panel; Meridian's own "
        "control service (<code>ctld</code>, <code>--control=meridian</code>) replaces it in 4.0, with "
        "encrypted snapshots on every managing device and <code>restore</code> onto a new server.",
        "commands": ["deploy", "panel", "serve", "migrate", "backup", "restore", "teardown"],
    },
    {
        "id": "exit",
        "name": "Exit (node)",
        "tag": "traffic leaves here",
        "text": "A server where your users' traffic reaches the internet. It runs Xray with one listener per "
        "protocol path: <strong>VLESS + Reality</strong> on TCP 443 imitating a camouflage site (its SNI), "
        "<strong>XHTTP</strong> over TLS, <strong>WSS</strong> in domain mode, and <strong>Hysteria2</strong> "
        "on UDP 443. Exits resolve names over encrypted DNS and can send chosen destinations through WARP.",
        "commands": ["node", "scan", "route", "probe"],
    },
    {
        "id": "relay",
        "name": "Relay",
        "tag": "TCP forwarder",
        "text": "A server that forwards TCP to an exit without decrypting anything (Realm). Clients connect to "
        "the relay, which helps where an exit's own address is blocked or slow; the exit still decides "
        "where traffic goes. A relay can announce its own camouflage SNI.",
        "commands": ["relay"],
    },
    {
        "id": "gateway",
        "name": "Routing gateway",
        "tag": "in-country entry",
        "text": "An entry server, usually inside the censored country, that routes per destination: local "
        "sites go out directly, everything else through an exit or an egress pool over a private bridge "
        "no user can reach. Set up with the wizard or <code>fleet import</code>.",
        "commands": ["setup", "node"],
    },
    {
        "id": "pool",
        "name": "Egress pool",
        "tag": "failover",
        "text": "Several exits behind one name. The gateway picks the fastest that answers a probe and moves on "
        "when one fails; with <em>fail closed</em>, traffic stops rather than leaking out directly when "
        "every exit is down.",
        "commands": ["fleet"],
    },
    {
        "id": "route",
        "name": "Route",
        "tag": "per destination",
        "text": "First-match rules on an exit: send matching domains or addresses through <strong>WARP</strong> "
        "(for services that refuse datacenter IPs), <strong>directly</strong>, or <strong>nowhere</strong>. "
        "Everything else follows the exit's default.",
        "commands": ["route"],
    },
    {
        "id": "client",
        "name": "Client",
        "tag": "a person",
        "text": "Someone who connects. Each client gets one subscription link and one connection page; their "
        "app refreshes the link on its own, so fleet changes reach them without a new QR code. "
        "Status, traffic limit, reset period, and expiry are yours to change at any time.",
        "commands": ["client"],
    },
    {
        "id": "delivery",
        "name": "Subscription & page",
        "tag": "what users get",
        "text": "The subscription answers every app in its own format (Base64, Xray JSON, Mihomo, sing-box, "
        "Stash), picked by the app's User-Agent, with failover between protocols. The connection page is "
        "an installable web app with a QR code, setup steps, and status in four languages.",
        "commands": ["client", "test"],
    },
    {
        "id": "health",
        "name": "Health",
        "tag": "evidence",
        "text": "<code>check</code> reads the servers, <code>test</code> sends real traffic through a client's "
        "own configuration from this device, <code>probe</code> looks the way a censor's scanner would, "
        "<code>logs</code> and <code>doctor</code> collect redacted evidence. Negative evidence exits 4, "
        "unavailable evidence 3.",
        "commands": ["check", "test", "probe", "doctor", "logs"],
    },
]


# -- diagrams (inline SVG, colored through CSS variables so both themes work) --------------


def _box(x: int, y: int, w: int, h: int, title: str, sub: str = "", kind: str = "", href: str = "") -> str:
    inner = (
        f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="10" class="node {kind}"/>'
        f'<text x="{x + w / 2}" y="{y + (h / 2 if not sub else h / 2 - 6)}" class="nt">{html.escape(title)}</text>'
    )
    if sub:
        inner += f'<text x="{x + w / 2}" y="{y + h / 2 + 12}" class="ns">{html.escape(sub)}</text>'
    return f'<a href="#{href}">{inner}</a>' if href else inner


def _arrow(points: str, label: str = "", lx: float = 0, ly: float = 0, kind: str = "") -> str:
    path = f'<polyline points="{points}" class="edge {kind}" marker-end="url(#arrow)"/>'
    if label:
        path += f'<text x="{lx}" y="{ly}" class="el">{html.escape(label)}</text>'
    return path


SVG_DEFS = (
    '<defs><marker id="arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" '
    'orient="auto-start-reverse"><path d="M0,0 L10,5 L0,10 z" class="arrowhead"/></marker></defs>'
)


def hierarchy_svg() -> str:
    parts = [
        _box(20, 150, 150, 60, "This machine", "install home", "k-machine", "c-machine"),
        _arrow("170,180 220,120", "", 0, 0),
        _arrow("170,180 220,240", "", 0, 0),
        _box(220, 90, 150, 56, "context: default", "a fleet", "k-context", "c-context"),
        _box(220, 214, 150, 56, "context: work", "another fleet", "k-context", "c-context"),
        _arrow("370,118 420,118"),
        _box(420, 82, 170, 72, "Fleet", "cluster.yml · intent", "k-fleet", "c-fleet"),
        _box(420, 214, 170, 56, "Saved servers", "servers.json", "k-shelf", "c-shelf"),
        _arrow("505,214 505,154", "roles go to", 512, 190, "dashed"),
    ]
    roles = [
        (20, "Control server", "people · signing · links", "k-control", "c-control"),
        (100, "Exits", "Reality · XHTTP · WSS · Hy2", "k-exit", "c-exit"),
        (180, "Relays", "TCP forward to an exit", "k-relay", "c-relay"),
        (260, "Gateways · pools", "route per destination", "k-gateway", "c-gateway"),
        (340, "Clients", "links · pages · limits", "k-client", "c-client"),
    ]
    for y, title, sub, kind, href in roles:
        parts.append(_arrow(f"590,118 650,{y + 28}"))
        parts.append(_box(650, y, 210, 56, title, sub, kind, href))
    return f'<svg viewBox="0 0 880 410" role="img" aria-label="Concept hierarchy">{SVG_DEFS}{"".join(parts)}</svg>'


def traffic_svg() -> str:
    parts = [
        '<text x="20" y="22" class="lane">direct</text>',
        _box(20, 34, 120, 50, "Phone", "app + subscription", "k-client"),
        _arrow("140,59 330,59", "Reality / XHTTP / Hysteria2", 235, 50),
        _box(330, 34, 140, 50, "Exit", "Xray", "k-exit", "c-exit"),
        _arrow("470,59 660,59", "", 0, 0),
        _box(660, 34, 140, 50, "Internet", "", "k-internet"),
        _arrow("400,84 400,114 660,114", "route: WARP", 540, 108, "dashed"),
        _box(660, 96, 140, 36, "Cloudflare WARP", "", "k-warp", "c-route"),
        '<text x="20" y="172" class="lane">through a relay</text>',
        _box(20, 184, 120, 50, "Phone", "", "k-client"),
        _arrow("140,209 200,209"),
        _box(200, 184, 130, 50, "Relay", "Realm, TCP only", "k-relay", "c-relay"),
        _arrow("330,209 400,209", "", 0, 0),
        _box(400, 184, 120, 50, "Exit", "", "k-exit", "c-exit"),
        _arrow("520,209 660,209"),
        _box(660, 184, 140, 50, "Internet", "", "k-internet"),
        '<text x="20" y="292" class="lane">through a routing gateway</text>',
        _box(20, 304, 120, 50, "Phone", "", "k-client"),
        _arrow("140,329 200,329"),
        _box(200, 304, 130, 50, "Gateway", "in-country", "k-gateway", "c-gateway"),
        _arrow("265,354 265,412 660,412", "local sites: direct", 470, 405, "dashed"),
        _arrow("330,329 400,329", "", 0, 0),
        '<rect x="392" y="282" width="140" height="96" rx="12" class="pool"/>',
        '<text x="462" y="298" class="ns">egress pool</text>',
        _box(402, 306, 120, 28, "Exit A", "", "k-exit", "c-pool"),
        _box(402, 340, 120, 28, "Exit B", "", "k-exit", "c-pool"),
        _arrow("532,329 660,329", "fastest that answers", 596, 320),
        _box(660, 304, 140, 50, "Internet", "", "k-internet"),
        _box(660, 394, 140, 36, "Local internet", "", "k-internet"),
        '<text x="20" y="466" class="lane">links</text>',
        _box(20, 478, 120, 44, "Phone", "", "k-client"),
        _arrow("330,500 140,500", "subscription refresh · page", 235, 492),
        _box(330, 478, 170, 44, "Control server", "HTTPS 443, secret path", "k-control", "c-control"),
    ]
    return f'<svg viewBox="0 0 820 534" role="img" aria-label="Traffic paths">{SVG_DEFS}{"".join(parts)}</svg>'


def lifecycle_svg() -> str:
    steps = [
        (20, "Edit the intent", "client · node · route · import", "k-fleet"),
        (215, "plan", "compile, observe, diff", "k-plan"),
        (410, "apply", "reconcile each resource", "k-plan"),
        (605, "Servers converge", "SSH + control service", "k-exit"),
    ]
    parts = []
    for x, title, sub, kind in steps:
        parts.append(_box(x, 30, 175, 64, title, sub, kind))
    for x in (195, 390, 585):
        parts.append(_arrow(f"{x},62 {x + 20},62"))
    parts += [
        _arrow("497,94 497,130"),
        _box(410, 130, 175, 40, "revision + snapshot", "", "k-control", "c-control"),
        _arrow("692,94 692,130"),
        _box(605, 130, 175, 40, "test · check", "", "k-health", "c-health"),
        '<text x="20" y="146" class="ns" style="text-anchor:start">Finished steps are checkpointed;</text>',
        '<text x="20" y="164" class="ns" style="text-anchor:start">re-running resumes, never repeats.</text>',
    ]
    return f'<svg viewBox="0 0 800 190" role="img" aria-label="Plan and apply">{SVG_DEFS}{"".join(parts)}</svg>'


# -- command tree ---------------------------------------------------------------------------


def _clean(text: str) -> str:
    text = text.replace("\b\n", "").replace("\b", "")
    return _MARKUP.sub("", text).strip()


def _concepts_for(path: str) -> list[str]:
    head = path.split(" ")[0]
    return [str(c["id"]) for c in CONCEPTS if head in c["commands"]]  # type: ignore[operator]


def _panel(command: click.Command, inherited: str) -> str:
    panel = getattr(command, "rich_help_panel", None)
    return panel if isinstance(panel, str) else inherited


def collect(include_hidden: bool) -> list[Command]:
    root = cli_root()
    found: list[Command] = []

    def walk(group: click.Group, prefix: list[str], panel: str, hidden: bool) -> None:
        for name, command in group.commands.items():
            is_hidden = hidden or command.hidden
            if is_hidden and not include_hidden:
                continue
            path = [*prefix, name]
            own_panel = HIDDEN_PANEL if is_hidden else _panel(command, panel)
            if isinstance(command, click.Group):
                walk(command, path, own_panel, is_hidden)
                continue
            ctx = click.Context(command, info_name="meridian " + " ".join(path))
            params = []
            for param in command.params:
                if param.name in {"help", "context"}:
                    continue
                if isinstance(param, click.Argument):
                    params.append(Param(param.human_readable_name, "argument", required=param.required))
                elif isinstance(param, click.Option):
                    flags = ", ".join([*param.opts, *param.secondary_opts])
                    metavar = "" if param.is_flag else (param.metavar or param.type.name.upper())
                    params.append(Param(flags, "option", _clean(param.help or ""), metavar, hidden=param.hidden))
            help_text = _clean(command.help or "")
            found.append(
                Command(
                    path=" ".join(path),
                    summary=_clean(command.short_help or help_text.split("\n")[0]),
                    help=help_text,
                    panel=own_panel,
                    hidden=is_hidden,
                    usage=" ".join(command.collect_usage_pieces(ctx)),
                    params=params,
                    concepts=_concepts_for(" ".join(path)),
                )
            )

    walk(root, [], "Advanced", False)
    order = {name: index for index, name in enumerate([*PANEL_ORDER, HIDDEN_PANEL])}
    return sorted(found, key=lambda c: (order.get(c.panel, 99), c.path))


# -- page -----------------------------------------------------------------------------------

CSS = """
:root{color-scheme:light dark;--bg:light-dark(#FDFAF6,#1C1E28);--s1:light-dark(#FFF,#252836);
--s2:light-dark(#F5F0E8,#2E3145);--b1:light-dark(#E5DFD3,#3A3E52);--b2:light-dark(#D5CFC3,#4A4F66);
--t1:light-dark(#1A1D2E,#E4E2DE);--t2:light-dark(#555A6E,#A6A8B6);--t3:light-dark(#636879,#9A9DAE);
--ac:light-dark(#C8933A,#D4A24E);--ac-text:light-dark(#85601F,#D4A24E);--ac2:light-dark(#C8933A1A,#D4A24E1F);
--code:light-dark(#F0EBE1,#1E2030);--ok:light-dark(#23744F,#5CE88E);--blue:light-dark(#2b7de9,#5b9cf5);
--violet:light-dark(#7a55c7,#a88af0);--teal:light-dark(#1f8a87,#4cc9c4);--rose:light-dark(#b8506d,#ee86a4);
--heading:Georgia,'Times New Roman',serif;--body:system-ui,-apple-system,'Segoe UI',sans-serif;
--mono:ui-monospace,'SF Mono',Menlo,Consolas,monospace}
[data-theme=light]{color-scheme:light}[data-theme=dark]{color-scheme:dark}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--t1);font:16px/1.55 var(--body)}
a{color:var(--ac-text)}code{font:0.88em var(--mono);background:var(--code);padding:1px 5px;border-radius:5px}
.wrap{max-width:1100px;margin:0 auto;padding:0 24px}header.top{padding:48px 0 24px;border-bottom:1px solid var(--b1)}
h1,h2,h3{font-family:var(--heading);letter-spacing:-0.015em;line-height:1.2}h1{font-size:2.4rem;margin:0 0 8px}
h2{font-size:1.7rem;margin:56px 0 8px}h3{margin:0}.lede{color:var(--t2);font-size:1.1rem;max-width:46rem}
.label{font:600 0.72rem var(--mono);letter-spacing:0.08em;text-transform:uppercase;color:var(--ac-text)}
nav.toc{display:flex;gap:16px;flex-wrap:wrap;margin-top:16px}nav.toc a{text-decoration:none;font-weight:600}
figure{margin:20px 0;padding:20px;background:var(--s1);border:1px solid var(--b1);border-radius:14px}
figcaption{color:var(--t2);font-size:0.92rem;margin-top:10px}svg{width:100%;height:auto;display:block}
.node{fill:var(--s2);stroke:var(--b2);stroke-width:1.2}a:hover .node{stroke:var(--ac);stroke-width:2}
.nt{font:600 14px var(--body);fill:var(--t1);text-anchor:middle;dominant-baseline:middle}
.ns{font:12px var(--body);fill:var(--t2);text-anchor:middle;dominant-baseline:middle}
.el{font:11.5px var(--body);fill:var(--t3);text-anchor:middle}.lane{font:600 11px var(--mono);fill:var(--ac-text);
text-transform:uppercase;letter-spacing:0.08em}.edge{fill:none;stroke:var(--t3);stroke-width:1.4}
.edge.dashed{stroke-dasharray:5 4}.arrowhead{fill:var(--t3)}.pool{fill:none;stroke:var(--teal);stroke-dasharray:4 3}
.k-machine{fill:light-dark(#efe9df,#2a2d3c)}.k-context,.k-fleet{fill:var(--ac2);stroke:var(--ac)}
.k-control{fill:light-dark(#7a55c714,#a88af01c);stroke:var(--violet)}.k-exit{fill:light-dark(#2b7de912,#5b9cf51a);stroke:var(--blue)}
.k-relay{fill:light-dark(#1f8a8714,#4cc9c41a);stroke:var(--teal)}.k-gateway{fill:light-dark(#b8506d12,#ee86a41a);stroke:var(--rose)}
.k-client{fill:light-dark(#23744F12,#5CE88E14);stroke:var(--ok)}.k-plan{fill:var(--s1);stroke:var(--ac)}
.k-health{fill:var(--s1);stroke:var(--ok)}.k-warp{fill:var(--s1);stroke:var(--ac);stroke-dasharray:4 3}
.k-internet{fill:var(--s1)}
.concepts{display:grid;grid-template-columns:repeat(auto-fill,minmax(310px,1fr));gap:14px;margin-top:16px}
.concept{background:var(--s1);border:1px solid var(--b1);border-radius:12px;padding:16px 18px;border-top:3px solid var(--ac)}
.concept p{margin:8px 0 10px;color:var(--t2);font-size:0.95rem}.chips{display:flex;flex-wrap:wrap;gap:6px}
.chip{font:12px var(--mono);padding:2px 8px;border-radius:999px;background:var(--s2);border:1px solid var(--b1);
text-decoration:none;color:var(--t1)}.chip:hover{border-color:var(--ac)}
.tools{position:sticky;top:0;background:var(--bg);padding:12px 0;display:flex;gap:16px;align-items:center;
border-bottom:1px solid var(--b1);z-index:2;flex-wrap:wrap}.tools input[type=search]{flex:1;min-width:220px;padding:9px 12px;
border:1px solid var(--b2);border-radius:8px;background:var(--s1);color:var(--t1);font:inherit}
.panel{margin-top:32px}.cmd{background:var(--s1);border:1px solid var(--b1);border-radius:12px;padding:16px 18px;margin:12px 0}
.cmd.hidden-cmd{border-style:dashed}.cmd h3{font:600 1.05rem var(--mono)}.cmd .sum{color:var(--t2);margin:4px 0 8px}
.usage{font:0.85rem var(--mono);background:var(--code);padding:8px 10px;border-radius:8px;overflow-x:auto;white-space:pre}
details{margin-top:8px}summary{cursor:pointer;color:var(--ac-text);font-weight:600;font-size:0.92rem}
.helptext{white-space:pre-wrap;color:var(--t2);font-size:0.92rem;margin:8px 0}
table{width:100%;border-collapse:collapse;font-size:0.9rem;margin-top:8px}td{border-top:1px solid var(--b1);
padding:6px 8px;vertical-align:top}td:first-child{font-family:var(--mono);white-space:nowrap;width:1%}
.pill{font:600 0.7rem var(--mono);padding:1px 7px;border-radius:999px;border:1px solid var(--b2);color:var(--t3);margin-left:8px}
.legend{display:flex;gap:14px;flex-wrap:wrap;font-size:0.85rem;color:var(--t2);margin-top:10px}
.legend span::before{content:"";display:inline-block;width:10px;height:10px;border-radius:3px;margin-right:6px;
border:1.5px solid var(--c);vertical-align:-1px}
footer{color:var(--t3);font-size:0.85rem;padding:40px 0;border-top:1px solid var(--b1);margin-top:56px}
"""

JS = """
const q=document.getElementById('q'),hid=document.getElementById('showhidden');
function filter(){const t=q.value.trim().toLowerCase();document.querySelectorAll('.cmd').forEach(c=>{
const okh=hid.checked||!c.classList.contains('hidden-cmd');c.style.display=okh&&(!t||c.dataset.text.includes(t))?'':'none'});
document.querySelectorAll('.panel').forEach(p=>{p.style.display=[...p.querySelectorAll('.cmd')].some(c=>c.style.display!=='none')?'':'none'})}
q.addEventListener('input',filter);hid.addEventListener('change',filter);filter();
document.getElementById('theme').addEventListener('click',()=>{const r=document.documentElement;
r.dataset.theme=r.dataset.theme==='dark'?'light':'dark'});
"""


def _concept_cards() -> str:
    cards = []
    for concept in CONCEPTS:
        chips = "".join(
            f'<a class="chip" href="#cmd-{html.escape(str(name))}">{html.escape(str(name))}</a>'
            for name in concept["commands"]  # type: ignore[union-attr]
        )
        cards.append(
            f'<article class="concept" id="c-{concept["id"]}"><div class="label">{concept["tag"]}</div>'
            f'<h3>{concept["name"]}</h3><p>{concept["text"]}</p><div class="chips">{chips}</div></article>'
        )
    return "".join(cards)


def _command_card(command: Command, first_of_group: set[str]) -> str:
    head = command.path.split(" ")[0]
    anchor = f' id="cmd-{html.escape(head)}"' if head not in first_of_group else ""
    first_of_group.add(head)
    rows = "".join(
        f"<tr><td>{html.escape(p.name)}{' ' + html.escape(p.metavar) if p.metavar else ''}</td>"
        f"<td>{html.escape(p.help) or ('required' if p.required else '')}"
        f"{'<span class=pill>hidden</span>' if p.hidden else ''}</td></tr>"
        for p in command.params
    )
    concepts = "".join(f'<a class="chip" href="#c-{c}">{c}</a>' for c in command.concepts)
    extra = command.help.split("\n", 1)[1].strip() if "\n" in command.help else ""
    details = (
        f'<details><summary>Full help</summary><div class="helptext">{html.escape(extra)}</div></details>'
        if extra
        else ""
    )
    text = html.escape(f"{command.path} {command.summary} {command.help}".lower(), quote=True)
    hidden = " hidden-cmd" if command.hidden else ""
    pill = "<span class=pill>hidden</span>" if command.hidden else ""
    return (
        f'<section class="cmd{hidden}"{anchor} data-text="{text}"><h3>meridian {html.escape(command.path)}{pill}</h3>'
        f'<div class="sum">{html.escape(command.summary)}</div>'
        f'<div class="usage">meridian {html.escape(command.path)} {html.escape(command.usage)}</div>'
        f"{f'<table>{rows}</table>' if rows else ''}{details}"
        f"{f'<div class=chips style=margin-top:10px>{concepts}</div>' if concepts else ''}</section>"
    )


def render(commands: list[Command]) -> str:
    panels: dict[str, list[Command]] = {}
    for command in commands:
        panels.setdefault(command.panel, []).append(command)
    seen: set[str] = set()
    reference = "".join(
        f'<div class="panel"><div class="label">{html.escape(name)}</div>'
        + "".join(_command_card(c, seen) for c in items)
        + "</div>"
        for name, items in panels.items()
    )
    legend = "".join(
        f'<span style="--c:var({var})">{label}</span>'
        for var, label in [
            ("--ac", "fleet & contexts"),
            ("--violet", "control server"),
            ("--blue", "exit"),
            ("--teal", "relay / pool"),
            ("--rose", "routing gateway"),
            ("--ok", "client & health"),
        ]
    )
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>Meridian — concepts and commands</title>
<style>{CSS}</style></head><body><div class="wrap">
<header class="top"><div class="label">Meridian {html.escape(__version__)} · generated from the CLI</div>
<h1>Concepts and commands</h1><p class="lede">Rent a server, run one command, share a link. This page shows what
Meridian manages, how traffic flows, how a change reaches your servers, and every command's help.</p>
<nav class="toc"><a href="#map">Concept map</a><a href="#traffic">Traffic paths</a><a href="#lifecycle">Plan &amp; apply</a>
<a href="#concepts">Concepts</a><a href="#reference">Command reference</a>
<a href="#" id="theme" onclick="return false">Light / dark</a></nav></header>
<h2 id="map">Concept map</h2><p class="lede">From this machine down to the people who connect. Click a box.</p>
<figure>{hierarchy_svg()}<div class="legend">{legend}</div>
<figcaption>A context is one fleet. The fleet's intent gives saved servers their roles; one server can hold several
(a control server is often also an exit).</figcaption></figure>
<h2 id="traffic">Traffic paths</h2><p class="lede">Three ways a phone reaches the internet, and where its links come from.</p>
<figure>{traffic_svg()}<figcaption>Relays forward TCP without decrypting it; gateways choose per destination; the
control server only hands out links and configuration — user traffic never passes through it unless it is also an
exit.</figcaption></figure>
<h2 id="lifecycle">Plan &amp; apply</h2><p class="lede">Every change edits the intent first; servers follow.</p>
<figure>{lifecycle_svg()}<figcaption><code>deploy</code> and <code>setup</code> run the same steps for you; commands
such as <code>client add</code> or <code>node add</code> edit the intent and apply it in one go.</figcaption></figure>
<h2 id="concepts">Concepts</h2><div class="concepts">{_concept_cards()}</div>
<h2 id="reference">Command reference</h2>
<div class="tools"><input id="q" type="search" placeholder="Filter commands, e.g. limit, sni, warp">
<label><input id="showhidden" type="checkbox"> show hidden commands</label></div>{reference}
<footer>Generated by <code>scripts/cli_map.py</code> from the live command tree; concept notes and diagrams are
hand-written there. No external requests.</footer></div><script>{JS}</script></body></html>"""


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", type=Path, default=Path("build/cli-map.html"))
    parser.add_argument("--hidden", action="store_true", help="Show hidden commands by default")
    parser.add_argument("--json", action="store_true", help="Print the collected commands as JSON instead")
    args = parser.parse_args(argv)
    commands = collect(include_hidden=True)
    if args.json:
        print(json.dumps([asdict(c) for c in commands], indent=2))
        return 0
    page = render(commands)
    if args.hidden:
        page = page.replace('id="showhidden" type="checkbox"', 'id="showhidden" type="checkbox" checked')
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(page)
    print(f"wrote {args.out} ({len(commands)} commands)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
