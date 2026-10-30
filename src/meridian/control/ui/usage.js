/* Meridian console: UTC daily usage charts, built with M.h and no chart library. */
(function () {
  "use strict";
  var M = (window.M = window.M || {});
  var h = M.h;

  function path(username, suffix) { return "api/users/" + encodeURIComponent(username) + suffix; }
  function isolated(value) { return "\u2068" + M.count(value) + "\u2069"; }

  M.personHistory = function (username) { return historyCard(username); };
  M.fleetHistory = function () { return historyCard(""); };

  function historyCard(username) {
    var days = h("select", { "aria-label": M.t("usage.period_label") });
    var node = h("select", { "aria-label": M.t("usage.server") });
    var chart = h("div", { class: "stack-sm" }, h("span", { class: "spinner" }));
    var description = h("p", { class: "muted small" });
    var latest = M.latestOnly();
    var data = null;
    var card = h("section", { class: "card" },
      h("div", { class: "card-head" }, h("h2", { text: M.t(username ? "usage.person_title" : "usage.fleet_title") }),
        M.field(M.t("usage.period_label"), days), username ? null : M.field(M.t("usage.server"), node)),
      description, chart);

    function ranges(retention, chosen) {
      var values = [1, 7, 30, 90, 365, 730].filter(function (n) { return n <= retention; });
      if (values.indexOf(retention) < 0) values.push(retention);
      values.sort(function (a, b) { return a - b; });
      M.mount(days, values.map(function (n) {
        return h("option", { value: String(n), selected: n === chosen,
          text: M.t("usage.period", { days: isolated(n) }) });
      }));
    }
    ranges(730, username ? 7 : 30);

    function selectedRows() {
      if (username) return data.daily.map(function (row) {
        return { day: row.day, bytes: row.uplink_bytes + row.downlink_bytes, up: row.uplink_bytes, down: row.downlink_bytes };
      });
      var chosen = (data.nodes || []).filter(function (item) { return item.node_id === node.value; })[0];
      return chosen ? chosen.daily : data.daily;
    }

    function draw() {
      var rows = selectedRows();
      var total = rows.reduce(function (sum, row) { return sum + row.bytes; }, 0);
      description.textContent = M.t("usage.window", { days: isolated(data.days), retention: isolated(data.history_days) });
      M.mount(chart, [
        h("p", { class: "strong", text: M.t("usage.window_total", { bytes: M.bytes(total) }) }),
        M.usageChart(rows),
        h("details", { class: "usage-data" }, h("summary", { text: M.t("usage.show_table") }),
          table(rows, !!username)),
      ]);
    }

    function load() {
      var chosen = Number(days.value);
      M.mount(chart, h("span", { class: "spinner" }));
      var request = username ? M.api("GET", path(username, "/usage") + "?days=" + chosen)
        : M.api("GET", "api/traffic/daily?days=" + chosen);
      latest(request).then(function (result) {
        data = result;
        ranges(result.history_days, result.days);
        if (!username) {
          var selected = node.value;
          M.mount(node, [h("option", { value: "", text: M.t("usage.all_servers") })].concat(
            (result.nodes || []).map(function (item) {
              return h("option", { value: item.node_id, selected: item.node_id === selected, text: item.node_id });
            })));
        }
        draw();
      }, function (e) {
        if (e.code === "history_off") {
          description.textContent = "";
          M.mount(chart, [h("p", { class: "muted", text: M.t("usage.off") }),
            h("a", { href: "#/settings", text: M.t("privacy.title") })]);
        } else M.mount(chart, h("p", { class: "form-error", role: "alert", text: M.errorText(e) }));
      });
    }
    days.addEventListener("change", load);
    node.addEventListener("change", function () { if (data) draw(); });
    load();
    return card;
  }

  M.usageChart = function (rows) {
    if (!rows.length) return h("p", { class: "muted small", text: M.t("usage.empty") });
    var peak = rows.reduce(function (highest, row) { return Math.max(highest, row.bytes); }, 0);
    var width = 600, baseline = 180, height = 160, slot = width / rows.length;
    return h("div", { class: "usage-plot", dir: "ltr" },
      h("p", { class: "muted small", text: M.t("usage.peak", { bytes: M.bytes(peak) }) }),
      h("svg", { viewBox: "0 0 600 190", class: "usage-chart", role: "img", "aria-label": M.t("usage.chart_alt") },
        h("title", { text: M.t("usage.chart_alt") }),
        [20, 100, 180].map(function (y) { return h("line", { class: "usage-grid", x1: "0", x2: "600", y1: y, y2: y }); }),
        rows.map(function (row, i) {
          var size = peak ? Math.max(1, row.bytes * height / peak) : 1;
          var label = row.day + " UTC · " + M.bytes(row.bytes);
          return h("rect", { class: row.bytes ? "usage-bar" : "usage-bar usage-zero", x: i * slot + slot * 0.15,
            y: baseline - size, width: slot * 0.7, height: size, rx: Math.min(3, slot * 0.1),
            tabindex: rows.length <= 90 ? "0" : null, role: "img", "aria-label": label }, h("title", { text: label }));
        })),
      h("div", { class: "usage-dates muted small" }, h("bdi", { text: rows[0].day }), h("bdi", { text: rows[rows.length - 1].day })),
      !peak ? h("p", { class: "muted small", text: M.t("usage.empty") }) : null);
  };

  function table(rows, person) {
    return h("div", { class: "usage-table-scroll" }, h("table", { class: "usage-table" },
      h("thead", {}, h("tr", {}, h("th", { scope: "col", text: M.t("usage.day") }),
        person ? [h("th", { scope: "col", text: M.t("usage.up") }), h("th", { scope: "col", text: M.t("usage.down") })] : null,
        h("th", { scope: "col", text: M.t("usage.total") }))),
      h("tbody", {}, rows.map(function (row) {
        return h("tr", {}, h("th", { scope: "row" }, h("bdi", { text: row.day })),
          person ? [h("td", { text: M.bytes(row.up) }), h("td", { text: M.bytes(row.down) })] : null,
          h("td", { text: M.bytes(row.bytes) }));
      }))));
  }
})();
