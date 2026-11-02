/* Recorded access and format evidence, with private policy edits confined to this document. */
(function () {
  "use strict";
  var D = window.MeridianDemo, reviews = new Map(), membershipState = "", membershipHash;
  D.access = function (user) {
    var key = D.source.access_templates[user.profiles.slice().sort().join(",")];
    if (!key) D.fail("unknown_profile");
    var view = D.clone(D.responses[key]);
    view.username = user.username; view.profiles = D.clone(user.profiles);
    view.connections.forEach(function (entry) { entry.available = entry.available && user.status === "active"; });
    return view;
  };
  D.route("GET", "api/access-profiles", function () {
    var view = D.clone(D.responses["GET api/access-profiles"]);
    var state = JSON.stringify(D.users.map(function (user) { return [user.user_id, user.profiles]; }));
    if (!membershipHash) { membershipHash = view.access_hash; membershipState = state; }
    if (state !== membershipState) { membershipState = state; membershipHash = D.id() + D.id(); }
    view.access_hash = membershipHash;
    view.profiles.forEach(function (profile) { profile.members = D.users.filter(function (user) { return user.profiles.indexOf(profile.id) >= 0; }).length; });
    delete view.has_non_default_memberships;
    if (D.users.some(function (user) { return user.profiles.some(function (name) { return name !== "default"; }); })) view.has_non_default_memberships = true;
    return D.reply(view);
  });
  D.route("GET", "api/users/{id}/access", function (request) {
    var user = D.person(request.ids[0]);
    return user ? D.reply(D.access(user)) : D.error("not_found", "missing", 404);
  });
  D.resolvePolicy = function (user, override, responseOverride) {
    var catalog = D.responses["GET api/delivery-policies"], source = "fleet", profiles = [], name = catalog.default_policy;
    if (arguments.length < 2 || override === undefined) override = user.delivery_policy || null;
    if (override !== null) { name = override; source = "person"; }
    else if (responseOverride) { name = responseOverride; source = "response_rule"; }
    else {
      profiles = user.profiles.filter(function (profile) { return Object.hasOwn(catalog.profile_policies, profile); }).sort();
      var choices = Array.from(new Set(profiles.map(function (profile) { return catalog.profile_policies[profile]; })));
      if (choices.length > 1) D.fail("conflicting_delivery_policies", 409);
      if (choices.length) { name = choices[0]; source = "profile"; }
    }
    var policy = catalog.policies.find(function (policy) { return policy.id === name; });
    if (!policy) D.fail("unknown_delivery_policy");
    return Object.assign(D.clone(D.responses["GET api/users/" + D.source.page_person + "/delivery-policy"].effective), {
      policy: D.clone(policy), built_in: name === "ru-split" || name === "full", source: source, profiles: profiles,
    });
  };
  function policyState(user, override) {
    if (arguments.length < 2) override = user.delivery_policy || null;
    return Object.assign(D.clone(D.responses["GET api/users/" + D.source.page_person + "/delivery-policy"]), {
      username: user.username, override: override, effective: D.resolvePolicy(user, override),
    });
  }
  D.recorded("GET", "api/delivery-policies");
  D.route("GET", "api/users/{id}/delivery-policy", function (request) {
    var user = D.person(request.ids[0]);
    return user ? D.reply(policyState(user)) : D.error("not_found", "missing", 404);
  });
  D.route("PUT", "api/users/{id}/delivery-policy", function (request) {
    var user = D.person(request.ids[0]), body = request.body;
    if (!user) return D.error("not_found", "missing", 404);
    if (!Object.hasOwn(body, "policy") || body.confirm !== undefined && typeof body.confirm !== "boolean") D.fail("invalid_request");
    if (body.confirm) {
      var held = reviews.get(body.review_hash);
      if (!held || held.user_id !== user.user_id || JSON.stringify(held.profiles) !== JSON.stringify(user.profiles) ||
          JSON.stringify(held.review.before) !== JSON.stringify(policyState(user)) || body.policy !== held.review.after.override) D.fail("stale_delivery_policy", 409);
      if ((user.delivery_policy || null) !== body.policy) D.bump(user);
      if (body.policy === null) delete user.delivery_policy; else user.delivery_policy = body.policy;
      return D.reply(Object.assign(D.clone(held.review), { after: policyState(user), confirmed: true }));
    }
    var review = Object.assign(D.clone(D.responses["PUT api/users/" + D.source.page_person + "/delivery-policy"]), {
      before: policyState(user), after: policyState(user, body.policy), review_hash: D.id() + D.id(), confirmed: false,
    });
    reviews.set(review.review_hash, { user_id: user.user_id, profiles: D.clone(user.profiles), review: review });
    return D.reply(review);
  });

  function checkHeaders(body) {
    [["user_agent", 512], ["accept", 256]].forEach(function (pair) {
      var value = body[pair[0]] || "";
      if (typeof value !== "string" || Array.from(value).length > pair[1] || /[\x00-\x1f\x7f-\x9f\u2028\u2029]/.test(value)) D.fail("invalid_request");
    });
    if (!Object.hasOwn(D.source.response_decisions.default, body.suffix || "")) D.fail("invalid_request");
  }
  function literalMatches(condition, value) {
    var candidate = value.toLowerCase(), literal = condition.value.toLowerCase();
    return condition.operator === "equals" ? candidate === literal : condition.operator === "prefix" ? candidate.indexOf(literal) === 0 : candidate.indexOf(literal) >= 0;
  }
  function decision(body, user) {
    checkHeaders(body);
    var agent = body.user_agent || "", accept = body.accept || "", suffix = body.suffix || "", choice = "default";
    var rules = D.responses["GET api/settings"].response_rules || [];
    var custom = rules.find(function (rule) {
      return literalMatches(rule.match.user_agent, agent) && (!rule.match.accept || literalMatches(rule.match.accept, accept));
    });
    if (custom) choice = custom.name;
    else if (!suffix) {
      if (accept.indexOf("text/html") >= 0) choice = "accept";
      else {
        // Only visitor-supplied headers need selection here; response shapes remain recordings.
        var selectors = [
          [/^(?:flclash|rabbit|flowvy|murge|mihomo|prizrak-box|koala-clash|clash(?:-verge|-nyanpasu|x meta|[-.]?meta))/i, "mihomo"],
          [/^stash/i, "stash"], [/^(?:sfa|sfi|sfm|sft|karing|singbox|hiddify)/i, "singbox"], [/^clash/i, "clash"],
          [/^(?:[Ss]treisand|Happ\/|INCY\/|ktor-client|V2Box|io\.github\.saeeddev94\.xray\/|v2rayNG\/\d+\.\d+\.\d+|v2rayN\/\d+\.\d+\.\d+|v2plus\/\d+\.\d+\.\d+)/, "xray-json"],
        ];
        var selected = selectors.find(function (entry) { return entry[0].test(agent.trim()); });
        if (selected) choice = selected[1];
      }
    }
    var key = D.source.response_decisions[choice] && D.source.response_decisions[choice][suffix];
    if (!key) D.fail("demo_unavailable", 409);
    var view = D.clone(D.responses[key]);
    if (view.policy) view.policy = D.resolvePolicy(user || { profiles: [] }, undefined, view.policy.source === "response_rule" ? view.policy.policy.id : null);
    return view;
  }
  D.route("POST", "api/response-rules/test", function (request) {
    var user = request.body.person && D.person(request.body.person);
    if (request.body.person && !user) return D.error("not_found", "missing", 404);
    return D.reply(decision(request.body, user));
  });
  function staleClient(agent) {
    if (/^NekoBox\/PC\//i.test(agent)) return "abandoned";
    var version = /^v2rayNG\/(\d+)\.(\d+)(?:\.(\d+))?/i.exec(agent);
    if (!version) return null;
    var parts = version.slice(1).map(function (value) { return Number(value || 0); }), minimum = [1, 9, 17];
    for (var index = 0; index < 3; index++) { if (parts[index] !== minimum[index]) return parts[index] < minimum[index] ? "outdated" : null; }
    return null;
  }
  function preview(body) {
    var user = D.person(body.username);
    if (!user) D.fail("not_found", 404);
    D.enforce(user);
    var selected = decision(body, user), view;
    if (selected.block) view = D.clone(D.responses["POST api/subscription/preview?case=blocked"]);
    else {
      var mode = selected.format + (selected.format === "browser" ? ":" + selected.response_kind : "");
      var key = D.source.subscription_templates[user.profiles.slice().sort().join(",")][selected.policy.policy.id][mode];
      view = D.clone(D.responses[key]);
      view.delivery.effective = D.clone(selected.policy);
      var allowed = D.access(user).connections.map(function (connection) { return connection.id; });
      if (user.status !== "active") {
        view.omitted = view.included.concat(view.omitted).map(function (entry) {
          var omitted = D.clone(entry);
          omitted.reason = allowed.indexOf(entry.id) >= 0 ? "status_placeholder" : "access_profile";
          omitted.restriction = null;
          return omitted;
        });
        view.included = [];
      }
      view.placeholder = user.status !== "active" ? user.status : view.included.length ? null : "no_connections";
      view.protocols = D.source.protocol_order.filter(function (protocol) { return view.included.some(function (entry) { return entry.protocol === protocol; }); });
      view.stale_client = selected.format !== "browser" && !view.placeholder ? staleClient((body.user_agent || "").trim()) : null;
      var headers = view.response_headers;
      if (selected.format !== "browser") {
        headers["content-disposition"] = "attachment; filename=" + user.username;
        headers["subscription-userinfo"] = "upload=" + user.period_traffic.upload_bytes + "; download=" + user.period_traffic.download_bytes +
          "; total=" + user.traffic_limit_bytes + "; expire=" + (user.expire_at ? Math.floor(Date.parse(user.expire_at) / 1000) : 0);
      }
      if (user.schedule.next_reset_at && selected.response_kind !== "page") headers["subscription-refill-date"] = String(Math.floor(Date.parse(user.schedule.next_reset_at) / 1000));
      else delete headers["subscription-refill-date"];
      if (D.responses["GET api/privacy"].settings.device_limits !== "off" && selected.format !== "browser" &&
          !(body.accept || "").includes("text/html") && !/^Mozilla\//i.test(body.user_agent || "")) headers["x-hwid-active"] = "true";
      else delete headers["x-hwid-active"];
      if (!D.responses["GET api/notice"].notice) delete headers.announce;
      Object.keys(selected.headers || {}).forEach(function (name) { if (selected.headers[name]) headers[name] = selected.headers[name]; else delete headers[name]; });
      delete view.explanation;
      if (body.destination) view.explanation = explain(body.destination, selected.format, selected.policy, view.delivery, user.status === "active", !!view.included.length);
    }
    Object.assign(view, { username: user.username, status: user.status, format: selected.format, selection: selected.selection, response_kind: selected.response_kind });
    delete view.response_rule;
    if (selected.selection.rule === "custom" || selected.block) view.response_rule = selected;
    return view;
  }
  D.route("POST", "api/subscription/preview", function (request) { return D.reply(preview(request.body)); });

  function ip(host) {
    if (/^\d+\.\d+\.\d+\.\d+$/.test(host)) {
      var bytes = host.split(".").map(Number);
      if (bytes.some(function (byte) { return byte > 255; })) return null;
      return { version: 4, value: bytes.reduce(function (value, byte) { return value * 256n + BigInt(byte); }, 0n) };
    }
    if (host.indexOf(":") < 0) return null;
    var sides = host.split("::"), left = sides[0] ? sides[0].split(":") : [], right = sides[1] ? sides[1].split(":") : [];
    var groups = sides.length === 1 ? left : left.concat(Array(8 - left.length - right.length).fill("0"), right);
    if (groups.length !== 8 || groups.some(function (value) { return !/^[0-9a-f]{1,4}$/i.test(value); })) return null;
    return { version: 6, value: groups.reduce(function (value, group) { return value * 65536n + BigInt(parseInt(group, 16)); }, 0n) };
  }
  function inNetwork(address, cidr) {
    var parts = cidr.split("/"), network = ip(parts[0]);
    if (!network || network.version !== address.version) return false;
    var shift = BigInt((address.version === 4 ? 32 : 128) - Number(parts[1]));
    return address.value >> shift === network.value >> shift;
  }
  function privateIp(address) {
    var ranges = address.version === 4 ? ["10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "127.0.0.0/8", "169.254.0.0/16", "224.0.0.0/4"] : ["fc00::/7", "fe80::/10", "ff00::/8"];
    return address.value === 0n || address.version === 4 && address.value === 4294967295n || address.version === 6 && address.value === 1n || ranges.some(function (cidr) { return inNetwork(address, cidr); });
  }
  function destination(value) {
    if (typeof value !== "string" || !value || value.length > 512 || value !== value.trim() || value.includes("%")) D.fail("invalid_request");
    var host, port = null, network = "tcp";
    try {
      var bare = ip(value);
      if (bare) host = value.toLowerCase();
      else {
        var parsed = new URL(value.includes("://") ? value : "tcp://" + value);
        if (!["tcp:", "udp:"].includes(parsed.protocol) || parsed.pathname || parsed.search || parsed.hash || parsed.username || parsed.password) D.fail("invalid_request");
        host = parsed.hostname.replace(/^\[|\]$/g, "").toLowerCase();
        if (!host || !ip(host) && !/^[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?$/.test(host)) D.fail("invalid_request");
        if (parsed.port) { port = Number(parsed.port); if (port < 1 || port > 65535) D.fail("invalid_request"); }
        network = parsed.protocol === "udp:" ? "udp" : "tcp";
      }
    } catch (error) { D.fail("invalid_request"); }
    return { host: host, port: port, network: network, protocol: "other" };
  }
  function explain(value, format, policy, delivery, active, available) {
    var target = destination(value), evidence = delivery.formats.find(function (entry) { return entry.format === format; });
    var rules = evidence ? evidence.rules : [], address = ip(target.host);
    var result = Object.assign(D.clone(D.responses["POST api/delivery-policies/explain"]), {
      destination: target, format: format, policy: policy.policy.id, status: "default", action: { kind: "proxy" },
      rule: null, reason: "default_proxy", omitted: evidence ? evidence.omitted : [], geodata_available: false,
    });
    if (!rules.length || !active || !available) return Object.assign(result, { status: "unavailable", action: null, reason: !rules.length ? "rules_not_carried" : !active ? "inactive_person" : "no_connections" });
    for (var index = 0; index < rules.length; index++) {
      var rule = rules[index];
      if (rule.networks.length && !rule.networks.includes(target.network) || rule.protocols.length && !rule.protocols.includes(target.protocol)) continue;
      if (rule.ports.length && target.port !== null && !rule.ports.some(function (range) { return target.port >= range.start && target.port <= range.end; })) continue;
      var matches = [], unknown = false;
      rule.domains.forEach(function (text) {
        if (address) { matches.push(false); return; }
        var colon = text.indexOf(":"), kind = text.slice(0, colon), match = text.slice(colon + 1);
        if (kind === "domain") matches.push(target.host === match || target.host.endsWith("." + match));
        else if (kind === "full") matches.push(target.host === match);
        else if (kind === "keyword") matches.push(target.host.includes(match));
        else unknown = true;
      });
      rule.ips.forEach(function (text) {
        if (!address) { matches.push(false); return; }
        if (text === "geoip:private" && format === "singbox") matches.push(privateIp(address));
        else if (text.startsWith("geoip:")) unknown = true;
        else matches.push(inNetwork(address, text));
      });
      if (!matches.includes(true) && unknown) return Object.assign(result, { status: "unknown", action: null, rule: rule.number, reason: "geodata_unavailable" });
      if (matches.length && !matches.includes(true)) continue;
      if (rule.ports.length && target.port === null) return Object.assign(result, { status: "unknown", action: null, rule: rule.number, reason: "port_required" });
      return Object.assign(result, { status: "matched", action: rule.action, rule: rule.number, reason: "rule_matched" });
    }
    return result;
  }
  D.route("POST", "api/users/{id}/rotate-credentials", function (request) {
    var user = D.person(request.ids[0]);
    if (!user) return D.error("not_found", "missing", 404);
    if (request.body.cut_off) return D.error("demo_unavailable", "unavailable", 409);
    if (user.credential_generation !== request.body.expected_generation) D.fail("credentials_changed", 409);
    var recording = D.source.key_rotations[user.username] || D.source.key_rotations.DemoTemplate;
    var original = D.initial["GET api/users/" + user.username] || D.source.responses["POST api/users"];
    var keep = D.share(user), useOriginal = user.uuid === recording.result.user.uuid;
    user.uuid = useOriginal ? original.uuid : recording.result.user.uuid;
    user.credential_generation++; D.bump(user);
    var share = D.clone(useOriginal ? D.source.active_shares[user.username] || D.source.active_shares.DemoTemplate : recording.share);
    Object.assign(share, { username: user.username, subscription_url: keep.subscription_url, page_url: keep.page_url, qr_svg: keep.qr_svg, formats: keep.formats });
    D.currentShares[user.username] = share;
    var result = D.clone(recording.result);
    result.user = D.clone(user); result.flow_blocks = D.flowBlocks(user, result.flow_blocks);
    D.emit("user.credentials_rotated", user.username);
    return D.reply(result);
  });
})();
