/* Overview composes existing owner reads; counts and UTC windows come from the service. */
(function () {
  "use strict";
  var M = window.M, h = M.h;
  function number(value) { return "\u2068" + M.count(value) + "\u2069"; }
  function optional(request) { return request.then(function (data) { return { data: data }; }, function (error) { return { error: error }; }); }

  var EVENT_LABELS = {
    people: { bulk: "overview.event_bulk" },
    user: { created: "overview.event_added", updated: "overview.event_updated", enabled: "overview.event_enabled",
      disabled: "overview.event_disabled", reissued: "overview.event_reissued", credentials_rotated: "overview.event_rotated",
      removed: "overview.event_removed", traffic_reset: "overview.event_reset", expired: "overview.event_expired",
      limited: "overview.event_limited", restored: "overview.event_restored", flow_blocking: "overview.event_blocking" },
    privacy: { changed: "overview.event_privacy" }, notice: { changed: "overview.event_notice" }, language: { changed: "overview.event_language" },
    token: { created: "overview.event_token_created", revoked: "overview.event_token_revoked" },
    command: { issued: "overview.event_command_issued", result: "overview.event_command_result" },
    node: { offline: "servers.offline", online: "servers.online", apply_failed: "servers.failed_badge", apply_recovered: "servers.in_sync" },
  };
  function eventLabel(kind) {
    var parts = kind.split("."), family = M.has(EVENT_LABELS, parts[0]) && EVENT_LABELS[parts[0]];
    return family && M.has(family, parts[1]) ? family[parts[1]] : null;
  }

  M.renderOverview = function (view) {
    var status = h("div", { class: "live-status" });
    var fleetSlot = h("section", { class: "card overview-fleet" }, h("span", { class: "spinner" }));
    var attentionBody = h("div", { class: "overview-attention-body" }, h("span", { class: "spinner" }));
    var attentionSlot = h("section", { class: "card overview-attention" }, h("h2", { text: M.t("overview.attention") }), attentionBody);
    var trafficSlot = h("section", { class: "card overview-traffic", "data-detailed-only": "", hidden: M.mode() !== "detailed" });
    var recentSlot = h("section", { class: "card overview-recent", "data-detailed-only": "", hidden: M.mode() !== "detailed" });
    var cache = null, stop, events = [], revisions = [], eventCursor = 0, recentError = null;
    var attentionKey = null, lastTraffic = null;
    var actions = h("div", { class: "row-actions overview-actions", role: "group", "aria-label": M.t("overview.quick_actions") },
      h("button", { class: "btn btn-primary", type: "button", "data-overview-add": "", onclick: M.addPerson },
        M.icon("plus"), M.t("people.add")),
      h("button", { class: "btn btn-ghost", type: "button", "data-overview-share": "", onclick: M.sharePicker },
        M.icon("link"), M.t("overview.share")));
    M.mount(view, h("div", { class: "overview-page" },
      h("div", { class: "page-head" }, h("div", {}, h("h1", { text: M.t("nav.overview") }),
        h("p", { class: "muted small", text: M.t("overview.lede") })), actions),
      status,
      h("div", { class: "overview-grid" }, fleetSlot, attentionSlot, trafficSlot, recentSlot)));

    function draw(data) {
      cache = data;
      M.state.fleet = data.fleet;
      if (M.updateFleetTitle) M.updateFleetTitle();
      var active = data.people.facets.status.active || 0, total = data.people.population;
      var findings = serverFindings(data.fleet);
      var healthCount = (data.fleet.servers || []).filter(function (server) { return serverReason(server); }).length;
      if (data.traffic.data) lastTraffic = data.traffic.data;
      else if (data.traffic.error && data.traffic.error.code === "history_off") lastTraffic = null;
      var traffic = { data: lastTraffic, error: data.traffic.error, stale: !!lastTraffic && !!data.traffic.error };
      drawFleet(data, active, total, healthCount, traffic);
      var rows = [
        { href: "#/people?expires=7", label: M.t("overview.expiring", { days: number(7) }), count: data.expiring.total, kind: "warn" },
        { href: "#/people?quota=90", label: M.t("overview.quota", { percent: M.percent(90) }), count: data.quota.total, kind: "warn" },
        { href: "#/people?status=limited", label: M.t("overview.limited"), count: data.people.facets.status.limited || 0, kind: "danger" },
        { href: "#/people?status=disabled", label: M.t("status.disabled"), count: data.people.facets.status.disabled || 0, kind: "neutral" },
      ];
      if (M.mode() === "detailed") rows.push({ href: "#/people?never=1", label: M.t("overview.never"), count: data.people.facets.never || 0, kind: "neutral" });
      rows = rows.filter(function (row) { return row.count > 0; }).concat(findings);
      var key = JSON.stringify(rows);
      if (key !== attentionKey) {
        attentionKey = key;
        M.mount(attentionBody, rows.length ? h("div", { class: "attention-list" },
          rows.map(function (row) {
            return h("a", { class: "attention-row", href: row.href },
              h("span", { class: "attention-label", text: row.label }),
              row.count != null ? h("span", { "data-attention-count": "" }, M.statusChip(row.kind, number(row.count))) : M.icon("back"));
          })) : h("div", { "data-overview-empty": "" }, M.emptyState(M.t("overview.attention_empty"), "", true)));
      }
      drawTraffic(traffic);
      if (data.recent) {
        if (data.recent.events.data) {
          eventCursor = data.recent.events.data.next_cursor;
          var byId = new Map(events.map(function (event) { return [event.id, event]; }));
          (data.recent.events.data.events || []).forEach(function (event) { if (event.actor === "owner") byId.set(event.id, event); });
          events = Array.from(byId.values()).sort(function (a, b) { return b.id - a.id; }).slice(0, 5);
        }
        if (data.recent.revisions.data) revisions = (data.recent.revisions.data.revisions || []).slice(0, 5);
        recentError = data.recent.events.error || data.recent.revisions.error || null;
      }
      drawRecent();
    }

    function drawFleet(data, active, total, needsAttention, trafficState) {
      var serverCount = (data.fleet.servers || []).length;
      var healthKey = !serverCount ? "overview.no_servers" : needsAttention
        ? needsAttention === 1 ? "overview.health_attention_one" : "overview.health_attention"
        : serverCount === 1 ? "overview.health_one" : "overview.health_all";
      var healthKind = needsAttention ? "warn" : serverCount ? "success" : "neutral";
      var traffic = trafficState.data;
      var used = traffic ? traffic.daily.reduce(function (sum, day) { return sum + day.bytes; }, 0) : null;
      var historyOff = trafficState.error && trafficState.error.code === "history_off";
      M.mount(fleetSlot, [
        h("h2", { text: M.t("overview.fleet") }),
        h("p", { class: "overview-health", "data-overview-health": "", "data-health-kind": healthKind },
          h("span", { class: "dot " + (healthKind === "success" ? "dot-ok" : healthKind === "warn" ? "dot-warn" : "dot-neutral"), "aria-hidden": "true" }),
          M.t(healthKey, { count: number(needsAttention || serverCount) })),
        h("dl", { class: "overview-facts" },
          M.keyValue(M.t("overview.people"), h("div", { class: "stack-sm" },
            h("span", { class: "overview-number" }, h("span", { class: "overview-ratio", dir: document.documentElement.dir },
              h("bdi", { "data-overview-active": "", text: M.count(active) }), h("span", { class: "muted", text: "/" }),
              h("bdi", { "data-overview-total": "", text: M.count(total) }))),
            h("span", { class: "muted small", text: M.t("overview.active_total") }),
            total ? M.meter(active * 100 / total, M.t("overview.people_meter", { active: number(active), total: number(total) }))
              : h("span", { class: "muted small", text: M.t("overview.first_person") }))),
          M.keyValue(M.t("overview.traffic"), h("div", { class: "stack-sm" },
            h("span", { class: "overview-number", "data-overview-traffic": "", text: used === null ? "—" : M.bytes(used) }),
            traffic ? h("span", { class: "muted small", "data-overview-window": String(traffic.days),
              text: M.t("overview.window", { days: number(traffic.days) }) })
              : h("span", { class: "muted small", "data-history-unavailable": "", text: M.t(historyOff ? "overview.history_off" : "overview.history_unavailable") }),
            trafficState.stale ? h("span", { class: "muted small", "data-history-stale": "", text: M.t("overview.history_stale") }) : null))),
        h("p", { class: "muted small", text: M.t("overview.counted_hint") }),
      ]);
    }

    function drawTraffic(result) {
      var rows = result.data && result.data.daily;
      M.mount(trafficSlot, [h("h2", { text: M.t("overview.traffic_title") }), rows
        ? sparkline(rows) : h("p", { class: "muted small", text: M.t(result.error && result.error.code === "history_off" ? "overview.history_off" : "overview.history_unavailable") }),
        result.stale ? h("p", { class: "muted small", text: M.t("overview.history_stale") }) : null,
        result.error && result.error.code === "history_off" ? h("a", { href: "#/settings/privacy", text: M.t("privacy.title") }) : null]);
    }

    function drawRecent() {
      var changes = revisions.map(function (revision) {
        return { kind: "revision:" + revision.number, time: revision.created_at,
          href: "#/revisions/" + revision.number, label: M.t("overview.revision", { number: number(revision.number) }),
          subject: M.t("revision." + revision.status) };
      }).concat(events.filter(function (event) { return eventLabel(event.kind); }).map(function (event) {
        var family = event.kind.split(".")[0];
        var href = "#/settings" + (["privacy", "notice", "language"].indexOf(family) >= 0 ? "/" + family : "");
        if (event.kind.indexOf("user.") === 0) href = "#/people?q=" + encodeURIComponent(event.subject);
        else if (event.kind === "people.bulk") href = "#/people";
        else if (event.kind.indexOf("token.") === 0) href = "#/tokens";
        else if (event.kind.indexOf("node.") === 0 || event.kind.indexOf("command.") === 0) href = "#/servers/" + encodeURIComponent(event.subject);
        return { kind: event.kind, time: event.time, href: href, label: M.t(eventLabel(event.kind)), subject: event.subject };
      })).sort(function (a, b) { return Date.parse(b.time) - Date.parse(a.time); }).slice(0, 5);
      M.mount(recentSlot, [h("h2", { text: M.t("overview.recent") }),
        recentError ? h("p", { class: "form-error", role: "status", text: M.errorText(recentError) }) : null,
        changes.length ? h("div", { class: "recent-list" }, changes.map(function (change) {
          return h("a", { class: "recent-row", href: change.href, "data-recent-kind": change.kind },
            h("span", {}, h("span", { class: "strong", text: change.label }), " · ", h("bdi", { text: change.subject })),
            h("bdi", { class: "muted small", text: M.date(change.time, true) }));
        })) : recentError ? null : M.emptyState(M.t("overview.recent_empty"))]);
    }

    function modeChanged() {
      if (cache) draw(cache);
      stop.refresh(true);
    }
    stop = M.liveView(view, { status: status, interval: 30000,
      load: function (passive) {
        var options = { passive: passive };
        var recent = M.mode() === "detailed" ? Promise.all([
          optional(M.api("GET", "api/revisions", undefined, options)),
          optional(M.api("GET", "api/events?limit=2000&after=" + eventCursor, undefined, options)),
        ]).then(function (items) { return { revisions: items[0], events: items[1] }; }) : Promise.resolve(null);
        return Promise.all([
          M.api("GET", "api/people?limit=1", undefined, options),
          M.api("GET", "ui/api/fleet", undefined, options),
          M.api("GET", "api/people?limit=1&expires=7", undefined, options),
          M.api("GET", "api/people?limit=1&quota=90", undefined, options),
          optional(M.api("GET", "api/traffic/daily?days=30", undefined, options)), recent,
        ]).then(function (items) { return { people: items[0], fleet: items[1], expiring: items[2], quota: items[3], traffic: items[4], recent: items[5] }; });
      }, draw: draw, warning: function (data) {
        return data.traffic.error && data.traffic.error.code !== "history_off" ? data.traffic.error
          : M.mode() === "detailed" && data.recent ? data.recent.events.error || data.recent.revisions.error : null;
      }, empty: function () {
        [fleetSlot, attentionBody, trafficSlot, recentSlot].forEach(function (slot) {
          M.mount(slot, h("p", { class: "muted small", text: M.t("overview.unavailable") }));
        });
      } });
    window.addEventListener("meridian:mode", modeChanged);
    return function () { stop(); window.removeEventListener("meridian:mode", modeChanged); };
  };

  function serverReason(server) {
    if (!server.live) return "overview.server_offline";
    if (!server.runtime_available) return "overview.server_unavailable";
    if (!server.in_sync) return "overview.server_behind";
    var health = server.runtime && server.runtime.health;
    if (!health || health.status !== "healthy" || (server.failed || []).length ||
        (health.checks || []).some(function (check) { return check.status !== "pass"; })) return "overview.server_attention";
    return null;
  }

  function serverFindings(fleet) {
    var rows = new Map();
    function add(id, label) {
      var href = "#/servers/" + encodeURIComponent(id) + "/allowance", previous = rows.get(href);
      rows.set(href, { href: href, label: previous ? previous.label + " · " + label : label });
    }
    (fleet.servers || []).forEach(function (server) {
      var reason = serverReason(server);
      if (reason) add(server.node_id, M.t(reason, { name: "\u2068" + server.node_id + "\u2069" }));
    });
    (fleet.server_attention || []).forEach(function (finding) {
      var usage = (fleet.server_usage || []).find(function (item) { return item.server_ref === finding.server_ref; });
      var id = usage && usage.node_id || finding.server_ref;
      (finding.reasons || []).forEach(function (reason) {
        if (reason === "payment_due" || reason === "payment_overdue") add(id, M.t(reason === "payment_due" ? "overview.payment_due" : "overview.payment_overdue",
          { name: "\u2068" + finding.server_ref + "\u2069", days: number(7) }));
      });
    });
    return Array.from(rows.values());
  }

  function sparkline(rows) {
    if (!rows.length) return M.emptyState(M.t("usage.empty"));
    var peak = rows.reduce(function (maximum, row) { return Math.max(maximum, row.bytes); }, 0);
    var points = rows.map(function (row, index) {
      return [(rows.length > 1 ? index * 600 / (rows.length - 1) : 300), 80 - (peak ? row.bytes * 72 / peak : 0)];
    });
    return h("div", { class: "stack-sm", dir: "ltr" },
      h("svg", { class: "traffic-sparkline", viewBox: "0 0 600 88", role: "img", "aria-label": M.t("overview.traffic_chart") },
        h("title", { text: M.t("overview.traffic_chart") }),
        h("line", { class: "usage-grid", x1: "0", x2: "600", y1: "80", y2: "80" }),
        points.length === 1 ? h("circle", { class: "sparkline-point", cx: "300", cy: points[0][1], r: "4" })
          : h("polyline", { class: "sparkline-line", points: points.map(function (point) { return point.join(","); }).join(" ") })),
      h("p", { class: "muted small", dir: "auto", text: M.t("usage.peak", { bytes: M.bytes(peak) }) }),
      h("div", { class: "usage-dates muted small" }, h("bdi", { text: rows[0].day }), h("bdi", { text: rows[rows.length - 1].day })));
  }
})();
