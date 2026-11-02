/* Searchable, bounded person picker. Choosing a row opens the existing share view. */
(function () {
  "use strict";
  var M = window.M, h = M.h;

  M.sharePicker = function () {
    var timer = null;
    return M.dialog(M.t("overview.share_picker"), function (close) {
      var search = h("input", { type: "search", maxlength: "256", autocomplete: "off", autofocus: true,
        "aria-label": M.t("people.search"), placeholder: M.t("people.search") });
      var list = h("div", { class: "share-picker-list" }, h("span", { class: "spinner" }));
      var error = h("p", { class: "form-error", role: "alert" });
      var count = h("p", { class: "muted small", role: "status" });
      var previous = [], cursor = null, page = null, latest = M.latestOnly();
      var back = h("button", { class: "btn btn-ghost btn-sm", type: "button", disabled: true, text: M.t("people.previous") });
      var next = h("button", { class: "btn btn-ghost btn-sm", type: "button", disabled: true, text: M.t("people.next") });
      var retry = h("button", { class: "btn btn-ghost btn-sm", type: "button", hidden: true, onclick: load, text: M.t("action.retry") });
      var body = h("div", { class: "stack" }, search, count, error, retry, list,
        h("div", { class: "row-actions share-picker-pages" }, back, next));
      function load() {
        var requested = cursor, query = search.value.trim(), params = new URLSearchParams({ limit: "20", q: query });
        function current() { return body.isConnected && requested === cursor && query === search.value.trim(); }
        if (requested) params.set("cursor", requested);
        back.disabled = next.disabled = true;
        error.textContent = ""; retry.hidden = true;
        body.setAttribute("aria-busy", "true");
        var work = M.api("GET", "api/people?" + params.toString()).catch(function (failure) {
          if (!requested || failure.code !== "invalid_request" || !current()) throw failure;
          cursor = requested = null; previous = []; params.delete("cursor");
          return M.api("GET", "api/people?" + params.toString());
        });
        latest(work).then(function (result) {
          if (!current()) return;
          page = result;
          M.mount(list, result.users.length ? result.users.map(function (person) {
            return h("button", { type: "button", class: "share-picker-person", "data-share-person": person.username,
              onclick: function () { close(person.username); } },
              h("bdi", { class: "strong", text: person.display_name || person.username }),
              M.statusChip(person.status === "active" ? "success" : "neutral", M.t("status." + person.status)));
          }) : M.emptyState(M.t(result.population ? "overview.share_no_match" : "people.empty")));
          count.textContent = M.t("people.page_count", { shown: "\u2068" + M.count(result.users.length) + "\u2069",
            matching: "\u2068" + M.count(result.total) + "\u2069", total: "\u2068" + M.count(result.population) + "\u2069" });
          back.disabled = !previous.length; next.disabled = !result.next_cursor;
          body.setAttribute("aria-busy", "false");
        }, function (failure) {
          if (!current()) return;
          error.textContent = M.errorText(failure); retry.hidden = false;
          body.setAttribute("aria-busy", "false");
        });
      }
      search.addEventListener("input", function () {
        cursor = null; previous = []; page = null;
        back.disabled = next.disabled = true;
        clearTimeout(timer);
        timer = setTimeout(load, 150);
      });
      back.addEventListener("click", function () { if (previous.length) { cursor = previous.pop(); load(); } });
      next.addEventListener("click", function () {
        if (page && page.next_cursor) { previous.push(cursor); cursor = page.next_cursor; load(); }
      });
      load();
      return body;
    }).then(function (username) {
      clearTimeout(timer);
      if (username) return M.openShare(username);
    });
  };
})();
