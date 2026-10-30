/* Website-only demo adapter. The console and connection-page assets are copied unchanged.
 * Responses start with a real service recording; every edit stays in this document's memory.
 * No browser address, device identifier, password, or API secret is collected or persisted. */
(function () {
  "use strict";
  var nativeFetch = window.fetch.bind(window);
  var script = new URL(document.currentScript.src, location.href);
  var consoleRoot = new URL("./", script).pathname;
  var demoRoot = consoleRoot.replace(/console\/$/, "");
  var inConsole = location.pathname.indexOf(consoleRoot) === 0;
  var query = new URLSearchParams(location.search);
  var languages = ["en", "ru", "fa", "zh"];
  var chosen = query.get("lang");
  var memory = Object.create(null);
  if (languages.indexOf(chosen) >= 0) {
    memory["mrd.lang"] = memory["meridian-lang"] = chosen;
    document.documentElement.lang = chosen;
    document.documentElement.dir = chosen === "fa" ? "rtl" : "ltr";
  }
  if (query.get("theme") === "dark" || query.get("theme") === "light") memory["mrd.theme"] = query.get("theme");
  // The real UI writes appearance choices to localStorage. In the demo that storage is memory too.
  var storage = {
    getItem: function (key) { return Object.hasOwn(memory, key) ? memory[key] : null; },
    setItem: function (key, value) { memory[key] = String(value); },
    removeItem: function (key) { delete memory[key]; },
    clear: function () { Object.keys(memory).forEach(function (key) { delete memory[key]; }); },
    key: function (index) { return Object.keys(memory)[index] || null; },
    get length() { return Object.keys(memory).length; },
  };
  Object.defineProperty(window, "localStorage", { value: storage });
  Object.defineProperty(window, "sessionStorage", { value: storage });
  // Static demos never register a worker or leave one behind in the visitor's browser.
  if (navigator.serviceWorker) navigator.serviceWorker.register = function () { return Promise.resolve({ active: null }); };

  var COPY = {
    en: {
      imports: "In the demo, apps import nothing: these links point at invented servers.",
      banner: "Demo: nothing here is real, changes reset when you reload.",
      console: "Owner console", page: "Connection page",
      unavailable: "This action is unavailable in the demo.",
      missing: "This person is no longer in the demo. Reload to reset.",
      declared: "This person is managed by the CLI. Choose a person added in the console.",
      load: "Demo data could not be loaded. Reload this page.",
    },
    ru: {
      imports: "В демо приложения ничего не импортируют: эти ссылки ведут на вымышленные серверы.",
      banner: "Демо: все данные вымышлены. После перезагрузки страницы изменения сбросятся.",
      console: "Консоль владельца", page: "Страница подключения",
      unavailable: "Это действие недоступно в демо.",
      missing: "Этого человека больше нет в демо. Перезагрузите страницу, чтобы вернуть исходные данные.",
      declared: "Этим человеком управляют через CLI. Выберите человека, добавленного в консоли.",
      load: "Не удалось загрузить данные демо. Перезагрузите страницу.",
    },
    fa: {
      imports: "در نسخهٔ نمایشی برنامه\u200cها چیزی وارد نمی\u200cکنند: این پیوندها به سرورهای ساختگی اشاره دارند.",
      banner: "نسخهٔ نمایشی: هیچ داده‌ای واقعی نیست؛ با بارگیری دوبارهٔ صفحه، تغییرات پاک می‌شوند.",
      console: "کنسول مالک", page: "صفحهٔ اتصال",
      unavailable: "این کار در نسخهٔ نمایشی در دسترس نیست.",
      missing: "این شخص دیگر در نسخهٔ نمایشی نیست. برای بازنشانی، صفحه را دوباره بارگیری کنید.",
      declared: "این شخص با CLI مدیریت می‌شود. شخصی را انتخاب کنید که در کنسول افزوده شده است.",
      load: "داده‌های نمایشی بارگیری نشدند. صفحه را دوباره بارگیری کنید.",
    },
    zh: {
      imports: "演示中应用不会导入任何内容：这些链接指向虚构的服务器。",
      banner: "演示：所有数据都是虚构的，重新加载页面后更改会重置。",
      console: "管理控制台", page: "连接页面",
      unavailable: "此操作在演示中不可用。",
      missing: "此用户已从演示中删除。重新加载页面即可重置。",
      declared: "此用户由 CLI 管理。请选择在控制台中添加的用户。",
      load: "无法加载演示数据，请重新加载页面。",
    },
  };
  function copy() { return COPY[document.documentElement.lang.split("-")[0]] || COPY.en; }
  function clone(value) { return JSON.parse(JSON.stringify(value)); }
  function reply(body, status) { return new Response(JSON.stringify(body), { status: status || 200, headers: { "Content-Type": "application/json", "Cache-Control": "no-store" } }); }
  function error(code, label, status) { return reply({ error: { code: code, message: copy()[label] || label } }, status || 400); }
  // The service writes whole-second UTC timestamps; keep the same spelling.
  function iso(ms) { return new Date(ms).toISOString().replace(/\.\d{3}Z$/, "Z"); }
  function now() { return iso(Date.now()); }
  var source, initial, responses, users, events, nextId, signedIn = true, rotations = {}, activeShares = {}, rotating = {}, pages = [], newId = 0;
  var ready = nativeFetch(new URL("demo-data.json", script), { cache: "no-store" }).then(function (response) {
    if (!response.ok) throw new Error(copy().load);
    return response.json();
  }).then(function (data) {
    var delta = Math.floor((Date.now() - Date.parse(data.generated_at)) / 1000) * 1000;
    var days = Math.floor(Date.now() / 86400000) - Math.floor(Date.parse(data.generated_at) / 86400000);
    function rebase(value, key) {
      if (Array.isArray(value)) return value.map(function (item) { return rebase(item, key); });
      if (value && typeof value === "object") {
        var result = {};
        Object.keys(value).forEach(function (field) { result[field] = rebase(value[field], field); });
        return result;
      }
      if (typeof value === "number" && /^(created_at|last_seen_at|session_expires_at)$/.test(key)) return value + Math.floor(delta / 1000);
      if (typeof value !== "string") return value;
      // 2099-12-31 is the service's "never expires" sentinel; pages test for it literally.
      if (/^\d{4}-\d\d-\d\dT/.test(value) && !/^2099-12-31T/.test(value)) {
        if (key === "period_start" || key === "period_end" || key === "counting_since" || key === "traffic_reset_at") {
          var period = new Date(value), current = new Date(), generated = new Date(data.generated_at);
          period.setUTCMonth(period.getUTCMonth() + (current.getUTCFullYear() - generated.getUTCFullYear()) * 12 + current.getUTCMonth() - generated.getUTCMonth());
          return iso(period.getTime());
        }
        return iso(Date.parse(value) + delta);
      }
      if (/^\d{4}-\d\d-\d\d$/.test(value)) return new Date(Date.parse(value) + days * 86400000).toISOString().slice(0, 10);
      return value;
    }
    source = data;
    responses = rebase(data.responses, "");
    initial = clone(responses);
    rotations = rebase(data.rotations, "");
    activeShares = rebase(data.active_shares || {}, "");
    // Only the recorded people have exported pages; other links stay inside the demo.
    pages = data.responses["GET api/users"].users.map(function (user) { return user.sub_token; });
    users = responses["GET api/users"].users;
    events = responses["GET api/events"].events;
    nextId = events.reduce(function (max, event) { return Math.max(max, event.id); }, 0);
  });

  function emit(kind, subject, detail) {
    events.push({ id: ++nextId, time: now(), kind: kind, actor: "owner", subject: subject, detail: detail || {} });
    if (events.length > 2000) events.shift();
  }
  function person(name) { return users.find(function (user) { return user.username === name; }); }
  function enforce(user) {
    if (user.status === "disabled") return;
    user.status = user.expire_at && Date.parse(user.expire_at) <= Date.now() ? "expired" :
      user.traffic_limit_bytes && user.used_traffic_bytes >= user.traffic_limit_bytes ? "limited" : "active";
  }
  function share(user) {
    var view = clone(responses["GET ui/api/people/" + user.username + "/share"] || responses["GET ui/api/people/DemoTemplate/share"]);
    var working = view.connections.some(function (entry) { return entry.available; });
    // People recorded while disabled or over their limit carry placeholders; enabling them shows real links.
    var spare = rotating[user.username] ? rotations[user.username] && rotations[user.username].share : activeShares[user.username];
    if (user.status === "active" && !working && spare) view = clone(spare);
    view.username = user.username;
    if (user.status !== "active") {
      view.connections.forEach(function (entry) { entry.available = false; entry.uri = ""; entry.qr_svg = ""; });
    }
    return view;
  }
  function devices(user) {
    var view = clone(responses["GET api/users/" + user.username + "/devices"] || responses["GET api/users/DemoTemplate/devices"]);
    view.username = user.username;
    view.level = responses["GET api/privacy"].settings.device_limits;
    view.limit = user.device_limit || responses["GET api/privacy"].settings.default_device_limit;
    if (view.level === "off") view.devices = [];
    if (view.level === "private") view.devices = view.devices.map(function (device) { return { device_key: device.device_key }; });
    view.count = view.devices.length;
    return view;
  }
  // notices.usage_text: six significant digits in binary units.
  function byteText(bytes) {
    var units = ["B", "KiB", "MiB", "GiB", "TiB"];
    var index = 0;
    while (bytes >= 1024 && index < units.length - 1) { bytes /= 1024; index++; }
    return Number(bytes.toPrecision(6)) + " " + units[index];
  }
  function readableBytes(count) {
    // browser.readable_bytes: two decimals in binary units; zero (unlimited) stays "0".
    if (count <= 0) return "0";
    var units = ["B", "KiB", "MiB", "GiB", "TiB", "PiB"], exponent = Math.min(Math.floor(Math.log(count) / Math.log(1024)), units.length - 1);
    return exponent === 0 ? count + " B" : (count / Math.pow(1024, exponent)).toFixed(2) + " " + units[exponent];
  }
  // Trim or pad a recorded daily series to the requested window, as the service answers.
  function fit(view, params) {
    var history = responses["GET api/privacy"].settings.history_days;
    var days = Math.max(1, Math.min(Number(params.get("days")) || 30, history));
    function resize(list) {
      var out = list.slice(-days);
      while (out.length < days) {
        var first = clone(out[0] || list[0]);
        Object.keys(first).forEach(function (key) { if (typeof first[key] === "number") first[key] = 0; });
        first.day = new Date(Date.parse((out[0] || list[0]).day) - 86400000).toISOString().slice(0, 10);
        out.unshift(first);
      }
      return out;
    }
    view.daily = resize(view.daily);
    (view.nodes || []).forEach(function (node) { node.daily = resize(node.daily); });
    return Object.assign(view, { days: days, history_days: history });
  }
  function noticeState(notice, name) {
    var user = name && person(name);
    if (name && !user) return null;
    var active = !!notice && (!notice.until || Date.parse(notice.until) > Date.now());
    var expiry = user && user.expire_at;
    var values = {
      name: user ? user.username : "person", expires: expiry ? expiry.slice(0, 10) + " " + expiry.slice(11, 16) + " UTC" : "No expiry",
      days_left: expiry ? String(Math.max(0, Math.ceil((Date.parse(expiry) - Date.now()) / 86400000))) : "—",
      used: byteText(user ? user.used_traffic_bytes : 0), quota: user && user.traffic_limit_bytes ? byteText(user.traffic_limit_bytes) : "Unlimited",
    };
    return { notice: notice, active: active, for_user: name || null, preview: active ? notice.text.replace(/\{(\w+)\}/g, function (whole, field) { return values[field] || whole; }) : "", changed: false,
      allowed_substitutions: clone(responses["GET api/notice"].allowed_substitutions) };
  }
  function expiryPreview(user, months) {
    var start = new Date(Math.max(Date.now(), Date.parse(user.expire_at) || 0));
    var base = iso(start.getTime());
    var day = start.getUTCDate();
    start.setUTCDate(1);
    start.setUTCMonth(start.getUTCMonth() + months);
    var end = new Date(start.getTime());
    end.setUTCMonth(end.getUTCMonth() + 1, 0);
    start.setUTCDate(Math.min(day, end.getUTCDate()));

    var view = clone(responses["GET ui/api/people/" + user.username + "/expiry?months=" + months] || responses["GET ui/api/people/" + source.page_person + "/expiry?months=" + months]);
    return Object.assign(view, { base_at: base, expire_at: iso(start.getTime()), months: months });
  }

  function userRoute(method, name, suffix, params, body) {
    var user = person(name);
    if (!user) return error("not_found", "missing", 404);
    if (method === "GET") {
      if (!suffix) return reply(user);
      if (suffix === "share") return reply(share(user));
      if (suffix === "expiry") return reply(expiryPreview(user, Number(params.get("months"))));
      if (suffix === "devices") return reply(devices(user));
      if (suffix === "connections") {
        var view = clone(responses["GET api/users/" + name + "/connections"] || responses["GET api/users/" + source.page_person + "/connections"]);
        var links = share(user);
        // The connections read carries no QR images; the share view does.
        links.connections.forEach(function (entry) { entry.qr_svg = ""; });
        return reply(Object.assign(view, { username: name, status: user.status, connections: links.connections, formats: links.formats, subscription_url: links.subscription_url }));
      }
      if (suffix === "usage") {
        if (responses["GET api/privacy"].settings.usage_history === "off") return error("history_off", "unavailable", 409);
        var usage = clone(responses["GET api/users/" + name + "/usage?days=30"] || responses["GET api/users/DemoTemplate/usage?days=30"]);
        usage.username = name;
        return reply(fit(usage, params));
      }
      if (suffix === "cut-off") return reply(responses["GET api/users/" + name + "/cut-off"] || responses["GET api/users/DemoTemplate/cut-off"]);
    }
    if (method === "PATCH" && !suffix) {
      var fields = Object.keys(body).filter(function (field) { return field !== "username"; });
      fields.forEach(function (field) { if (Object.hasOwn(user, field)) user[field] = body[field]; });
      enforce(user);
      // As the service: status changes are their own events; notes and tags emit none.
      if (fields.indexOf("status") >= 0) emit(body.status === "disabled" ? "user.disabled" : "user.enabled", name);
      var operational = fields.filter(function (field) { return ["note", "tag", "status"].indexOf(field) < 0; });
      if (operational.length) emit("user.updated", name, { fields: operational });
      return reply(user);
    }
    if (method === "POST" && suffix === "reset-traffic") {
      user.used_traffic_bytes = 0;
      user.traffic_reset_at = now();
      enforce(user);
      emit("user.traffic_reset", name);
      return reply(user);
    }
    if (method === "POST" && suffix === "reissue" || method === "DELETE" && !suffix) {
      if (responses["GET ui/api/fleet"].declared_users.indexOf(name) >= 0) return error("declared_user", "declared", 409);
      if (method === "DELETE") {
        users.splice(users.indexOf(user), 1);
        emit("user.removed", name);
        return reply(body.cut_off ? { user: null, commands: [] } : { deleted: name });
      }
      rotating[name] = !rotating[name];
      // Alternate between two real credential sets; people added here borrow the template's.
      var alternate = rotating[name] ? rotations[name] || rotations.DemoTemplate
        : { user: initial["GET api/users/" + name], share: initial["GET ui/api/people/" + name + "/share"] };
      if (alternate && alternate.user && alternate.share) {
        user.uuid = alternate.user.uuid;
        user.sub_token = alternate.user.sub_token;
        responses["GET ui/api/people/" + name + "/share"] = Object.assign(clone(alternate.share), { username: name });
      }
      responses["GET api/users/" + name + "/devices"] = Object.assign(devices(user), { devices: [], count: 0 });
      emit("user.reissued", name);
      return reply(body.cut_off ? { user: user, commands: [] } : user);
    }
    if (method === "DELETE" && suffix.indexOf("devices") === 0) {
      var list = devices(user);
      var before = list.devices.length;
      list.devices = suffix === "devices" ? [] : list.devices.filter(function (device) { return device.device_key !== suffix.slice(8); });
      list.count = list.devices.length;
      responses["GET api/users/" + name + "/devices"] = list;
      return reply({ deleted: before - list.count });
    }
    return error("not_found", "unavailable", 404);
  }

  function privacy(body) {
    var state = responses["GET api/privacy"], before = clone(state.settings);
    var changed = JSON.stringify(body) !== JSON.stringify(before);
    var deleted = { devices: 0, traffic_daily: 0, node_traffic_daily: 0 }, rekeyed = 0;
    if (changed) {
      users.forEach(function (user) {
        var list = devices(user);
        if (body.device_limits === "off") { deleted.devices += list.count; list.devices = []; list.count = 0; }
        else if (before.device_limits === "full" && body.device_limits === "private") {
          rekeyed += list.count;
          list.devices = list.devices.map(function (device) { return { device_key: device.device_key }; });
        } else if (before.device_limits === "private" && body.device_limits === "full") {
          list.devices = list.devices.map(function (device) { return Object.assign(device, { platform: null, os_version: null, model: null, user_agent: null, first_seen_at: null, last_seen_at: null }); });
        }
        responses["GET api/users/" + user.username + "/devices"] = list;
      });
      if (body.usage_history === "off" && before.usage_history !== "off") {
        // Turning history off deletes it; keep the shapes so turning it back on starts empty.
        Object.keys(responses).filter(function (key) { return key.indexOf("/usage?") >= 0 || key.indexOf("api/traffic/daily?") >= 0; }).forEach(function (key) {
          var view = responses[key];
          [view].concat(view.nodes || []).forEach(function (series) {
            series.daily.forEach(function (row) {
              Object.keys(row).forEach(function (field) { if (typeof row[field] === "number" && row[field]) { row[field] = 0; } });
            });
          });
        });
        deleted.traffic_daily = users.length;
      }
      var id = state.changes.reduce(function (max, item) { return Math.max(max, item.id); }, 0) + 1;
      state.changes.unshift({ id: id, changed_at: now(), author: "owner", before: before, after: clone(body) });
      state.settings = clone(body);
      emit("privacy.changed", "privacy", { audit_id: id });
    }
    return reply(Object.assign(clone(state), { changed: changed, deleted: deleted, rekeyed_devices: rekeyed }));
  }

  function handle(method, path, params, body) {
    if (path === "auth/state") return reply(Object.assign(clone(responses["GET auth/state"]), { signed_in: signedIn }));
    if (path === "auth/logout") { signedIn = false; return reply({ signed_in: false }); }
    if (path === "auth/login" || path === "auth/setup") { signedIn = true; return reply({ signed_in: true }); }
    if (path === "auth/confirm") return reply({ confirmed: true });
    if (path === "auth/password") return reply({ changed: true });
    if (path === "auth/sessions/revoke-others") return reply({ revoked: 0 });
    if (path === "auth/setup/check") return reply({ purpose: "setup" });
    if (path === "api/users" && method === "GET") return reply({ users: users });
    if (path === "api/users" && method === "POST") {
      if (person(body.username)) return error("user_exists", "A person with this name already exists.", 409);
      var user = clone(responses["POST api/users"]);
      Object.assign(user, body, { created_at: now(), username: body.username });
      user.user_id = "demo-person-" + (++newId);
      user.used_traffic_bytes = user.lifetime_traffic_bytes = 0;
      users.push(user);
      var view = clone(responses["GET ui/api/people/DemoTemplate/share"]);
      view.username = user.username;
      responses["GET ui/api/people/" + user.username + "/share"] = view;
      initial["GET api/users/" + user.username] = clone(user);
      initial["GET ui/api/people/" + user.username + "/share"] = clone(view);
      emit("user.created", user.username);
      return reply(user);
    }
    if (path === "api/users" && method === "PATCH") return userRoute(method, body.username, "", params, body);
    var match = path.match(/^(?:api\/users|ui\/api\/people)\/([^/]+)(?:\/(.*))?$/);
    if (match) return userRoute(method, decodeURIComponent(match[1]), decodeURIComponent(match[2] || ""), params, body);
    if (path === "api/notice") {
      var notice = method === "PUT" ? body.notice : responses["GET api/notice"].notice;
      var state = noticeState(notice, method === "PUT" ? body.for_user : params.get("for"));
      if (!state) return error("not_found", "missing", 404);
      if (method === "PUT" && body.confirm) {
        state.changed = JSON.stringify(notice) !== JSON.stringify(responses["GET api/notice"].notice);
        responses["GET api/notice"] = clone(state);
        if (state.changed) emit("notice.changed", "notice");
      }
      return reply(state);
    }
    if (path === "api/language" && method === "PUT") {
      responses["GET api/language"].language = body.language;
      emit("language.changed", "language");
      return reply(responses["GET api/language"]);
    }
    if (path === "api/privacy" && method === "PUT") return privacy(body);
    if (path === "api/events") {
      var after = Number(params.get("after")) || 0, limit = Math.max(1, Math.min(2000, Number(params.get("limit")) || 100));
      var pending = events.filter(function (event) { return event.id > after; });
      var page = pending.slice(0, limit);
      return reply({ events: page, next_cursor: page.length ? page[page.length - 1].id : after, has_more: pending.length > limit });
    }
    if (path === "api/tokens" && method === "POST") {
      var token = clone(responses["POST api/tokens"]);
      Object.assign(token, { id: (++newId).toString(16).padStart(12, "0"), name: body.name, scope: body.scope, created_at: now(), last_used_at: null, revoked_at: null,
        expires_at: body.expires_in_days ? iso(Date.now() + body.expires_in_days * 86400000) : null });
      token.token = "demo-only-token-" + token.id + "-never-authorizes-anything";
      var metadata = clone(token); delete metadata.token;
      responses["GET api/tokens"].tokens.push(metadata);
      emit("token.created", token.name, { name: token.name, scope: token.scope });
      return reply(token);
    }
    if (path.indexOf("api/tokens/") === 0 && method === "DELETE") {
      var token = responses["GET api/tokens"].tokens.find(function (item) { return item.id === path.slice(11); });
      if (!token) return error("not_found", "unavailable", 404);
      if (!token.revoked_at) { token.revoked_at = now(); emit("token.revoked", token.name, { name: token.name, scope: token.scope }); }
      return reply(token);
    }
    if (path === "api/traffic/daily") {
      if (responses["GET api/privacy"].settings.usage_history === "off") return error("history_off", "unavailable", 409);
      return reply(fit(clone(responses["GET api/traffic/daily?days=30"]), params));
    }
    if (method === "GET" && responses[method + " " + path]) return reply(responses[method + " " + path]);
    return error("not_found", "unavailable", 404);
  }

  window.fetch = function (input, options) {
    var url = new URL(typeof input === "string" || input instanceof URL ? input : input.url, location.href);
    var method = (options && options.method || input.method || "GET").toUpperCase();
    // Even an unknown API request is answered locally; it can never reach a service or another origin.
    if (url.origin !== location.origin) return Promise.resolve(error("not_found", "unavailable", 404));
    var path = url.pathname.indexOf(consoleRoot) === 0 ? url.pathname.slice(consoleRoot.length) : "";
    if (/^(?:api|auth|ui\/api)\//.test(path)) {
      var body = options && options.body ? JSON.parse(options.body) : {};
      return ready.then(function () { return handle(method, path, url.searchParams, body); }, function () { return error("demo_unavailable", "load", 503); });
    }
    var sub = url.pathname.match(/\/sub\/([^/]+)\/(info|config\.json)$/);
    if (sub) return ready.then(function () {
      var user = users.find(function (item) { return item.sub_token === sub[1]; });
      if (!user) return reply({ isFound: false, statusCode: 404, message: "Resource not found" }, 404);
      if (sub[2] === "info") {
        var info = clone(responses["GET sub/" + sub[1] + "/info"]);
        // Mirror the service's /info shape: byte counts are decimal strings and statuses upper case.
        var expires = user.expire_at || "2099-12-31T23:59:59.000Z";
        Object.assign(info.user, {
          userStatus: user.status.toUpperCase(), isActive: user.status === "active",
          trafficUsedBytes: String(user.used_traffic_bytes), trafficLimitBytes: String(user.traffic_limit_bytes || 0),
          trafficUsed: readableBytes(user.used_traffic_bytes), trafficLimit: readableBytes(user.traffic_limit_bytes || 0),
          expiresAt: expires, daysLeft: Math.max(0, Math.ceil((Date.parse(expires) - Date.now()) / 86400000)),
        });
        var notice = noticeState(responses["GET api/notice"].notice, user.username);
        info.notice = notice.active ? { text: notice.preview, until: notice.notice.until } : null;
        return reply(info);
      }
      return nativeFetch(input, options).then(function (response) { return response.json(); }).then(function (config) {
        config.subscription_url = localURL(share(user).subscription_url);
        config.owner_contact = clone(responses["GET api/settings"].owner_contact);
        var notice = noticeState(responses["GET api/notice"].notice, user.username);
        config.notice = notice.active ? { text: notice.preview, until: notice.notice.until } : null;
        return reply(config);
      });
    });
    return nativeFetch(input, options);
  };

  function localURL(url) {
    if (!source || url.indexOf(source.public_url) !== 0) return url;
    var local = new URL(location.origin + demoRoot.replace(/\/$/, "") + url.slice(source.public_url.length));
    local.searchParams.set("lang", document.documentElement.lang.split("-")[0]);
    return local.href;
  }
  function exported(url) {
    var match = new URL(url).pathname.match(/\/sub\/([^/]+)\/?$/);
    return !!match && pages.indexOf(match[1]) >= 0;
  }
  function say(text) {
    if (window.M && window.M.toast) return window.M.toast(text);
    var line = document.querySelector("#demo-banner p");
    if (!line) return;
    line.textContent = text;
    clearTimeout(say.timer);
    say.timer = setTimeout(function () { line.textContent = copy().banner; }, 5000);
  }
  // Displayed credentials and QR codes stay the generated RFC 5737 examples, but every link
  // points inside this demo, so a new tab or a middle click never leaves it either.
  new MutationObserver(function () {
    if (!source) return;
    document.querySelectorAll('a[href^="' + source.public_url + '"]').forEach(function (anchor) { anchor.href = localURL(anchor.href); });
  }).observe(document.documentElement, { childList: true, subtree: true });
  document.addEventListener("click", function (event) {
    var anchor = event.target.closest && event.target.closest("a[href]");
    if (!anchor) return;
    var url = new URL(anchor.href, location.href);
    // An app import would hand a real VPN app this demo's address; say so instead.
    if (anchor.hasAttribute("data-import") || !/^(https?|mailto|tg):$/.test(url.protocol)) {
      event.preventDefault();
      say(copy().imports);
      return;
    }
    if (!source) return;
    var href = localURL(url.href);
    var inside = href.indexOf(location.origin + demoRoot + "sub/") === 0;
    if (!inside) return;
    event.preventDefault();
    if (!exported(href)) { say(copy().unavailable); return; }
    if (anchor.target === "_blank") window.open(href, "_blank", "noopener,noreferrer");
    else location.assign(href);
  }, true);

  function banner() {
    var aside = document.createElement("aside");
    aside.className = "demo-banner";
    aside.id = "demo-banner";
    aside.setAttribute("role", "note");
    var inner = document.createElement("div"), text = document.createElement("p"), actions = document.createElement("div");
    inner.className = "demo-banner__inner";
    actions.className = "demo-banner__actions";
    var link = document.createElement("a");
    link.href = inConsole ? demoRoot : consoleRoot;
    actions.appendChild(link);
    inner.append(text, actions); aside.appendChild(inner);
    document.body.insertBefore(aside, document.body.firstChild);
    function translate() {
      text.textContent = copy().banner;
      link.textContent = inConsole ? copy().page : copy().console;
      link.href = (inConsole ? demoRoot : consoleRoot) + "?lang=" + document.documentElement.lang;
    }
    translate();
    new MutationObserver(translate).observe(document.documentElement, { attributes: true, attributeFilter: ["lang"] });
  }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", banner);
  else banner();
})();
