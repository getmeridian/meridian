/* Meridian console: server runtime, revisions, and API tokens.
 * Topology changes stay in the CLI on the deployer's computer: SSH keys never live on
 * the control server, so this console shows servers and history but cannot change them. */
(function () {
  "use strict";
  var M = (window.M = window.M || {});
  var h = M.h;

  M.cliHint = function () {
    return h("div", { class: "cli-hint" }, M.icon("terminal"),
      h("div", {}, h("p", { class: "strong", text: M.t("cli.title") }), h("p", { class: "muted small", text: M.t("cli.text") })),
      M.command("meridian"));
  };

  function country(code) {
    if (!code) return "";
    try {
      return new Intl.DisplayNames([M.i18n.locale()], { type: "region" }).of(code.toUpperCase()) || code;
    } catch (e) { return code.toUpperCase(); }
  }

  function loading(view) { M.mount(view, h("div", { class: "empty" }, h("span", { class: "spinner" }))); }
  function failed(view, e) { M.mount(view, h("div", { class: "notice notice-err", text: M.errorText(e) })); }

  // -- servers --------------------------------------------------------------------------------

  M.renderServers = function (view) {
    var status = h("div", { class: "live-status" });
    var cards = h("div", { class: "live-data" }, h("div", { class: "empty" }, h("span", { class: "spinner" })));
    M.mount(view, [
        h("div", { class: "page-head" }, h("div", {}, h("h1", { text: M.t("nav.servers") }),
          h("p", { class: "muted", text: M.t("servers.lede") }))),
        M.cliHint(), status, cards, M.fleetHistory(),
    ]);
    return M.liveView(view, {
      status: status, interval: 10000,
      load: function (passive) { return M.api("GET", "ui/api/fleet", undefined, { passive: passive }); },
      draw: function (fleet) {
        M.state.fleet = fleet;
        var servers = fleet.servers || [];
        M.mount(cards, servers.length ? h("div", { class: "cards" }, servers.map(serverCard))
          : h("div", { class: "empty" }, h("p", { text: M.t("servers.empty") })));
      },
      empty: function () { M.clear(cards); },
    });
  };

  function serverCard(server) {
    var sync = !server.runtime_available ? M.pill("muted", M.t("servers.convergence_unavailable"))
      : server.failed && server.failed.length ? M.pill("err", M.t("servers.failed")) : server.in_sync
      ? M.pill("ok", M.t("servers.in_sync"))
      : server.applied_revision ? M.pill("warn", M.t("servers.applying")) : M.pill("muted", M.t("servers.waiting"));
    var runtime = server.runtime_available ? server.runtime : null;
    var health = runtime && runtime.health;
    var checks = health ? health.checks || [] : [];
    var faults = checks.filter(function (check) { return check.status !== "pass"; });
    var healthStatus = health ? health.status : "unavailable";
    if (healthStatus === "healthy" && faults.length) healthStatus = "degraded";
    var coreUptime = checks.find(function (check) { return check.name === "xray.uptime"; });
    var up = coreUptime && /^up (\d+)\s*s$/.exec(coreUptime.message);
    return h("section", { class: "card server" },
      h("div", { class: "card-head" },
        h("div", { class: "server-name" },
          h("span", { class: "dot " + (server.live ? "dot-ok" : "dot-err"), "aria-hidden": "true" }),
          h("h2", { class: "mono", text: server.node_id })),
        M.pill("muted", M.t("role." + server.role))),
      h("div", { class: "server-badges" },
        M.pill(server.live ? "ok" : "muted", M.t(server.live ? "servers.online" : "servers.offline")),
        M.pill({ healthy: "ok", degraded: "warn", unhealthy: "err", unavailable: "muted" }[healthStatus],
          M.t("servers.health_" + healthStatus)), sync),
      h("dl", { class: "facts" },
        server.region ? [h("dt", { text: M.t("servers.country") }), h("dd", { text: country(server.region) })] : null,
        h("dt", { text: M.t("servers.agent") }),
        h("dd", {}, M.t(server.live ? "servers.online" : "servers.offline"), " · ",
          M.t("servers.seen", { when: M.ago(server.last_seen_at) })),
        h("dt", { text: M.t("servers.version") }), h("dd", { class: "mono", text: server.agent_version || "—" }),
        h("dt", { text: M.t("servers.revision") }),
        h("dd", {}, M.t("servers.revisions", {
          applied: M.count(server.applied_revision), current: M.count(server.revision) })),
        h("dt", { text: M.t("servers.traffic") }), h("dd", { text: M.bytes(server.traffic_bytes) }),
        health ? [
          h("dt", { text: M.t("servers.memory") }), h("dd", { text: M.bytes(health.memory_available_bytes) }),
          h("dt", { text: M.t("servers.disk") }), h("dd", { text: M.bytes(health.disk_free_bytes) }),
          h("dt", { text: M.t("servers.load") }), h("dd", { dir: "ltr", text: M.count(health.load1_milli / 1000) }),
          h("dt", { text: M.t("servers.uptime") }), h("dd", { text: M.uptime(health.uptime_seconds) }),
          coreUptime ? [h("dt", { text: M.t("servers.core_uptime") }),
            h("dd", { text: up ? M.uptime(Number(up[1])) : coreUptime.message })] : null,
          h("dt", { text: M.t("servers.core_version") }),
          h("dd", { class: "mono", text: "Xray " + (runtime.versions.xray || "—") }),
        ] : null),
      !runtime ? h("p", { class: "notice notice-warn small", text: M.t("servers.health_unavailable") }) : null,
      faults.length ? h("ul", { class: "notice notice-warn health-checks" }, faults.map(function (check) {
        return h("li", {}, h("strong", { text: check.name === "stats" ? M.t("servers.stats_stalled") + " (stats)" : check.name }),
          " · ", h("span", { text: check.message }));
      })) : null,
      runtime && server.failed && server.failed.length ? h("div", { class: "notice notice-err" },
        h("p", { class: "strong", text: M.t("servers.failed") }),
        h("ul", {}, (server.errors && server.errors.length ? server.errors : server.failed).map(function (line) {
          return h("li", { class: "mono small", text: line });
        })))
        : null,
      h("div", { class: "row-actions" }, h("button", { class: "btn btn-ghost btn-sm", type: "button",
        onclick: function () { M.restartNode(server.node_id); } }, M.t("commands.restart"))));
  }

  // -- revisions --------------------------------------------------------------------------------

  M.renderRevisions = function (view, number) {
    if (number) return renderRevision(view, Number(number));
    loading(view);
    M.api("GET", "api/revisions").then(function (data) {
      var revisions = data.revisions || [];
      M.mount(view, [
        h("div", { class: "page-head" }, h("div", {}, h("h1", { text: M.t("nav.revisions") }),
          h("p", { class: "muted", text: M.t("revisions.lede") }))),
        revisions.length ? h("div", { class: "list" }, revisions.map(function (r) {
          return h("a", { class: "row row-link", href: "#/revisions/" + r.number },
            h("div", { class: "row-main" },
              h("div", { class: "row-title" }, h("span", { class: "name mono", text: "#" + r.number }),
                revisionPill(r.status)),
              h("div", { class: "row-sub" },
                h("span", { text: M.t("revisions.by", { name: r.author.name }) }),
                h("span", { text: M.date(r.created_at, true) }))),
            h("div", { class: "row-side muted small", text: r.detail || "" }));
        })) : h("div", { class: "empty" }, h("p", { text: M.t("revisions.empty") })),
      ]);
    }, function (e) { failed(view, e); });
  };

  function revisionPill(status) {
    var kinds = { applied: "ok", approved: "info", proposed: "muted", failed: "err" };
    return M.pill(kinds[status] || "muted", M.t("revision." + status));
  }

  function renderRevision(view, number) {
    loading(view);
    Promise.all([M.api("GET", "api/revisions/" + number), M.api("GET", "api/revisions")]).then(function (results) {
      var revision = results[0];
      var others = (results[1].revisions || []).filter(function (r) { return r.number !== number; });
      var compare = h("select", { "aria-label": M.t("revisions.compare") },
        others.map(function (r) {
          return h("option", { value: String(r.number), selected: r.number === number - 1,
            text: "#" + r.number + " · " + M.t("revision." + r.status) });
        }));
      var diffBox = h("div", { class: "diff" });
      var latest = M.latestOnly();
      function showDiff() {
        if (!compare.value) { M.mount(diffBox, h("p", { class: "muted", text: M.t("revisions.first") })); return; }
        var chosen = Number(compare.value);
        latest(M.api("GET", "api/revisions/" + chosen)).then(function (other) {
          var older = chosen < number;
          renderDiff(diffBox, older ? other.intent : revision.intent, older ? revision.intent : other.intent,
            older ? chosen : number, older ? number : chosen);
        }, function (e) { failed(diffBox, e); });
      }
      compare.addEventListener("change", showDiff);
      M.mount(view, [
        h("a", { class: "back", href: "#/revisions" }, M.icon("back"), M.t("nav.revisions")),
        h("div", { class: "page-head" }, h("h1", { class: "mono", text: "#" + revision.number }),
          revisionPill(revision.status)),
        h("section", { class: "card" }, h("dl", { class: "facts" },
          h("dt", { text: M.t("revisions.proposed") }),
          h("dd", { text: M.t("revisions.by_at", { name: revision.author.name, when: M.date(revision.created_at, true) }) }),
          revision.approved_by ? [h("dt", { text: M.t("revisions.approved") }),
            h("dd", { text: M.t("revisions.by_at", { name: revision.approved_by.name,
              when: M.date(revision.approved_at, true) }) })] : null,
          revision.finished_at ? [h("dt", { text: M.t("revisions.finished") }),
            h("dd", { text: M.date(revision.finished_at, true) })] : null,
          revision.detail ? [h("dt", { text: M.t("revisions.detail") }), h("dd", { text: revision.detail })] : null,
          h("dt", { text: M.t("revisions.plan_hash") }), h("dd", { class: "mono small hash", text: revision.plan_hash }))),
        h("section", { class: "card" },
          h("div", { class: "card-head" }, h("h2", { text: M.t("revisions.changes") }),
            others.length ? M.field(M.t("revisions.compare"), compare) : null),
          diffBox),
      ]);
      showDiff();
    }, function (e) { failed(view, e); });
  }

  /* Flatten an intent to path -> JSON text, so two snapshots compare leaf by leaf. */
  function flatten(value, prefix, out) {
    if (value !== null && typeof value === "object") {
      var keys = Object.keys(value);
      if (!keys.length) out[prefix || "·"] = Array.isArray(value) ? "[]" : "{}";
      keys.forEach(function (key) {
        // A key that is not a plain name is quoted, so {"a.b": 1} and {"a": {"b": 1}} never share a path.
        var next = Array.isArray(value) ? prefix + "[" + key + "]"
          : /^[A-Za-z_][A-Za-z0-9_-]*$/.test(key) ? (prefix ? prefix + "." : "") + key
          : prefix + "[" + JSON.stringify(key) + "]";
        flatten(value[key], next, out);
      });
    } else {
      out[prefix || "·"] = JSON.stringify(value);
    }
    return out;
  }

  M.intentDiff = function (older, newer) {
    // Null-prototype maps: a key named like a built-in (toString, __proto__) must read as absent, not inherited.
    var a = flatten(older || {}, "", Object.create(null));
    var b = flatten(newer || {}, "", Object.create(null));
    var paths = Object.keys(a).concat(Object.keys(b).filter(function (k) { return !M.has(a, k); })).sort();
    return paths.filter(function (p) { return a[p] !== b[p]; }).map(function (p) {
      return { path: p, before: a[p], after: b[p] };
    });
  };

  function renderDiff(box, older, newer, olderNumber, newerNumber) {
    var changes = M.intentDiff(older, newer);
    if (!changes.length) {
      M.mount(box, h("p", { class: "muted", text: M.t("revisions.same", { a: olderNumber, b: newerNumber }) }));
      return;
    }
    M.mount(box, [
      h("p", { class: "muted small", text: M.t("revisions.diff_of", { a: olderNumber, b: newerNumber }) }),
      h("ul", { class: "diff-list" }, changes.map(function (c) {
        var kind = c.before === undefined ? "add" : c.after === undefined ? "del" : "mod";
        return h("li", { class: "diff-" + kind, dir: "ltr" },
          h("span", { class: "diff-mark", "aria-hidden": "true", text: kind === "add" ? "+" : kind === "del" ? "−" : "~" }),
          h("span", { class: "diff-path", text: c.path }),
          kind !== "add" ? h("span", { class: "diff-before", text: c.before }) : null,
          kind === "mod" ? h("span", { class: "diff-arrow", "aria-hidden": "true", text: "→" }) : null,
          kind !== "del" ? h("span", { class: "diff-after", text: c.after }) : null);
      })),
    ]);
  }

  // -- tokens -----------------------------------------------------------------------------------

  M.renderTokens = function (view) {
    loading(view);
    M.api("GET", "api/tokens").then(function (data) {
      var tokens = data.tokens || [];
      M.mount(view, [
        h("div", { class: "page-head" },
          h("div", {}, h("h1", { text: M.t("nav.tokens") }), h("p", { class: "muted", text: M.t("tokens.lede") })),
          h("button", { class: "btn btn-primary", type: "button", onclick: function () { createToken(view); } },
            M.icon("plus"), M.t("tokens.create"))),
        tokens.length ? h("div", { class: "list" }, tokens.map(function (token) {
          var expired = token.expires_at && Date.parse(token.expires_at) <= Date.now();
          return h("div", { class: "row" + (token.revoked_at || expired ? " row-dim" : "") },
            h("div", { class: "row-main" },
              h("div", { class: "row-title" }, h("span", { class: "name", text: token.name }),
                M.pill(token.scope === "admin" ? "warn" : "muted", M.t("scope." + token.scope))),
              h("div", { class: "row-sub" },
                h("span", { text: M.t("tokens.created", { when: M.date(token.created_at) }) }),
                h("span", { text: token.expires_at
                  ? M.t(expired ? "tokens.expired" : "tokens.expires", { when: M.date(token.expires_at, true) })
                  : M.t("tokens.expires_never") }),
                h("span", { text: token.revoked_at ? M.t("tokens.revoked", { when: M.date(token.revoked_at) })
                  : M.t("tokens.used", { when: M.ago(token.last_used_at) }) }))),
            token.revoked_at ? null : h("button", { class: "btn btn-ghost btn-sm", type: "button", onclick: function () {
              M.confirm(M.t("tokens.revoke_title", { name: token.name }), M.t("tokens.revoke_text"), M.t("tokens.revoke"),
                true).then(function (ok) {
                if (!ok) return;
                M.api("DELETE", "api/tokens/" + token.id).then(function () {
                  M.toast(M.t("tokens.revoked_toast"));
                  M.renderTokens(view);
                }, function (e) { M.toast(M.errorText(e), "error"); });
              });
            } }, M.t("tokens.revoke")));
        })) : h("div", { class: "empty" }, h("p", { text: M.t("tokens.empty") })),
      ]);
    }, function (e) { failed(view, e); });
  };

  function createToken(view) {
    M.dialog(M.t("tokens.create_title"), function (close) {
      var issuing = false;
      var name = h("input", { type: "text", required: true, maxlength: "64", autocomplete: "off" });
      var scope = h("select", {}, ["read", "propose", "approve", "people"].map(function (s) {
        return h("option", { value: s, text: M.t("scope." + s) + " — " + M.t("scope." + s + "_hint") });
      }));
      var expiry = h("select", {}, [7, 30, 90, null].map(function (days) {
        return h("option", { value: days === null ? "never" : String(days), selected: days === 90,
          text: days === null ? M.t("tokens.expires_never") : M.t("tokens.expiry_days", { days: M.count(days) }) });
      }));
      var form = h("form", { class: "stack", novalidate: true },
        M.field(M.t("tokens.name"), name, M.t("tokens.name_hint")),
        M.field(M.t("tokens.scope"), scope),
        M.field(M.t("tokens.expiry"), expiry),
        h("p", { class: "form-error", role: "alert" }),
        h("div", { class: "actions" },
          h("button", { class: "btn btn-ghost", type: "button", onclick: function () { close(); } }, M.t("action.cancel")),
          h("button", { class: "btn btn-primary", type: "submit" }, M.t("tokens.create"))));
      form.addEventListener("submit", function (e) {
        e.preventDefault();
        if (issuing) return;
        if (!name.value.trim()) { form.querySelector(".form-error").textContent = M.t("tokens.name_required"); return; }
        issuing = true;
        close.lock(true);
        M.busy(form, function () {
          return M.api("POST", "api/tokens", { name: name.value.trim(), scope: scope.value,
            expires_in_days: expiry.value === "never" ? null : Number(expiry.value) });
        }).then(function (issued) {
          close.lock(false);
          close(issued && typeof issued.token === "string" && issued.token ? issued : { uncertain: true });
        }, function (error) {
          issuing = false;
          close.lock(false);
          if (error && error.uncertain) close({ uncertain: true });
        });
      });
      return form;
    }).then(function (issued) {
      if (!issued) return;
      M.renderTokens(view);
      if (issued.uncertain) {
        return M.dialog(M.t("tokens.uncertain_title"), function (close) {
          return [h("p", { class: "notice notice-warn", text: M.t("tokens.uncertain_text") }),
            h("div", { class: "actions" }, h("button", { class: "btn btn-primary", type: "button",
              onclick: function () { close(); } }, M.t("action.done")))];
        });
      }
      M.dialog(M.t("tokens.secret_title"), function (close) {
        return [
          h("p", { class: "notice notice-warn", text: M.t("tokens.secret_once") }),
          h("code", { class: "secret secret-token", text: issued.token }),
          h("div", { class: "actions" },
            h("button", { class: "btn btn-ghost", type: "button", onclick: function () { M.copy(issued.token); } },
              M.icon("copy"), M.t("action.copy")),
            h("button", { class: "btn btn-primary", type: "button", onclick: function () { close(); } }, M.t("action.done"))),
        ];
      });
    });
  }
})();
