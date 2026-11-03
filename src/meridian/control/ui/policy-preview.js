/* Effective policy details share the service's format projection and explanation. */
(function () {
  "use strict";
  var M = (window.M = window.M || {}), h = M.h;

  M.policyPreview = function (data) {
    if (!data) return null;
    var effective = data.effective;
    return h("section", { class: "stack", "data-policy-preview": "" },
      h("h3", { text: M.t("policy.title") }),
      h("p", {}, h("bdi", { text: effective.policy.id }), " · ", M.t("delivery.source_" + effective.source)),
      h("p", { class: "hint", text: M.t("policy.geodata") }),
      data.formats.map(function (format) {
        var rules = h("ol", { class: "stack-sm" });
        (format.rules || []).forEach(function (rule) {
          var match = [].concat(rule.domains, rule.ips, rule.networks,
            (rule.ports || []).map(function (port) { return port.start === port.end ? String(port.start) : port.start + "–" + port.end; }), rule.protocols).join(" · ");
          rules.appendChild(h("li", { value: rule.number }, M.arrowPair(match, action(rule.action))));
        });
        var omissions = (format.omitted || []).map(function (omission) {
          var text = M.t("policy.omit_" + omission.reason);
          if (omission.rule) text = M.t("policy.rule_number", { rule: omission.rule }) + ": " + text;
          if (omission.context) text += " · " + M.t("policy." + omission.context, { count: omission.profile_count });
          return h("li", { class: "hint", text: text });
        });
        var dns = format.dns ? format.dns.resolvers.map(function (resolver) {
          return h("li", {}, h("bdi", { text: resolver.transport + " · " + resolver.address + ":" + resolver.port }));
        }) : [];
        return h("details", {}, h("summary", { text: M.t("preview.format_" + format.format) }),
          rules, format.final_action ? h("p", {}, M.t("policy.final") + ": ", action(format.final_action)) : null,
          h("p", { class: "label", text: M.t("policy.dns_" + format.dns_source) }),
          h("ul", { class: "stack-sm" }, dns),
          format.dns ? h("p", { class: "hint", text: M.t(format.dns.fake_ip ? "policy.fake_ip" : "policy.real_ip") }) : null,
          h("ul", { class: "stack-sm" }, omissions));
      }));
  };

  function action(value) {
    return value.kind === "exit" ? M.t("policy.action_exit", { exit: value.exit }) : M.t("policy.action_" + value.kind);
  }

  M.policyExplanation = function (result) {
    if (!result) return null;
    var text;
    if (result.action) {
      var destination = result.destination.host + (result.destination.port ? ":" + result.destination.port : "");
      text = M.t(result.rule ? "policy.explained_rule" : "policy.explained_default", {
        destination: destination, action: action(result.action), rule: result.rule,
      });
    } else text = M.t("policy.explain_" + result.reason);
    return h("div", { class: "stack-sm", "data-policy-explanation": "" }, h("p", { text: text }),
      h("p", { class: "hint", text: M.t("policy.no_lookup") }));
  };
})();
