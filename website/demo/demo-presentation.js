/* Recorded fleet settings are editable in this tab only. Use the same revision for billing and connection settings. */
(function () {
  "use strict";
  var D = window.MeridianDemo, day = 86400000;
  function has(object, key) { return Object.hasOwn(object, key); }
  function bad() { D.fail("invalid_request", 400); }
  function keys(value, allowed) {
    if (!value || typeof value !== "object" || Array.isArray(value) ||
        Object.keys(value).some(function (k) { return !allowed.includes(k); })) bad();
  }
  function text(value, max) {
    if (typeof value !== "string" || Array.from(value).length > max || /[\u0000-\u001f\u007f-\u009f]/u.test(value)) bad();
    return value.trim();
  }
  function integer(value, min, max) { if (!Number.isSafeInteger(value) || value < min || value > max) bad(); }
  function contact(value) {
    keys(value, ["text", "link"]);
    var c = { text: text(has(value, "text") ? value.text : "", 120), link: text(has(value, "link") ? value.link : "", 2048) };
    if (c.link) {
      try {
        var url = new URL(c.link), decoded = decodeURIComponent(c.link);
        if (/\s|\\/.test(c.link) || /[\u0000-\u001f\u007f-\u009f]/u.test(decoded) || url.username || url.password ||
            !(url.protocol === "https:" && url.hostname ||
              url.protocol === "tg:" && /^[a-z][a-z0-9_-]{0,63}$/i.test(url.host) && (!url.pathname || url.pathname === "/") ||
              url.protocol === "mailto:" && !url.host && !url.hash && /^[^\s@]+@[^\s@]+$/.test(url.pathname))) bad();
      } catch (e) { bad(); }
    }
    return c;
  }
  function metadata(old, patch) {
    keys(patch, ["allowance_bytes", "reset_day", "provider", "pay_on", "pay_every"]);
    var m = Object.assign({}, old, patch);
    if (has(patch, "allowance_bytes") && m.allowance_bytes !== null) integer(m.allowance_bytes, 1, Number.MAX_SAFE_INTEGER);
    integer(m.reset_day, 1, 31); m.provider = text(m.provider, 80);
    if (has(patch, "pay_on") && m.pay_on === null) m.pay_every = null;
    if (m.pay_on !== null && (typeof m.pay_on !== "string" || !/^\d{4}-\d\d-\d\d$/.test(m.pay_on) ||
        !Number.isFinite(Date.parse(m.pay_on)) || new Date(m.pay_on).toISOString().slice(0, 10) !== m.pay_on)) bad();
    if (m.pay_every !== null && (!m.pay_on || !["month", "year"].includes(m.pay_every))) bad();
    return m;
  }
  function clampMonth(year, month, date) {
    return Date.UTC(year, month, Math.min(date, new Date(Date.UTC(year, month + 1, 0)).getUTCDate()));
  }
  function nextPayment(m, today) {
    if (!m.pay_on || !m.pay_every || m.pay_on >= today) return m.pay_on;
    var now = new Date(today), anchor = new Date(m.pay_on), y = now.getUTCFullYear(), d = anchor.getUTCDate();
    var monthly = m.pay_every === "month", n = monthly ? now.getUTCMonth() : anchor.getUTCMonth();
    var due = clampMonth(y, n, d);
    if (due < now.getTime()) due = monthly ? clampMonth(y, n + 1, d) : clampMonth(y + 1, n, d);
    return new Date(due).toISOString().slice(0, 10);
  }
  function billing(servers) {
    var fleet = D.responses["GET ui/api/fleet"], attention = D.responses["GET api/attention"];
    var t = attention.thresholds, now = new Date(D.now()), today = now.toISOString().slice(0, 10);
    var soon = new Date(Date.parse(today) + t.payment_days * day).toISOString().slice(0, 10);
    var alerts = [];
    fleet.server_usage.forEach(function (row) {
      var m = servers[row.server_ref], old = row.metadata, reasons = [];
      if (!m) return;
      row.metadata = D.clone(m); row.next_payment_on = nextPayment(m, today);
      if (row.measurement === "accepted_user_traffic") {
        if (!m.allowance_bytes) {
          row.period_start = row.period_end = row.counting_since = row.used_bytes = null;
        } else if (!old.allowance_bytes || old.reset_day !== m.reset_day) {
          var y = now.getUTCFullYear(), n = now.getUTCMonth();
          if (clampMonth(y, n, m.reset_day) > now.getTime()) n--;
          row.period_start = D.iso(clampMonth(y, n, m.reset_day)); row.period_end = D.iso(clampMonth(y, n + 1, m.reset_day));
          row.counting_since = D.now(); row.used_bytes = 0;
        }
      }
      if (m.allowance_bytes && row.used_bytes !== null) {
        var percent = row.used_bytes / m.allowance_bytes * 100;
        if (percent >= t.allowance_exceeded_percent) reasons.push("allowance_exceeded");
        else if (percent >= t.allowance_percent) reasons.push("allowance_near");
      }
      if (row.next_payment_on && row.next_payment_on < today) reasons.push("payment_overdue");
      else if (row.next_payment_on && row.next_payment_on <= soon) reasons.push("payment_due");
      if (reasons.length) alerts.push({ server_ref: row.server_ref, reasons: reasons, usage: D.clone(row) });
    });
    fleet.server_attention = alerts; attention.servers = D.clone(alerts);
  }
  D.recorded("GET", "api/settings/presentation");
  D.route("PATCH", "api/settings/presentation", function (request) {
    var patch = request.body, state = D.responses["GET api/settings/presentation"];
    keys(patch, ["expected_version", "profile_title", "update_interval_hours", "owner_contact", "servers"]);
    if (patch.expected_version !== state.version) D.fail("stale_presentation", 409);
    var next = D.clone(state);
    if (has(patch, "profile_title")) { next.profile_title = text(patch.profile_title, 80); if (!next.profile_title) bad(); }
    if (has(patch, "update_interval_hours")) {
      integer(patch.update_interval_hours, 1, 168); next.update_interval_hours = patch.update_interval_hours;
    }
    if (has(patch, "owner_contact")) next.owner_contact = contact(patch.owner_contact);
    if (has(patch, "servers")) {
      if (!patch.servers || typeof patch.servers !== "object" || Array.isArray(patch.servers)) bad();
      Object.keys(patch.servers).forEach(function (ref) {
        if (!has(next.servers, ref)) D.fail("unknown_server", 400);
        next.servers[ref] = metadata(next.servers[ref], patch.servers[ref]);
      });
    }
    if (JSON.stringify(state) !== JSON.stringify(next)) {
      next.version++; D.responses["GET api/settings/presentation"] = next;
      var settings = D.responses["GET api/settings"];
      settings.profile_title = next.profile_title; settings.update_interval_hours = next.update_interval_hours;
      settings.owner_contact = next.owner_contact; D.responses["GET ui/api/fleet"].profile_title = next.profile_title;
      if (has(patch, "servers")) billing(next.servers);
    }
    return D.reply(next);
  });
})();
