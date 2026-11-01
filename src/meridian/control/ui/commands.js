/* Signed node commands: opt-in session cut-off consent and per-node outcomes. */
(function () {
  "use strict";
  var M = (window.M = window.M || {});
  var h = M.h;

  function commandPath(record) {
    return "api/nodes/" + encodeURIComponent(record.node_id) + "/commands/" + encodeURIComponent(record.command.command_id);
  }
  function pending(record) { return record.status === "pending" || record.status === "waiting"; }
  function retryable(error) {
    return !error.status || error.status === 408 || error.status === 429 || error.status >= 500;
  }
  function outcome(records, timedOut, stopped) {
    if (records.some(function (r) { return r.status === "failed" || r.status === "rejected"; })) return "failed";
    if (records.some(function (r) { return r.status === "expired"; })) return "timed_out";
    if (stopped && records.some(pending)) return "read_error";
    if (timedOut && records.some(pending)) return "timed_out";
    if (records.some(function (r) { return r.status === "waiting"; })) return "waiting";
    if (records.some(pending)) return "pending";
    return records.some(function (r) { return r.result && r.result.effect === "core_restart"; }) ? "core_restart" : "done";
  }
  var LABELS = {
    waiting: "commands.waiting", pending: "commands.pending", done: "commands.done",
    core_restart: "commands.core_restart", failed: "commands.failed", timed_out: "commands.timed_out",
    read_error: "commands.read_error",
  };
  var ERROR_KEYS = {
    apply_pending: "commands.error_apply_pending",
    document_not_applied: "commands.error_document_not_applied",
    config_not_installed: "commands.error_config_not_installed",
    config_mismatch: "commands.error_config_mismatch"
  };
  function commandError(error) {
    return ERROR_KEYS[error.code] ? M.t(ERROR_KEYS[error.code]) : error.message;
  }
  function render(records, timedOut, errors, stoppedError) {
    var nodes = Object.create(null);
    records.forEach(function (record) { (nodes[record.node_id] = nodes[record.node_id] || []).push(record); });
    return h("div", { class: "stack" }, Object.keys(nodes).sort().map(function (node) {
      var status = outcome(nodes[node], timedOut, !!stoppedError);
      var readError = nodes[node].some(pending) ? stoppedError || nodes[node].map(function (record) {
        return errors[record.command.command_id];
      }).find(Boolean) : null;
      return h("section", { class: "stack-sm" }, h("p", {}, h("bdi", { class: "mono", text: node }), ": ", M.t(LABELS[status])),
        nodes[node].map(function (record) {
          return record.result && record.result.error ? h("p", { class: "muted small", text: commandError(record.result.error) }) : null;
        }), readError ? h("p", { class: "muted small", text: M.errorText(readError) }) : null);
    }));
  }

  M.showCommandResults = function (records) {
    if (!records.length) return;
    var closed = false, pollTimer;
    function stop() { closed = true; window.clearTimeout(pollTimer); }
    return M.dialog(M.t("commands.title"), function (close) {
      var box = h("div", { role: "status", "aria-live": "polite" });
      var latest = records.slice(), deadline = Date.now() + 300000;
      var errors = Object.create(null), stoppedError = null;
      function draw() {
        var timedOut = Date.now() >= deadline && latest.some(pending);
        M.mount(box, render(latest, timedOut, errors, stoppedError));
        return timedOut;
      }
      function refresh() {
        if (closed) return;
        if (draw() || stoppedError || !latest.some(pending)) return;
        var polled = latest.slice();
        Promise.allSettled(polled.map(function (record) {
          return pending(record) ? M.api("GET", commandPath(record)) : Promise.resolve(record);
        })).then(function (updated) {
          if (closed) return;
          latest = updated.map(function (update, index) {
            var record = polled[index], id = record.command.command_id;
            if (update.status === "fulfilled") { delete errors[id]; return update.value; }
            errors[id] = update.reason;
            if (!retryable(update.reason) && !stoppedError) stoppedError = update.reason;
            return record;
          });
          if (draw() || stoppedError || !latest.some(pending)) return;
          pollTimer = window.setTimeout(refresh, 1000);
        });
      }
      refresh();
      return [box, h("div", { class: "actions" }, h("button", { class: "btn btn-ghost", type: "button",
        onclick: function () { stop(); close(); } }, M.t("action.close")))];
    }).then(stop);
  };

  function confirmUserCutOff(name, title, text, action) {
    return M.api("GET", "api/users/" + encodeURIComponent(name) + "/cut-off").then(function (plan) {
      return M.dialog(title, function (close) {
        var opt = h("input", { type: "checkbox", name: "cut-off", disabled: !plan.nodes.length });
        return [h("p", { class: "muted", text: text }),
          h("label", { class: "check-row" }, opt, " ", M.t("commands.cut_off_label")),
          h("p", { class: "hint", text: M.t("commands.cut_off_help") }),
          plan.nodes.length ? h("div", { class: "stack" }, plan.nodes.map(function (node) {
            var params = { node: "\u2068" + node.node_id + "\u2069",
              inbounds: "\u2068" + node.inbound_tags.join(", ") + "\u2069" };
            return h("p", { class: "hint", text: M.t(node.effect === "core_restart" ?
              "commands.core_scope" : "commands.inbound_scope", params) });
          })) : h("p", { class: "hint", text: M.t("commands.no_inbounds") }),
          h("div", { class: "actions" },
            h("button", { class: "btn btn-ghost", type: "button", onclick: function () { close(); } }, M.t("action.cancel")),
            h("button", { class: "btn btn-danger", type: "button", onclick: function () { close({ cut_off: opt.checked }); } }, action))];
      });
    });
  }

  M.confirmCutOffRemoval = function (name) {
    return confirmUserCutOff(name, M.t("person.remove_title", { name: M.displayName(name) }), M.t("person.remove_confirm"), M.t("person.remove"));
  };

  M.confirmCutOffReissue = function (name) {
    return confirmUserCutOff(name, M.t("person.reissue_title", { name: M.displayName(name) }), M.t("person.reissue_confirm"), M.t("person.reissue"));
  };

  M.confirmCredentialRotation = function (name) {
    return confirmUserCutOff(name, M.t("person.rotate_title"), M.t("person.rotate_confirm"), M.t("person.rotate"));
  };

  var restarting = Object.create(null);
  M.restartNode = function (node) {
    if (restarting[node]) return;
    restarting[node] = true;
    return M.api("GET", "api/nodes/" + encodeURIComponent(node) + "/command-targets").then(function (targets) {
      var core = targets.units.find(function (unit) { return unit.core; });
      if (!core) throw new M.ApiError(409, "node_no_core", M.t("commands.no_core"));
      return M.confirm(M.t("commands.restart_title", { node: node }), M.t("commands.restart_text"), M.t("commands.restart"), true).then(function (ok) {
        if (!ok) return;
        return M.api("POST", "api/nodes/" + encodeURIComponent(node) + "/commands", { kind: "restart_unit", resource_id: core.resource_id }).then(function (record) {
          M.showCommandResults([record]);
        });
      });
    }).catch(function (error) { M.toast(M.errorText(error), "error"); })
      .finally(function () { delete restarting[node]; });
  };
})();
