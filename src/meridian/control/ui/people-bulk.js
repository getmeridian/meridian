/* Frozen reviews and receipts: no credential or private-note fields. */
(function () {
  "use strict";
  var M = window.M, h = M.h;
  var ACTIONS = ["extend", "expiry", "quota", "reset-traffic", "enable", "disable", "tag", "devices", "renewal", "delete"];

  function errorText(error) {
    if (error && error.subjects && error.subjects.length) return M.t("bulk.stale", { people: error.subjects.join(", ") });
    return M.errorText(error);
  }
  function value(fields, key) {
    if (!fields) return M.t("bulk.deleted");
    var chosen = fields[key];
    if (key === "status") return M.t("status." + chosen);
    if (key === "device_limit") return chosen === null ? M.t("devices.inherit") : chosen === 0 ? M.t("devices.unlimited") : M.count(chosen);
    if (key === "traffic_limit_bytes") return chosen ? String(chosen) + " B" : M.t("people.unlimited");
    if (key === "traffic_limit_strategy") return M.t("reset." + chosen);
    if (key === "period_traffic") return chosen.upload_bytes + " B ↑ / " + chosen.download_bytes + " B ↓";
    if (typeof chosen === "string" && /_at$/.test(key)) return M.date(chosen, true) + " · " + chosen;
    return chosen === null || chosen === undefined || chosen === "" ? M.t("bulk.none") : String(chosen);
  }
  function reviewRows(people) {
    var slot = h("div", { class: "stack" }), page = 0, size = 100;
    var count = h("span", { class: "muted small", role: "status" });
    var list = h("div", { class: "bulk-review-list" });
    var previous = h("button", { type: "button", class: "btn btn-ghost btn-sm", text: M.t("people.previous"),
      onclick: function () { page--; draw(); } });
    var next = h("button", { type: "button", class: "btn btn-ghost btn-sm", text: M.t("people.next"),
      onclick: function () { page++; draw(); } });
    function draw() {
      M.mount(list, people.slice(page * size, (page + 1) * size).map(function (person) {
        var fields = person.changes || [];
        var content = [h("div", { class: "row-title" }, h("bdi", { class: "name", text: person.display_name }),
          h("bdi", { class: "muted small", text: person.username }))];
        if (person.cli_command) content.push(h("p", { text: M.t("bulk.cli_required") }), h("code", { class: "link-text", text: person.cli_command }));
        else if (!fields.length) content.push(h("p", { class: "muted small", text: M.t("bulk.unchanged") }));
        else content.push(h("dl", { class: "bulk-changes" }, fields.map(function (key) {
          return h("div", {}, h("dt", { text: M.t("bulk.field." + key) }),
            h("dd", {}, h("bdi", { text: value(person.before, key) }), h("span", { "aria-hidden": "true", text: " → " }),
              h("bdi", { text: value(person.after, key) })));
        })));
        (person.warnings || []).forEach(function (code) { content.push(h("p", { class: "hint", text: M.t("bulk.warning." + code) })); });
        return h("section", { class: "card bulk-person" }, content);
      }));
      count.textContent = M.t("bulk.review_count", { shown: M.count(Math.min(size, people.length - page * size)), total: M.count(people.length) });
      previous.disabled = page === 0; next.disabled = (page + 1) * size >= people.length;
    }
    slot.appendChild(count); slot.appendChild(list); slot.appendChild(h("div", { class: "people-pager" }, previous, next));
    draw(); return slot;
  }
  function summary(view) {
    var parameters = {};
    Object.keys(view.counts).forEach(function (key) { parameters[key] = M.count(view.counts[key]); });
    return h("div", { class: "stack-sm" }, h("p", { text: M.t("bulk.summary", parameters) }),
      h("p", { class: "muted small", text: M.t("bulk.nodes", { count: M.count(view.changed_nodes) }) }));
  }
  M.showBulkReceipt = function (initial, done) {
    var receipt = initial, busy = false;
    return M.dialog(M.t("bulk.receipt_title"), function (close) {
      var details = h("div", { class: "stack" }), error = h("p", { class: "form-error", role: "alert" });
      var undo = h("button", { type: "button", class: "btn btn-primary", "data-bulk-undo": "", text: M.t("bulk.undo"),
        onclick: function () {
          if (busy) return;
          busy = true; close.lock(true); undo.disabled = true; error.textContent = "";
          M.api("POST", "api/people/bulk/undo", { receipt_id: receipt.id }).then(function (updated) {
            receipt = updated; draw(); if (done) done(updated);
          }, function (failure) { error.textContent = errorText(failure); undo.disabled = false; })
            .finally(function () { busy = false; close.lock(false); });
        } });
      function draw() {
        M.mount(details, [
          h("p", { class: "muted small", text: M.t("bulk.receipt", { id: receipt.id }) }), summary(receipt),
          receipt.undone_at ? h("p", { role: "status", text: M.t("bulk.undone") }) : h("p", { class: "hint", text: M.t(receipt.undo_until ? "bulk.undo_description" : receipt.counts.changed ? "bulk.warning.irreversible" : "bulk.no_undo_changes") }),
          reviewRows(receipt.undo_people || receipt.people),
        ]);
        undo.hidden = !receipt.undo_until || !!receipt.undone_at || Date.parse(receipt.undo_until) < Date.now();
      }
      draw();
      return h("div", { class: "stack" }, details, error, h("div", { class: "actions" }, undo,
        h("button", { type: "button", class: "btn btn-ghost", onclick: function () { close(true); }, text: M.t("action.close") })));
    }, { wide: true });
  };

  function openReview(request, done) {
    return M.dialog(M.t("bulk.preview_title"), function (close) {
      var review = null, busy = false, generation = 0;
      var details = h("div", { class: "stack" }), error = h("p", { class: "form-error", role: "alert" });
      var commit = h("button", { type: "button", class: "btn " + (request.action.action === "delete" ? "btn-danger" : "btn-primary"),
        disabled: true, "data-bulk-commit": "", text: M.t("bulk.commit"), onclick: function () {
          if (busy || !review) return;
          busy = true; close.lock(true); commit.disabled = retry.disabled = true; error.textContent = "";
          function accepted(receipt) {
            close.lock(false); close(receipt);
            M.lastBulkReceipt = receipt;
            if (done) done(receipt);
            M.showBulkReceipt(receipt, done);
          }
          M.api("POST", "api/people/bulk/commit", { preview_id: review.id, preview_hash: review.preview_hash }).then(accepted,
            function (failure) {
              if (failure.uncertain) {
                return M.api("GET", "api/people/bulk/receipts/" + review.id).then(accepted, function () {
                  error.textContent = M.t("bulk.uncertain", { id: review.id }); retry.hidden = true;
                });
              }
              error.textContent = errorText(failure);
              retry.hidden = false;
            }).finally(function () {
              busy = false; close.lock(false); retry.disabled = false;
              // A refused commit needs a fresh review; never reuse a stale target set.
            });
        } });
      var retry = h("button", { type: "button", class: "btn btn-ghost", hidden: true, text: M.t("bulk.preview_again"),
        onclick: function () { load(); } });
      var body = h("div", { class: "stack" }, details, error, h("div", { class: "actions" }, retry,
        h("button", { type: "button", class: "btn btn-ghost", text: M.t("action.cancel"), onclick: function () { close(false); } }), commit));
      function load() {
        var mine = ++generation;
        commit.disabled = retry.disabled = true; error.textContent = "";
        M.mount(details, h("span", { class: "spinner" }));
        M.api("POST", "api/people/bulk/preview", request).then(function (result) {
          if (!body.isConnected || mine !== generation) return;
          review = result;
          M.mount(details, [summary(result), h("p", { class: "hint", text: M.t("bulk.freeze") }),
            (result.warnings || []).map(function (code) { return h("p", { class: code === "irreversible" ? "form-error" : "hint", text: M.t("bulk.warning." + code) }); }),
            reviewRows(result.people)]);
          commit.disabled = false; retry.disabled = false; retry.hidden = true;
        }, function (failure) {
          if (!body.isConnected || mine !== generation) return;
          M.mount(details, []); error.textContent = errorText(failure); retry.hidden = false; retry.disabled = false;
        });
      }
      Promise.resolve().then(load); return body;
    }, { wide: true });
  }

  function action(kind, selection, done) {
    if (["enable", "disable", "delete", "reset-traffic"].indexOf(kind) >= 0) {
      return openReview(Object.assign({ action: { action: kind } }, selection), done);
    }
    M.dialog(M.t("bulk.action." + kind), function (close) {
      var fields = M.bulkActionFields(kind);
      return h("form", { class: "stack", novalidate: true, onsubmit: function (event) {
        event.preventDefault();
        var parameters = fields.value();
        if (!parameters) return;
        close(true);
        openReview(Object.assign({ action: Object.assign({ action: kind }, parameters) }, selection), done);
      } }, fields.nodes, h("div", { class: "actions" },
        h("button", { type: "button", class: "btn btn-ghost", text: M.t("action.cancel"), onclick: function () { close(false); } }),
        h("button", { type: "submit", class: "btn btn-primary", text: M.t("bulk.preview") })));
    });
  }
  M.peopleBulkBar = function (options) {
    var count = h("span", { "data-bulk-count": "0", role: "status", "aria-live": "polite" });
    var matching = h("button", { type: "button", class: "btn btn-ghost btn-sm", "data-select-matching": "", disabled: true });
    var select = h("select", { name: "bulk-action", "aria-label": M.t("bulk.action_label") }, ACTIONS.map(function (kind) {
      return h("option", { value: kind, text: M.t("bulk.action." + kind) });
    }));
    select.value = "extend";
    var preview = h("button", { type: "button", class: "btn btn-primary btn-sm", "data-bulk-preview": "", disabled: true,
      text: M.t("bulk.preview"), onclick: function () { action(select.value, options.selection(), options.done); } });
    var clear = h("button", { type: "button", class: "btn btn-ghost btn-sm", text: M.t("bulk.clear"), onclick: options.clear });
    var receipt = h("button", { type: "button", class: "btn btn-ghost btn-sm", text: M.t("bulk.receipt_title"), hidden: !M.lastBulkReceipt,
      onclick: function () { if (M.lastBulkReceipt) M.showBulkReceipt(M.lastBulkReceipt, options.done); } });
    var nodes = h("div", { class: "card people-bulk-bar" }, count, matching, select, preview, clear, receipt);
    return { nodes: nodes, matching: matching, update: function (selected, total, all, ready) {
      count.setAttribute("data-bulk-count", String(selected));
      count.textContent = M.t(all ? "bulk.matching_selected" : "bulk.selected", { count: M.count(selected) });
      matching.textContent = M.t("bulk.select_matching", { count: M.count(total) });
      matching.disabled = !ready || !total || all;
      select.disabled = preview.disabled = !ready || !selected;
      clear.disabled = !selected; receipt.hidden = !M.lastBulkReceipt;
    } };
  };
})();
