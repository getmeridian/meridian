/* Read-only format explanations; request headers and results stay in this view. */
(function () {
  "use strict";
  var M = (window.M = window.M || {});
  var h = M.h;

  M.formatPreviewSection = function () {
    var person = h("input", { type: "text", maxlength: "64", autocomplete: "off", "data-preview": "person" });
    var agent = h("input", { type: "text", maxlength: "1024", autocomplete: "off", "data-preview": "user_agent" });
    var accept = h("input", { type: "text", maxlength: "512", autocomplete: "off", "data-preview": "accept" });
    var destination = h("input", { type: "text", maxlength: "512", autocomplete: "off", "data-preview": "destination" });
    var suffix = h("select", { "data-preview": "suffix" },
      ["", "info", "json", "v2ray-json", "mihomo", "clash", "stash", "singbox"].map(function (value) {
        return h("option", { value: value, text: value || M.t("preview.automatic") });
      }));
    var error = h("p", { class: "form-error", role: "alert" });
    var result = h("div", { class: "stack", "data-preview": "result", "aria-live": "polite" });
    var form = h("form", { class: "stack", novalidate: true },
      M.field(M.t("preview.person"), person, M.t("response_rules.person_hint")), M.field(M.t("preview.user_agent"), agent),
      M.field(M.t("preview.accept"), accept), M.field(M.t("preview.suffix"), suffix),
      M.field(M.t("policy.destination"), destination, M.t("policy.destination_hint")), error,
      h("div", { class: "row-actions" }, h("button", { type: "submit", class: "btn btn-primary btn-sm" }, M.t("preview.run"))));
    var pending = false;
    M.trackForm(form);
    form.addEventListener("submit", function (event) {
      event.preventDefault();
      if (pending) return;
      var username = person.value.trim();
      if (username.length > 64) { error.textContent = M.t("preview.invalid_person"); return; }
      if (!username && destination.value.trim()) { error.textContent = M.t("response_rules.destination_person"); return; }
      if (Array.from(agent.value).length > 512 || /[\x00-\x1f\x7f-\x9f\u2028\u2029]/.test(agent.value)) {
        error.textContent = M.t("preview.invalid_user_agent"); return;
      }
      if (Array.from(accept.value).length > 256 || /[\x00-\x1f\x7f-\x9f\u2028\u2029]/.test(accept.value)) {
        error.textContent = M.t("preview.invalid_accept"); return;
      }
      var body = { user_agent: agent.value, accept: accept.value, suffix: suffix.value };
      if (username) body.username = username;
      if (destination.value.trim()) body.destination = destination.value.trim();
      pending = true;
      M.busy(form, function () {
        return username ? M.api("POST", "api/subscription/preview", body) : M.api("POST", "api/response-rules/test", body);
      }).then(function (data) {
        if (!form.isConnected) return;
        draw(result, data);
      }, function () { /* explained by M.busy */ }).finally(function () { pending = false; });
    });
    return h("section", { class: "card" }, h("h2", { text: M.t("preview.title") }),
      h("p", { class: "muted small", text: M.t("preview.caption") }),
      M.learnMore(h("p", { text: M.t("preview.hint") })), form, result);
  };

  function draw(target, data) {
    var decision = data.response_rule || (data.reason ? data : null);
    var blocked = data.block || data.response_kind === "blocked";
    var kind = blocked ? M.t("response_rules.blocked") : data.format === "browser"
      ? M.t("preview.kind_" + (data.response_kind || (data.selection.suffix ? "account_info" : "page")))
      : M.t("preview.format_" + data.format);
    var rule = data.selection.rule === "custom"
      ? M.t("response_rules.custom", { number: data.selection.number, name: data.selection.name })
      : M.t("preview.rule_" + data.selection.rule);
    if (data.selection.family) rule += " · " + data.selection.family;
    if (data.selection.suffix) rule += " · /" + data.selection.suffix;
    var rows = (data.included || []).map(function (entry) {
      return entryRow(entry, M.t("preview.included"));
    });
    (data.omitted || []).forEach(function (entry) {
      var reason = M.t("preview.reason_" + entry.reason);
      if (entry.restriction) reason += " · " + M.t("preview.restriction_" + entry.restriction);
      rows.push(entryRow(entry, reason));
    });
    var headers = h("dl", { class: "stack-sm mono small", dir: "ltr" });
    Object.keys(data.response_headers || {}).forEach(function (name) {
      headers.appendChild(h("div", {}, h("dt", { text: name }), h("dd", { class: "link-text", text: data.response_headers[name] })));
    });
    if (decision && !data.response_headers) Object.keys(decision.headers || {}).forEach(function (name) {
      headers.appendChild(h("div", {}, h("dt", { text: name }), h("dd", { class: "link-text", text: decision.headers[name] })));
    });
    M.mount(target, [
      h("p", { class: "row-title", text: kind }), h("p", { class: "muted small", text: rule }),
      decision ? h("p", { class: "hint", text: M.t("response_rules.reason_" + decision.reason) }) : null,
      blocked ? h("p", { class: "hint", text: M.t("response_rules.refusal") }) : null,
      decision ? (decision.matched_conditions || []).map(function (condition) {
        return h("p", { class: "small", text: M.t("response_rules.condition", {
          header: condition.header, operator: M.t("response_rules.operator_" + condition.operator),
        }) });
      }) : null,
      decision && decision.fallback ? h("p", { class: "hint", text: M.t("response_rules.fallback", {
        rule: M.t("preview.rule_" + decision.fallback.rule),
      }) }) : null,
      decision && decision.policy ? h("p", { class: "small", text: M.t("response_rules.policy", {
        policy: decision.policy.policy.id, source: M.t("delivery.source_" + decision.policy.source),
      }) }) : null,
      h("p", {}, M.t("preview.protocols") + ": ", h("bdi", { text: (data.protocols || []).join(", ") || M.t("preview.none") })),
      data.placeholder ? h("p", { class: "muted small", text: M.t("preview.placeholder_" + data.placeholder) }) : null,
      data.stale_client ? h("p", { class: "muted small", text: M.t("preview.stale_client_" + data.stale_client) }) : null,
      M.policyExplanation ? M.policyExplanation(data.explanation) : null,
      M.policyPreview ? M.policyPreview(data.delivery) : null,
      h("div", { class: "list list-plain" }, rows),
      h("details", {}, h("summary", { text: M.t("preview.headers") }), headers),
    ]);
  }

  function entryRow(entry, reason) {
    return h("div", { class: "row row-compact" }, h("div", { class: "row-main" },
      h("p", { class: "row-title", dir: "auto", text: entry.remark }),
      h("p", { class: "row-sub" }, h("bdi", { text: entry.protocol }), " · ", reason)));
  }
})();
