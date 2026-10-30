/* Signed node commands: opt-in session cut-off consent and per-node outcomes. */
(function () {
  "use strict";
  var M = (window.M = window.M || {});
  var h = M.h;

  function commandPath(record) {
    return "api/nodes/" + encodeURIComponent(record.node_id) + "/commands/" + encodeURIComponent(record.command.command_id);
  }
  function pending(record) { return record.status === "pending" || record.status === "waiting"; }
  function outcome(records, timedOut) {
    if (records.some(function (r) { return r.status === "failed" || r.status === "rejected"; })) return "failed";
    if (timedOut || records.some(function (r) { return r.status === "expired"; })) return "timed_out";
    if (records.some(function (r) { return r.status === "waiting"; })) return "waiting";
    if (records.some(pending)) return "pending";
    return records.some(function (r) { return r.result && r.result.effect === "core_restart"; }) ? "core_restart" : "done";
  }
  var LABELS = {
    waiting: "commands.waiting", pending: "commands.pending", done: "commands.done",
    core_restart: "commands.core_restart", failed: "commands.failed", timed_out: "commands.timed_out",
  };
  function render(records, timedOut, readError) {
    var nodes = Object.create(null);
    records.forEach(function (record) { (nodes[record.node_id] = nodes[record.node_id] || []).push(record); });
    return h("div", { class: "stack" }, Object.keys(nodes).sort().map(function (node) {
      var status = outcome(nodes[node], timedOut);
      return h("section", { class: "stack-sm" }, h("p", {}, h("bdi", { class: "mono", text: node }), ": ", M.t(LABELS[status])),
        nodes[node].map(function (record) {
          return record.result && record.result.error ? h("p", { class: "muted small", text: record.result.error.message }) : null;
        }), readError ? h("p", { class: "muted small", text: M.errorText(readError) }) : null);
    }));
  }

  M.showCommandResults = function (records) {
    if (!records.length) return;
    var closed = false;
    return M.dialog(M.t("commands.title"), function (close) {
      var box = h("div", { role: "status", "aria-live": "polite" });
      var latest = records.slice(), deadline = Date.now() + 300000, error = null;
      function refresh() {
        if (closed) return;
        var timedOut = Date.now() >= deadline && latest.some(pending);
        M.mount(box, render(latest, timedOut, error));
        if (timedOut || !latest.some(pending)) return;
        Promise.all(latest.map(function (record) {
          return pending(record) ? M.api("GET", commandPath(record)) : Promise.resolve(record);
        })).then(function (updated) {
          latest = updated; error = null;
          if (!latest.some(pending)) refresh();
          else window.setTimeout(refresh, 1000);
        }, function (e) {
          error = e;
          if (e.status === 401 || e.status === 403) {
            M.mount(box, render(latest, true, error));
          } else window.setTimeout(refresh, 1000);
        });
      }
      refresh();
      return [box, h("div", { class: "actions" }, h("button", { class: "btn btn-ghost", type: "button",
        onclick: function () { closed = true; close(); } }, M.t("action.close")))];
    }).then(function () { closed = true; });
  };

  M.confirmCutOffRemoval = function (name) {
    return M.api("GET", "api/users/" + encodeURIComponent(name) + "/cut-off").then(function (plan) {
      return M.dialog(M.t("person.remove_title", { name: M.displayName(name) }), function (close) {
        var opt = h("input", { type: "checkbox", name: "cut-off", disabled: !plan.nodes.length });
        var scope = plan.nodes.map(function (node) { return node.node_id; }).join(", ");
        return [h("p", { class: "muted", text: M.t("person.remove_confirm") }),
          h("label", { class: "check-row" }, opt, " ", M.t("commands.cut_off_label")),
          h("p", { class: "hint", text: M.t("commands.cut_off_help") }),
          h("p", { class: "muted small", text: scope ? M.t("commands.scope", { nodes: "\u2068" + scope + "\u2069" }) : M.t("commands.no_inbounds") }),
          h("div", { class: "actions" },
            h("button", { class: "btn btn-ghost", type: "button", onclick: function () { close(); } }, M.t("action.cancel")),
            h("button", { class: "btn btn-danger", type: "button", onclick: function () { close({ cut_off: opt.checked }); } }, M.t("person.remove")))];
      });
    });
  };
})();
