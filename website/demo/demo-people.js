/* Interactive people queries reuse recorded row shapes and the service's facet catalogs. */
(function () {
  "use strict";
  var D = window.MeridianDemo, cursors = new Map();
  var facets = ["status", "tag", "profile", "expires", "quota", "never", "attention", "lifecycle"];
  var orders = ["asc", "desc"], sorts = ["name", "usage", "expiry", "activity"];
  var statuses = ["active", "disabled", "expired", "limited"];
  var lifecycles = ["pending", "renewing", "fixed", "unlimited"];
  function invalid() { D.fail("invalid_request"); }
  function integer(value, min, max) { return Number.isSafeInteger(value) && value >= min && value <= max; }
  function compare(left, right) { return left < right ? -1 : left > right ? 1 : 0; }
  function same(left, right) { return JSON.stringify(left) === JSON.stringify(right); }
  function normalize(values) {
    var query = Object.assign({ q: "", status: "", tag: "", profile: "", expires: null, quota: null, never: false,
      attention: "", lifecycle: "", sort: "name", order: null, limit: 100, cursor: null }, values);
    if (Object.keys(values).some(function (key) { return !Object.hasOwn(query, key) || ["q"].concat(facets, ["sort", "order", "limit", "cursor"]).indexOf(key) < 0; })) invalid();
    if (typeof query.q !== "string" || query.q.length > 256) invalid();
    query.q = query.q.trim();
    if (typeof query.tag !== "string" || query.tag && query.tag !== "none" && !/^[A-Z0-9_]{1,16}$/.test(query.tag)) invalid();
    if (typeof query.profile !== "string" || query.profile && !/^[a-z0-9][a-z0-9_-]{0,47}$/.test(query.profile)) invalid();
    if ([""].concat(statuses).indexOf(query.status) < 0 || [""].concat(lifecycles).indexOf(query.lifecycle) < 0 ||
        ["", "all", "expiring", "quota", "unused", "stopped"].indexOf(query.attention) < 0 || sorts.indexOf(query.sort) < 0) invalid();
    query.order = query.order || (query.sort === "usage" || query.sort === "activity" ? "desc" : "asc");
    if (orders.indexOf(query.order) < 0 || typeof query.never !== "boolean" || !integer(query.limit, 1, 500)) invalid();
    if (query.expires !== null && !integer(query.expires, 1, 365) || query.quota !== null && !integer(query.quota, 1, 100)) invalid();
    if (query.cursor !== null && (typeof query.cursor !== "string" || query.cursor.length > 2048)) invalid();
    return query;
  }
  function fromParams(params) {
    var values = {};
    params.forEach(function (value, key) {
      if (params.getAll(key).length !== 1) invalid();
      if (["expires", "quota", "limit"].indexOf(key) >= 0) {
        if (value && !/^\d+$/.test(value)) invalid();
        value = value ? Number(value) : null;
      } else if (key === "never") {
        if (value !== "0" && value !== "1") invalid();
        value = value === "1";
      } else if (key === "cursor" || key === "order") value = value || null;
      values[key] = value;
    });
    return normalize(values);
  }
  function selection(query) {
    var view = {};
    ["q"].concat(facets, ["sort", "order"]).forEach(function (key) { view[key] = query[key]; });
    return JSON.stringify(view);
  }
  function lifecycle(user) {
    return !user.expire_at && user.activation_duration_days !== null && !user.first_traffic_at ? "pending" :
      user.renewal_interval_days !== null || user.renewal_interval_months !== null ? "renewing" : user.expire_at ? "fixed" : "unlimited";
  }
  function row(user) {
    D.enforce(user);
    var view = {};
    D.source.people_fields.forEach(function (key) { if (Object.hasOwn(user, key)) view[key] = D.clone(user[key]); });
    var fleet = D.responses["GET ui/api/fleet"];
    var name = Object.hasOwn(user, "display_name") ? user.display_name : fleet.display_names[user.username];
    return Object.assign(view, { display_name: name || user.username, row_version: D.versions[user.username] || 1 });
  }
  function matches(user, query, moment, omit) {
    var name = user.username.toLowerCase(), display = user.display_name.toLowerCase();
    if (query.q && name.indexOf(query.q.toLowerCase()) < 0 && display.indexOf(query.q.toLowerCase()) < 0) return false;
    return facets.every(function (key) {
      if (key === omit || !query[key]) return true;
      if (key === "status") return user.status === query.status;
      if (key === "tag") return user.tag === (query.tag === "none" ? "" : query.tag);
      if (key === "profile") return user.profiles.indexOf(query.profile) >= 0;
      if (key === "expires") { var expiry = Date.parse(user.expire_at); return expiry >= moment && expiry <= moment + query.expires * 86400000; }
      if (key === "quota") return user.quota_used_percent !== null && user.quota_used_percent >= query.quota;
      if (key === "never") return user.never_used;
      if (key === "lifecycle") return lifecycle(user) === query.lifecycle;
      return query.attention === "all" ? !!user.attention_reasons.length : user.attention_reasons.indexOf(query.attention) >= 0;
    });
  }
  function counts(rows, query, moment) {
    var view = D.clone(D.responses["GET api/people"].facets);
    ["status", "tag", "profile", "attention", "lifecycle"].forEach(function (key) { view[key] = {}; });
    ["status", "tag", "lifecycle"].forEach(function (key) {
      var values = new Set(rows.map(function (user) { return key === "lifecycle" ? lifecycle(user) : key === "tag" ? user.tag || "none" : user.status; }));
      (key === "status" ? statuses : key === "lifecycle" ? lifecycles : []).forEach(function (value) { values.add(value); });
      values.forEach(function (value) {
        view[key][value] = rows.filter(function (user) {
          return matches(user, query, moment, key) && (key === "lifecycle" ? lifecycle(user) : key === "tag" ? user.tag || "none" : user.status) === value;
        }).length;
      });
      view[key][""] = rows.filter(function (user) { return matches(user, query, moment, key); }).length;
    });
    var profileRows = rows.filter(function (user) { return matches(user, query, moment, "profile"); });
    view.profile[""] = profileRows.length;
    D.responses["GET api/access-profiles"].profiles.forEach(function (profile) {
      view.profile[profile.id] = profileRows.filter(function (user) { return user.profiles.indexOf(profile.id) >= 0; }).length;
    });
    ["", "all", "expiring", "quota", "unused", "stopped"].forEach(function (reason) {
      view.attention[reason] = rows.filter(function (user) {
        return matches(user, query, moment, "attention") && (!reason || reason === "all" ? !reason || !!user.attention_reasons.length : user.attention_reasons.indexOf(reason) >= 0);
      }).length;
    });
    ["expires", "quota", "never"].forEach(function (key) {
      var filter = Object.assign({}, query);
      filter[key] = key === "expires" ? query.expires || 7 : key === "quota" ? query.quota || 80 : true;
      view[key] = rows.filter(function (user) { return matches(user, filter, moment); }).length;
    });
    return view;
  }
  function sortKey(user, query) {
    var key = query.sort === "name" ? user.display_name.toLowerCase() : query.sort === "usage" ? user.used_traffic_bytes :
      query.sort === "expiry" ? user.expire_at && Date.parse(user.expire_at) : user.online_at && Date.parse(user.online_at);
    return [key === null ? 1 : 0, key === null ? 0 : key, user.username];
  }
  function compareKeys(left, right, query) {
    return compare(left[0], right[0]) || compare(left[1], right[1]) * (query.order === "desc" ? -1 : 1) || compare(left[2], right[2]);
  }
  D.peopleQuery = function (values, all) {
    var query = normalize(values), observed = Date.now(), held = null;
    var ids = D.users.map(function (user) { return user.user_id; });
    if (query.cursor) {
      held = cursors.get(query.cursor);
      if (!held || held.selection !== selection(query) || Date.now() - held.observed > 900000) invalid();
      observed = held.observed; ids = held.ids;
    }
    var rows = D.users.filter(function (user) { return ids.indexOf(user.user_id) >= 0; }).map(row);
    var filtered = rows.filter(function (user) { return matches(user, query, observed); });
    filtered.sort(function (left, right) { return compareKeys(sortKey(left, query), sortKey(right, query), query); });
    if (all) return filtered;
    var pending = held ? filtered.filter(function (user) { return compareKeys(sortKey(user, query), held.last, query) > 0; }) : filtered;
    var page = pending.slice(0, query.limit), cursor = null;
    if (pending.length > query.limit) {
      cursor = D.id();
      cursors.set(cursor, { selection: selection(query), observed: observed, ids: ids, last: sortKey(page[page.length - 1], query) });
      cursors.forEach(function (entry, token) { if (Date.now() - entry.observed > 900000) cursors.delete(token); });
    }
    return Object.assign(D.clone(D.responses["GET api/people"]), { users: page, total: filtered.length,
      population: rows.length, facets: counts(rows, query, observed), next_cursor: cursor, observed_at: D.iso(observed) });
  };
  D.route("GET", "api/people", function (request) { return D.reply(D.peopleQuery(fromParams(request.params))); });
  D.route("GET", "api/attention", function () {
    var view = D.clone(D.responses["GET api/attention"]);
    view.observed_at = D.now();
    view.people = D.users.map(row).filter(function (user) { return user.attention_reasons.length; }).map(function (user) {
      return { username: user.username, reasons: user.attention_reasons };
    });
    return D.reply(view);
  });

  D.monthsAfter = function (base, months) {
    var date = new Date(base), day = date.getUTCDate();
    date.setUTCDate(1); date.setUTCMonth(date.getUTCMonth() + months);
    var end = new Date(date); end.setUTCMonth(end.getUTCMonth() + 1, 0);
    date.setUTCDate(Math.min(day, end.getUTCDate()));
    return date.getTime();
  };
  D.periodStart = function (user, moment) {
    if (user.traffic_limit_strategy === "no_reset") return null;
    var date = new Date(moment); date.setUTCHours(0, 0, 0, 0);
    if (user.traffic_limit_strategy === "week") date.setUTCDate(date.getUTCDate() - (date.getUTCDay() + 6) % 7);
    if (user.traffic_limit_strategy === "month") date.setUTCDate(1);
    if (user.traffic_limit_strategy === "month_rolling") {
      var day = user.traffic_reset_anchor_day || new Date(user.created_at).getUTCDate();
      date.setUTCDate(1);
      var end = new Date(date); end.setUTCMonth(end.getUTCMonth() + 1, 0);
      date.setUTCDate(Math.min(day, end.getUTCDate()));
      if (date.getTime() > moment) {
        date.setUTCDate(1); date.setUTCMonth(date.getUTCMonth() - 1);
        end = new Date(date); end.setUTCMonth(end.getUTCMonth() + 1, 0);
        date.setUTCDate(Math.min(day, end.getUTCDate()));
      }
    }
    return date.getTime();
  };
  function activationExpiry(user) {
    if (user.expire_at) return Date.parse(user.expire_at);
    return user.activation_duration_days && user.first_traffic_at
      ? Math.min(Date.parse(user.first_traffic_at) + user.activation_duration_days * 86400000, Date.UTC(D.source.finite_expiry_year, 0, 1) - 1000) : null;
  }
  function renewed(user, anchor, index) { return user.renewal_interval_days ? anchor + index * user.renewal_interval_days * 86400000 : D.monthsAfter(anchor, index * user.renewal_interval_months); }
  D.schedule = function (user) {
    var moment = Date.now(), expiry = activationExpiry(user), completed = user.renewals_completed || 0;
    var enabled = !!(user.renewal_interval_days || user.renewal_interval_months);
    var left = enabled && user.renewal_limit !== null ? Math.max(0, user.renewal_limit - completed) : null;
    var anchor = Date.parse(user.renewal_anchor_at) || expiry, due = 0;
    if (enabled && expiry) {
      while (expiry <= moment && (left === null || due < left) && new Date(renewed(user, anchor, completed + due + 1)).getUTCFullYear() < D.source.finite_expiry_year) {
        due++; expiry = renewed(user, anchor, completed + due);
      }
    }
    var periodic = D.periodStart(user, moment), nextPeriod = null;
    if (periodic !== null) {
      nextPeriod = user.traffic_limit_strategy === "day" ? periodic + 86400000 : user.traffic_limit_strategy === "week" ? periodic + 7 * 86400000 : D.monthsAfter(periodic, 1);
      if (user.traffic_limit_strategy === "month_rolling") {
        var next = new Date(periodic); next.setUTCDate(1); next.setUTCMonth(next.getUTCMonth() + 1);
        var end = new Date(next); end.setUTCMonth(end.getUTCMonth() + 1, 0);
        next.setUTCDate(Math.min(user.traffic_reset_anchor_day || new Date(user.created_at).getUTCDate(), end.getUTCDate()));
        nextPeriod = next.getTime();
      }
    }
    if (left !== null) left -= due;
    var renewal = enabled && expiry && (left === null || left > 0) && new Date(renewed(user, anchor, completed + due + 1)).getUTCFullYear() < D.source.finite_expiry_year ? expiry : null;
    var refills = [nextPeriod, renewal].filter(function (value) { return value !== null; });
    return Object.assign(D.clone(D.responses["POST api/users/preview"]), {
      activation_pending: !expiry && !!user.activation_duration_days, activation_duration_days: user.activation_duration_days,
      expire_at: expiry ? D.iso(expiry) : null, next_reset_at: refills.length ? D.iso(Math.min.apply(Math, refills)) : null,
      renewal_enabled: enabled, next_renewal_at: renewal ? D.iso(renewal) : null, renewals_left: left,
      renewals_due: due, renewals_completed: completed + due,
    });
  };
  function validateSchedule(user) {
    if (user.activation_duration_days !== null && user.expire_at === null && user.first_traffic_at === null) {
      if (user.lifetime_traffic_bytes > 0) D.fail("invalid_request", 400, [], "firstUseUnknown");
      if (new Date(Date.now() + user.activation_duration_days * 86400000).getUTCFullYear() >= D.source.finite_expiry_year) D.fail("invalid_request", 400, [], "scheduleRange");
    }
    if (user.renewals_completed && !user.renewal_anchor_at) invalid();
    if ((user.renewal_interval_days || user.renewal_interval_months) && user.renewal_anchor_at && user.expire_at &&
        renewed(user, Date.parse(user.renewal_anchor_at), user.renewals_completed) !== Date.parse(user.expire_at)) invalid();
    var preview = D.schedule(user);
    if (preview.renewal_enabled && preview.expire_at && preview.next_renewal_at === null && preview.renewals_left !== 0) D.fail("invalid_request", 400, [], "scheduleRange");
  }
  // account_lifecycle.enforce_user_schedules: materialize dates and reset once before status.
  D.catchUp = function (user, forceReset) {
    var schedule = D.schedule(user), start = D.periodStart(user, Date.now());
    var reset = !!(forceReset || schedule.renewals_due || start !== null && (!user.traffic_reset_at || Date.parse(user.traffic_reset_at) < start));
    if (schedule.renewals_due || schedule.expire_at !== user.expire_at) {
      var anchor = user.renewal_anchor_at || activationExpiry(user);
      user.renewal_anchor_at = schedule.renewal_enabled && anchor !== null ? typeof anchor === "string" ? anchor : D.iso(anchor) : null;
      user.expire_at = schedule.expire_at;
      user.renewals_completed = schedule.renewals_completed;
    }
    if (reset) {
      user.used_traffic_bytes = 0; user.traffic_reset_at = D.now();
      Object.keys(user.period_traffic).forEach(function (key) { user.period_traffic[key] = 0; });
    }
    return reset;
  };
  /* Close to Python's casefold: upper first so Straße and STRASSE meet. */
  function folded(value) { return String(value || "").toUpperCase().toLowerCase(); }
  /* As core.inputs: up to 40 printable characters, no commas; empty shows the username. */
  D.cleanName = function (value) {
    if (value === undefined) return "";
    if (typeof value !== "string") invalid();
    var name = value.trim();
    if (Array.from(name).length > 40 || name.indexOf(",") >= 0 || /[\p{Cc}\p{Cf}\p{Cs}\p{Co}\p{Cn}\p{Zl}\p{Zp}]|(?! )\p{Zs}/u.test(name)) invalid();
    return name;
  };
  /* As the service: a name or username may match nobody else's name or username, ignoring case. */
  D.nameTaken = function (username, displayName, except) {
    var wanted = [folded(username), folded(displayName)].filter(Boolean);
    return D.users.some(function (other) {
      return other.username !== except && [folded(other.username), folded(other.display_name)].some(function (name) {
        return name && wanted.indexOf(name) >= 0;
      });
    });
  };
  /* A Latin username for a typed name, as core.inputs: the name itself when it is one, else its Latin letters
   * (the service transliterates every script; the demo keeps what NFKD leaves), numbered around every name. */
  D.assignUsername = function (displayName) {
    var base = displayName;
    if (!(new RegExp("^" + D.source.username_pattern + "$")).test(base)) {
      base = displayName.normalize("NFKD").toLowerCase().replace(/[^a-z0-9_]+/g, "-").replace(/^[-_]+|[-_]+$/g, "");
      base = base.slice(0, 32).replace(/^[-_]+|[-_]+$/g, "");
      if (base.length < 3) base = ("user-" + base).replace(/-+$/, "");
    }
    var name = base, n = 1;
    while (D.nameTaken(name, "")) {
      var suffix = "-" + (++n);
      name = (base.slice(0, 32 - suffix.length).replace(/[-_]+$/, "") || "user") + suffix;
    }
    return name;
  };
  D.updateUser = function (user, changes) {
    if (Object.keys(changes).some(function (key) { return key !== "username" && D.source.user_writable_fields.indexOf(key) < 0; })) invalid();
    if (Object.hasOwn(changes, "username") && !(new RegExp("^" + D.source.username_pattern + "$")).test(changes.username)) invalid();
    var updated = D.clone(user);
    Object.keys(changes).forEach(function (field) {
      if (["status", "traffic_limit_bytes", "traffic_limit_strategy"].indexOf(field) < 0 || changes[field] !== null) updated[field] = D.clone(changes[field]);
    });
    if (statuses.indexOf(updated.status) < 0 || ["no_reset", "day", "week", "month", "month_rolling"].indexOf(updated.traffic_limit_strategy) < 0) invalid();
    if (changes.status && changes.status !== "active" && changes.status !== "disabled") invalid();
    if (!integer(updated.traffic_limit_bytes, 0, D.source.traffic_limit_max) || updated.device_limit !== null && !integer(updated.device_limit, 0, 100)) invalid();
    if (typeof updated.note !== "string" || Array.from(updated.note).length > D.source.note_limit || /[\x00-\x09\x0b-\x1f\x7f-\x9f]/.test(updated.note) || typeof updated.tag !== "string" || updated.tag && !/^[A-Z0-9_]{1,16}$/.test(updated.tag)) invalid();
    if (updated.language !== null && ["en", "ru", "fa", "zh"].indexOf(updated.language) < 0) invalid();
    updated.display_name = D.cleanName(updated.display_name);
    if (updated.display_name !== (user.display_name || "") && D.nameTaken("", updated.display_name, user.username)) D.fail("name_conflict", 409, [], "exists");
    [["activation_duration_days", 3650], ["renewal_interval_days", 3650], ["renewal_interval_months", 120], ["renewal_limit", 10000], ["traffic_reset_anchor_day", 31]].forEach(function (pair) {
      if (updated[pair[0]] !== null && !integer(updated[pair[0]], 1, pair[1])) invalid();
    });
    if (updated.expire_at !== null && (!Number.isFinite(Date.parse(updated.expire_at)) || new Date(updated.expire_at).getUTCFullYear() >= D.source.finite_expiry_year)) invalid();
    if (changes.activation_duration_days !== null && Object.hasOwn(changes, "activation_duration_days")) {
      if (changes.expire_at) invalid(); updated.expire_at = null;
    } else if (Object.hasOwn(changes, "expire_at")) updated.activation_duration_days = null;
    if (changes.renewal_interval_days) updated.renewal_interval_months = null;
    if (changes.renewal_interval_months) updated.renewal_interval_days = null;
    if (changes.renewal_interval_days && changes.renewal_interval_months) invalid();
    if (Object.hasOwn(changes, "expire_at") || Object.hasOwn(changes, "activation_duration_days") ||
        !same(user.renewal_interval_days, updated.renewal_interval_days) || !same(user.renewal_interval_months, updated.renewal_interval_months)) {
      updated.renewals_completed = 0;
      updated.renewal_anchor_at = updated.renewal_interval_days || updated.renewal_interval_months ? updated.expire_at : null;
      if (!updated.renewal_interval_days && !updated.renewal_interval_months) updated.renewal_limit = null;
    }
    if ((updated.renewal_interval_days || updated.renewal_interval_months) && !updated.expire_at && !updated.activation_duration_days ||
        updated.renewal_limit !== null && !updated.renewal_interval_days && !updated.renewal_interval_months) invalid();
    if (updated.traffic_reset_anchor_day === null) updated.traffic_reset_anchor_day = new Date(updated.created_at).getUTCDate();
    if (!Array.isArray(updated.profiles) || !updated.profiles.length || updated.profiles.length > 64 || new Set(updated.profiles).size !== updated.profiles.length) invalid();
    var catalog = D.responses["GET api/access-profiles"].profiles.map(function (profile) { return profile.id; });
    if (updated.profiles.some(function (profile) { return catalog.indexOf(profile) < 0; })) D.fail("unknown_profile");
    updated.profiles.sort();
    validateSchedule(updated);
    if (D.resolvePolicy) D.resolvePolicy(updated);
    return updated;
  };
  D.route("POST", "api/users/preview", function (request) {
    var changes = Object.assign({}, request.body), user = changes.username ? D.person(changes.username) : D.clone(D.responses["POST api/users"]);
    if (!user) return D.error("not_found", "missing", 404);
    if (!changes.username) { user.created_at = D.now(); user.expire_at = null; user.activation_duration_days = null; }
    delete changes.username;
    return D.reply(D.schedule(D.updateUser(user, changes)));
  });
})();
