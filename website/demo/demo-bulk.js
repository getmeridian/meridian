/* Frozen in-memory reviews, atomic edits, receipts, and guarded ten-minute undo. */
(function () {
  "use strict";
  var D = window.MeridianDemo, previews = new Map(), receipts = new Map(), undoVersions = new Map();
  var parameters = {
    extend: ["days", "months"], expiry: ["expire_at"], quota: ["traffic_limit_bytes", "traffic_limit_strategy", "traffic_reset_anchor_day"],
    "reset-traffic": [], enable: [], disable: [], tag: ["tag"], devices: ["device_limit"],
    renewal: ["renewal_interval_days", "renewal_interval_months", "renewal_limit"], "set-profiles": ["profiles"], delete: [],
  };
  function fields(user) {
    var view = {};
    D.source.bulk_fields.forEach(function (key) { view[key] = D.clone(user[key]); });
    return view;
  }
  function changed(before, after) {
    return after === null ? ["delete"] : D.source.bulk_fields.filter(function (key) { return JSON.stringify(before[key]) !== JSON.stringify(after[key]); });
  }
  function counts(people) {
    return Object.assign(D.clone(D.responses["POST api/people/bulk/preview"].counts), {
      targeted: people.length, changed: people.filter(function (person) { return person.outcome === "changed"; }).length,
      unchanged: people.filter(function (person) { return person.outcome === "unchanged"; }).length,
      cli_required: people.filter(function (person) { return person.outcome === "cli_required"; }).length,
    });
  }
  function version(user) { return [user.user_id, D.versions[user.username]]; }
  function checkVersions(held) {
    var stale = Object.keys(held).filter(function (name) {
      var user = D.person(name);
      return !user || JSON.stringify(version(user)) !== JSON.stringify(held[name]);
    });
    if (stale.length) D.fail("stale_people", 409, stale);
  }
  function project(original, reset) {
    var user = D.clone(original), schedule = D.schedule(user), start = D.periodStart(user, Date.now());
    reset = !!(reset || schedule.renewals_due || start !== null && (!user.traffic_reset_at || Date.parse(user.traffic_reset_at) < start));
    if (schedule.renewals_due || schedule.expire_at !== user.expire_at) {
      user.renewal_anchor_at = user.renewal_interval_days || user.renewal_interval_months ? user.renewal_anchor_at || schedule.next_renewal_at || user.expire_at : null;
      user.expire_at = schedule.expire_at;
      user.renewals_completed = schedule.renewals_completed;
    }
    if (reset) {
      user.used_traffic_bytes = 0; user.traffic_reset_at = D.now();
      Object.keys(user.period_traffic).forEach(function (key) { user.period_traffic[key] = 0; });
    }
    D.enforce(user);
    return { user: user, reset: reset };
  }
  function validateAction(action) {
    if (!action || !Object.hasOwn(parameters, action.action)) D.fail("invalid_request");
    var allowed = parameters[action.action];
    if (Object.keys(action).some(function (key) { return key !== "action" && allowed.indexOf(key) < 0; })) D.fail("invalid_request");
    var required = { expiry: "expire_at", tag: "tag", devices: "device_limit", "set-profiles": "profiles" }[action.action];
    if (required && !Object.hasOwn(action, required)) D.fail("invalid_request");
    if (action.action === "extend") {
      if ((action.days === undefined) === (action.months === undefined)) D.fail("invalid_request");
      var value = action.days === undefined ? action.months : action.days;
      if (!Number.isInteger(value) || value < 1 || value > (action.days === undefined ? 120 : 3650)) D.fail("invalid_request");
    }
    if ((action.action === "quota" || action.action === "renewal") && Object.keys(action).length === 1) D.fail("invalid_request");
    if (Object.hasOwn(action, "traffic_limit_bytes") && action.traffic_limit_bytes === null) D.fail("invalid_request");
  }
  function applyAction(original, action) {
    var change = {};
    Object.keys(action).forEach(function (key) { if (["action", "days", "months"].indexOf(key) < 0) change[key] = D.clone(action[key]); });
    if (action.action === "extend") {
      if (!original.expire_at && original.activation_duration_days && !original.first_traffic_at) {
        var duration = action.days || Math.floor((D.monthsAfter(Date.now(), action.months) - Date.now()) / 86400000);
        change = { activation_duration_days: original.activation_duration_days + duration };
      } else {
        var base = Math.max(Date.now(), Date.parse(original.expire_at) || 0);
        change = { expire_at: D.iso(action.days ? base + action.days * 86400000 : D.monthsAfter(base, action.months)) };
      }
    } else if (action.action === "enable" || action.action === "disable") change = { status: action.action === "disable" ? "disabled" : "active" };
    if (action.action === "renewal" && !change.renewal_interval_days && !change.renewal_interval_months &&
        (Object.hasOwn(change, "renewal_interval_days") || Object.hasOwn(change, "renewal_interval_months"))) {
      change.renewal_interval_days = change.renewal_interval_months = change.renewal_limit = null;
    }
    return project(D.updateUser(original, change), action.action === "reset-traffic");
  }
  function membership(before, after) {
    var oldRefs = D.access(before).inbounds.map(function (inbound) { return inbound.inbound_ref; });
    var newRefs = D.access(after).inbounds.map(function (inbound) { return inbound.inbound_ref; });
    return { username: before.username, profiles: D.clone(after.profiles),
      gained: newRefs.filter(function (ref) { return oldRefs.indexOf(ref) < 0; }).sort(),
      lost: oldRefs.filter(function (ref) { return newRefs.indexOf(ref) < 0; }).sort() };
  }
  function cutOff(user, lost) {
    var inbounds = D.access(user).inbounds.filter(function (inbound) { return lost.indexOf(inbound.inbound_ref) >= 0; });
    var plan = D.clone(D.responses["GET api/users/" + D.source.page_person + "/cut-off"]);
    plan.nodes = plan.nodes.filter(function (node) { return inbounds.some(function (inbound) { return inbound.node_id === node.node_id; }); });
    plan.nodes.forEach(function (node) { node.inbound_tags = inbounds.filter(function (inbound) { return inbound.node_id === node.node_id; }).map(function (inbound) { return inbound.inbound_tag; }).sort(); });
    return plan;
  }
  function changedNodes(originals, effects) {
    var nodes = new Set();
    Object.keys(originals).forEach(function (name) {
      var before = originals[name], after = effects[name];
      var oldRefs = before.status === "active" ? D.access(before).inbounds : [];
      var newRefs = after && after.status === "active" ? D.access(after).inbounds : [];
      oldRefs.concat(newRefs).forEach(function (inbound) {
        if (oldRefs.some(function (entry) { return entry.inbound_ref === inbound.inbound_ref; }) !== newRefs.some(function (entry) { return entry.inbound_ref === inbound.inbound_ref; })) nodes.add(inbound.node_id);
      });
    });
    return nodes.size;
  }
  D.route("POST", "api/people/bulk/preview", function (request) {
    var body = request.body, action = body.action;
    validateAction(action);
    if ((body.usernames === undefined) === (body.query === undefined)) D.fail("invalid_request");
    var names;
    if (body.usernames !== undefined) {
      if (!Array.isArray(body.usernames) || !body.usernames.length || body.usernames.length > 50000 || new Set(body.usernames).size !== body.usernames.length) D.fail("invalid_request");
      names = body.usernames;
    } else {
      if (body.query.cursor) D.fail("invalid_request");
      names = D.peopleQuery(body.query, true).map(function (user) { return user.username; });
    }
    var people = [], versions = {}, originals = {}, effects = {}, reversible = ["delete", "reset-traffic"].indexOf(action.action) < 0;
    names.forEach(function (name) {
      var actual = D.person(name);
      if (!actual) D.fail("not_found", 404);
      var before = project(actual).user, after = null, warnings = [], cli = false;
      versions[name] = version(actual); originals[name] = before;
      if (action.action === "delete") {
        cli = D.responses["GET ui/api/fleet"].declared_users.indexOf(name) >= 0;
        if (cli) { after = before; warnings.push("topology_delete"); }
      } else {
        var effect = applyAction(before, action); after = effect.user;
        if (effect.reset) { reversible = false; warnings.push("period_reset"); }
        if (before.status === "disabled" && after.status === "disabled") warnings.push("disabled");
        if (action.action === "enable" && after.status === "expired") warnings.push("expiry_blocks_enable");
        if (action.action === "enable" && after.status === "limited") warnings.push("quota_blocks_enable");
      }
      effects[name] = after;
      var beforeFields = fields(before), afterFields = after && fields(after), changes = changed(beforeFields, afterFields);
      var person = Object.assign(D.clone(D.responses["POST api/people/bulk/preview"].people[0]), {
        username: name, display_name: D.responses["GET ui/api/fleet"].display_names[name] || name,
        row_version: versions[name][1], before: beforeFields, after: afterFields, changes: changes,
        outcome: cli ? "cli_required" : changes.length ? "changed" : "unchanged", cli_command: null, warnings: warnings,
      });
      delete person.access; delete person.cut_off;
      if (action.action === "set-profiles") { person.access = membership(before, after); person.cut_off = cutOff(before, person.access.lost); }
      people.push(person);
    });
    var result = Object.assign(D.clone(D.responses["POST api/people/bulk/preview"]), {
      id: D.id(), preview_hash: D.id() + D.id(), created_at: D.now(), expires_at: D.iso(Date.now() + 600000),
      action: D.clone(action), people: people, counts: counts(people), changed_nodes: changedNodes(originals, effects), reversible: reversible, warnings: [],
    });
    delete result.proposal;
    if (!reversible) result.warnings.push("irreversible");
    if (!result.counts.changed) result.warnings.push("no_change");
    var declared = people.filter(function (person) { return person.outcome === "cli_required"; }).map(function (person) { return person.username; });
    if (declared.length) {
      result.proposal = D.clone(D.responses["POST api/people/bulk/preview?case=declared-delete"].proposal);
      result.proposal.usernames = declared.sort();
    }
    previews.set(result.id, { result: result, versions: versions, effects: effects });
    if (previews.size > 16) previews.delete(previews.keys().next().value);
    return D.reply(result);
  });
  D.route("POST", "api/people/bulk/commit", function (request) {
    var body = request.body, receipt = receipts.get(body.preview_id), held = previews.get(body.preview_id);
    if (receipt) {
      if (receipt.preview_hash !== body.preview_hash) D.fail("preview_mismatch", 409);
      return D.reply(receipt);
    }
    if (!held || Date.parse(held.result.expires_at) < Date.now()) D.fail("preview_expired", 409);
    var review = held.result;
    if (review.preview_hash !== body.preview_hash) D.fail("preview_mismatch", 409);
    checkVersions(held.versions);
    var stale = review.people.filter(function (person) { return JSON.stringify(fields(project(D.person(person.username)).user)) !== JSON.stringify(person.before); }).map(function (person) { return person.username; });
    if (stale.length) D.fail("stale_people", 409, stale);
    // Validate every target before mutating the first, as the service transaction does.
    review.people.forEach(function (person) {
      if (person.outcome !== "changed") return;
      var actual = D.person(person.username), effect = held.effects[person.username];
      if (effect === null) D.users.splice(D.users.indexOf(actual), 1);
      else { Object.assign(actual, effect); D.bump(actual); }
    });
    receipt = Object.assign(D.clone(D.responses["POST api/people/bulk/commit"]), {
      id: review.id, preview_hash: review.preview_hash, action: review.action, committed_at: D.now(),
      people: D.clone(review.people), counts: D.clone(review.counts), changed_nodes: review.changed_nodes,
      undo_until: review.reversible && review.counts.changed ? D.iso(Date.now() + 600000) : null, undone_at: null, undo_people: null,
    });
    delete receipt.proposal;
    if (review.proposal) receipt.proposal = D.clone(review.proposal);
    receipts.set(receipt.id, receipt);
    var versions = {};
    review.people.forEach(function (person) { var user = D.person(person.username); if (user) versions[person.username] = version(user); });
    undoVersions.set(receipt.id, versions);
    D.emit("people.bulk", receipt.id, Object.assign({ action: review.action.action }, review.counts));
    return D.reply(receipt);
  });
  D.route("GET", "api/people/bulk/receipts/{id}", function (request) {
    var receipt = receipts.get(request.ids[0]);
    return receipt ? D.reply(receipt) : D.error("demo_unavailable", "unavailable", 409);
  });
  D.route("POST", "api/people/bulk/undo", function (request) {
    var receipt = receipts.get(request.body.receipt_id);
    if (!receipt || receipt.undone_at || !receipt.undo_until || Date.parse(receipt.undo_until) < Date.now()) D.fail("undo_refused", 409);
    checkVersions(undoVersions.get(receipt.id));
    var outcomes = [], effects = {};
    receipt.people.forEach(function (person) {
      var actual = D.person(person.username), inverse = D.clone(actual), before = fields(actual);
      if (person.outcome === "changed") {
        D.source.bulk_writable_fields.forEach(function (key) { if (key !== "status") inverse[key] = D.clone(person.before[key]); });
        if (receipt.action.action === "enable" || receipt.action.action === "disable") inverse.status = person.before.status === "disabled" ? "disabled" : "active";
      }
      var effect = project(inverse);
      if (effect.reset || effect.user.status !== inverse.status) D.fail("undo_refused", 409);
      D.resolvePolicy(effect.user);
      effects[person.username] = effect.user;
      var after = fields(effect.user), changes = changed(before, after);
      var outcome = Object.assign(D.clone(person), { before: before, after: after, changes: changes, outcome: changes.length ? "changed" : "unchanged", row_version: D.versions[person.username] });
      if (receipt.action.action === "set-profiles") { outcome.access = membership(actual, effect.user); outcome.cut_off = cutOff(actual, outcome.access.lost); }
      outcomes.push(outcome);
    });
    var updated = Object.assign(D.clone(receipt), { undo_people: outcomes });
    if (!request.body.dry_run) {
      outcomes.forEach(function (person) { if (person.outcome === "changed") { var user = D.person(person.username); Object.assign(user, effects[person.username]); D.bump(user); } });
      updated.undone_at = D.now(); receipts.set(updated.id, updated);
      D.emit("people.bulk", receipt.id, Object.assign({ action: "undo" }, counts(outcomes)));
    }
    return D.reply(updated);
  });
})();
