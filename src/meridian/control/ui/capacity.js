/* Address-free server capacity. Missing samples stay gaps in local SVG charts. */
(function () {
  "use strict";
  var M = (window.M = window.M || {});
  var h = M.h;
  var states = {
    current: "capacity.current", stale: "capacity.stale",
    unavailable: "capacity.unavailable", not_reported: "capacity.not_reported",
  };

  function missing() { return M.t("capacity.not_measured"); }
  function isolated(value) { return "\u2068" + value + "\u2069"; }
  function number(value) { return value == null ? missing() : isolated(M.count(value)); }
  function bytes(value) { return value == null ? missing() : M.bytes(value); }
  function load(value) { return value == null ? missing() : isolated(M.count(value / 1000)); }
  function percent(value) { return value == null ? missing() : M.percent(value / 10, 1); }
  function rate(value) { return value == null ? missing() : M.t("capacity.rate", { rate: M.bytes(value) }); }
  function utc(value) { return "\u2066" + String(value).slice(0, 16).replace("T", " ") + "\u2069"; }
  function fact(label, value) { return [h("dt", { text: M.t(label) }), h("dd", { text: value })]; }

  M.serverCapacity = function (capacity) {
    var data = capacity || { status: "not_reported" };
    var status = M.has(states, data.status) ? data.status : "unavailable";
    var values = data.values;
    var sample = data.sample;
    var help = {
      stale: "capacity.stale_help", unavailable: "capacity.unavailable_help", not_reported: "capacity.not_reported_help",
    };
    return h("div", { class: "server-capacity stack-sm" },
      h("div", { class: "card-head" }, h("h3", { text: M.t("capacity.title") }),
        M.pill(status === "current" ? "ok" : status === "stale" ? "warn" : "muted", M.t(states[status]))),
      status !== "current" ? h("p", { class: "muted small", text: M.t(help[status]) }) : null,
      data.received_at ? h("p", { class: "muted small", text: M.t("capacity.received", { when: M.ago(data.received_at) }) }) : null,
      status === "current" && values && sample ? h("dl", { class: "facts capacity-now" },
        fact("capacity.cpus", number(values.cpu_count)),
        fact("capacity.cpu_load", load(values.load_per_cpu_milli)),
        fact("capacity.memory", values.memory_used_bytes != null && values.memory_total_bytes != null
          ? M.t("capacity.memory_fraction", { used: bytes(values.memory_used_bytes), total: bytes(values.memory_total_bytes) }) : missing()),
        fact("capacity.rx_now", rate(values.rx_bytes_per_second)),
        fact("capacity.tx_now", rate(values.tx_bytes_per_second)),
        fact("capacity.xray_memory", bytes(values.xray_memory_resident_bytes)),
        fact("capacity.xray_uptime", values.xray_uptime_seconds == null ? missing() : M.uptime(values.xray_uptime_seconds))) : null,
      h("p", { class: "muted small", text: M.t("capacity.cpu_load_hint") }),
      h("p", { class: "muted small", text: M.t("capacity.host_traffic") }),
      history(data));
  };

  function history(data) {
    var day = data.last_day;
    if (!day || !day.samples) return h("p", { class: "muted small", text: M.t("capacity.no_history") });
    var rows = [];
    var first = Date.parse(day.starts_at);
    for (var i = 0; i < 24; i++) {
      var stamp = new Date(first + i * 3600000).toISOString();
      var recorded = (data.hourly || []).find(function (row) { return Date.parse(row.starts_at) === Date.parse(stamp); });
      rows.push(recorded || { starts_at: stamp, mean: {}, peak: {} });
    }
    return h("details", { class: "capacity-history" },
      h("summary", { text: M.t("capacity.history") }),
      h("div", { class: "stack-sm" },
        h("p", { class: "muted small", text: M.t("capacity.window", { from: utc(day.starts_at), to: utc(day.ends_at) }) }),
        h("p", { class: "muted small", text: M.t("capacity.history_hint") }),
        chart(rows, M.t("capacity.cpu_load"), [{ key: "load_per_cpu_milli", label: M.t("capacity.cpu_load"), color: "receive" }], load, 1000),
        chart(rows, M.t("capacity.memory"), [{ key: "memory_used_per_mille", label: M.t("capacity.memory"), color: "receive" }], percent, 1000),
        chart(rows, M.t("capacity.network"), [
          { key: "rx_bytes_per_second", label: M.t("capacity.rx"), color: "receive" },
          { key: "tx_bytes_per_second", label: M.t("capacity.tx"), color: "send" },
        ], rate, 1),
        h("dl", { class: "facts" }, fact("capacity.day_rx", bytes(day.rx_bytes)), fact("capacity.day_tx", bytes(day.tx_bytes))),
        h("p", { class: "muted small", text: M.t("capacity.coverage", { covered: M.uptime(day.covered_seconds) }) })));
  }

  function chart(rows, title, series, format, minimum) {
    var maximum = minimum, observedPeak = null;
    rows.forEach(function (row) { series.forEach(function (item) {
      var value = row.peak[item.key];
      if (value != null) {
        maximum = Math.max(maximum, value);
        observedPeak = observedPeak == null ? value : Math.max(observedPeak, value);
      }
    }); });
    var slot = 600 / rows.length;
    var marks = [];
    rows.forEach(function (row, index) {
      series.forEach(function (item, direction) {
        var value = row.mean[item.key];
        var peak = row.peak[item.key];
        var width = slot * 0.7 / series.length;
        var x = index * slot + slot * 0.15 + direction * width;
        var hour = utc(row.starts_at);
        if (value == null) {
          var absent = M.t("capacity.missing_point", { hour: hour, series: item.label });
          marks.push(h("rect", { class: "capacity-gap", x: x, y: 8, width: width - 1, height: 64,
            role: "img", "aria-label": absent }, h("title", { text: absent })));
          return;
        }
        var size = Math.max(1, value * 64 / maximum);
        var label = M.t("capacity.point", { hour: hour, series: item.label, mean: format(value), peak: format(peak) });
        marks.push(h("rect", { class: "capacity-bar capacity-" + item.color, x: x, y: 72 - size,
          width: width - 1, height: size, rx: 1, "data-value": value, role: "img", tabindex: "0", "aria-label": label },
          h("title", { text: label })));
        if (peak != null) marks.push(h("line", { class: "capacity-peak", x1: x, x2: x + width - 1,
          y1: 72 - peak * 64 / maximum, y2: 72 - peak * 64 / maximum, "aria-hidden": "true" }));
      });
    });
    return h("div", { class: "capacity-plot", dir: "ltr" },
      h("div", { class: "capacity-legend" }, h("h4", { text: title }),
        h("span", { class: "muted small", text: M.t("capacity.peak", { value: format(observedPeak) }) })),
      series.length > 1 ? h("p", { class: "capacity-legend small" }, series.map(function (item) {
        return h("span", { class: "capacity-key capacity-key-" + item.color, text: item.label });
      })) : null,
      h("svg", { viewBox: "0 0 600 80", class: "capacity-chart", role: "img", "aria-label": title },
        h("title", { text: title }),
        h("line", { class: "usage-grid", x1: 0, x2: 600, y1: 72, y2: 72 }), marks));
  }
})();
