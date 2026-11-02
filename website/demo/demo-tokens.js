/* Owner delegation and token requests use the recorded catalog and generated model bounds. */
(function () {
  "use strict";
  var D = window.MeridianDemo;
  function invalid() { D.fail("invalid_request"); }
  function matches(value, pattern) { return new RegExp("(?:" + pattern + ")(?![\\s\\S])", "u").test(value); }
  function preview(body) {
    var schema = D.source.token_create_schema.properties, catalog = D.responses["GET api/token-capabilities"];
    if (!body || typeof body !== "object" || Array.isArray(body) || Object.keys(body).some(function (key) { return !Object.hasOwn(schema, key); })) invalid();
    if (typeof body.name !== "string" || Array.from(body.name).length < schema.name.minLength ||
        Array.from(body.name).length > schema.name.maxLength || !matches(body.name, schema.name.pattern)) invalid();
    var scope = body.scope === undefined ? null : body.scope, grants = body.capabilities === undefined ? null : body.capabilities;
    if ((scope === null) === (grants === null)) invalid();
    if (scope !== null) {
      if (typeof scope !== "string" || !Object.hasOwn(catalog.presets, scope)) invalid();
      grants = D.clone(catalog.presets[scope]);
    } else {
      var grantSchema = schema.capabilities.anyOf.find(function (part) { return part.type === "array"; });
      if (!Array.isArray(grants) || grants.length < grantSchema.minItems || grants.length > grantSchema.maxItems ||
          new Set(grants).size !== grants.length || grants.some(function (grant) { return grantSchema.items.enum.indexOf(grant) < 0; })) invalid();
      grants = D.clone(grants);
    }
    grants.sort();
    var expires = body.expires_at === undefined ? null : body.expires_at;
    var days = body.expires_in_days === undefined ? schema.expires_in_days.default : body.expires_in_days;
    var daysSchema = schema.expires_in_days.anyOf.find(function (part) { return part.type === "integer"; });
    if (days !== null && (!Number.isSafeInteger(days) || days < daysSchema.minimum || days > daysSchema.maximum)) invalid();
    if (Object.hasOwn(body, "never_expires") && typeof body.never_expires !== "boolean") invalid();
    if (expires !== null) {
      var expirySchema = schema.expires_at.anyOf.find(function (part) { return part.type === "string"; });
      if (typeof expires !== "string" || expires.length > expirySchema.maxLength || !matches(expires, expirySchema.pattern) ||
          !Number.isFinite(Date.parse(expires)) || new Date(expires).toISOString().slice(0, 19) !== expires.slice(0, 19)) invalid();
    }
    if (expires !== null || body.never_expires) {
      if (Object.hasOwn(body, "expires_in_days") && days !== null || expires !== null && body.never_expires) invalid();
      days = null;
    } else if (days === null) invalid();
    var permissions = grants.map(function (grant) { return catalog.capabilities.find(function (row) { return row.capability === grant; }); });
    if (scope === "admin" || permissions.some(function (permission) { return !permission || !permission.console_delegable; })) D.fail("insufficient_scope", 403);
    if (days !== null) expires = D.iso(Date.now() + days * 86400000);
    if (expires !== null && Date.parse(expires) <= Date.now()) invalid();
    return { scope: scope, capabilities: grants, expires_at: expires,
      permissions: permissions.map(function (permission) { return { capability: permission.capability, labels: D.clone(permission.labels) }; }) };
  }
  D.route("POST", "api/tokens/preview", function (request) { return D.reply(preview(request.body)); });
  D.route("POST", "api/tokens", function (request) {
    var reviewed = preview(request.body), token = D.clone(D.responses["POST api/tokens"]);
    Object.assign(token, { id: D.id().slice(0, 12), name: request.body.name, scope: reviewed.scope,
      capabilities: reviewed.capabilities, expires_at: reviewed.expires_at, created_at: D.now(), last_used_at: null, revoked_at: null });
    token.token = "demo-only-token-" + token.id + "-never-authorizes-anything";
    var metadata = D.clone(token); delete metadata.token;
    D.responses["GET api/tokens"].tokens.push(metadata);
    D.emit("token.created", token.name, { name: token.name, scope: token.scope });
    return D.reply(token);
  });
})();
