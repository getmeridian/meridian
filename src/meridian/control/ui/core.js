/* Meridian console: shared building blocks.
 *
 * Security model: every element is built with createElement and textContent (`M.h`);
 * nothing is ever parsed as HTML, so data from the service can never become markup. Images take only `data:image/svg+xml` URIs the service rendered. Requests
 * are same-origin, never cached, and every one carries the X-Meridian-Request header
 * the service requires of writes (a cross-site page cannot send it). */
(function () {
  "use strict";
  var M = (window.M = window.M || {});
  M.state = { auth: null, fleet: null };

  // -- elements -------------------------------------------------------------------------

  /* h("button", {class: "btn", onclick: fn, type: "button"}, "Text", child, [more]) */
  M.h = function (tag, attrs) {
    var svgTags = ["svg", "g", "path", "rect", "line", "text", "title", "desc", "circle", "polyline"];
    var el = svgTags.indexOf(tag) >= 0 ? document.createElementNS("http://www.w3.org/2000/svg", tag)
      : document.createElement(tag);
    if (attrs) {
      Object.keys(attrs).forEach(function (name) {
        var value = attrs[name];
        if (value === null || value === undefined || value === false) return;
        if (name === "class") el.setAttribute("class", value);
        else if (name === "text") el.textContent = value;
        else if (name.slice(0, 2) === "on" && typeof value === "function") el.addEventListener(name.slice(2), value);
        else if (name === "dataset") Object.keys(value).forEach(function (k) { el.dataset[k] = value[k]; });
        else if (name === "src" && tag === "img" && !/^data:image\/svg\+xml[;,]/.test(value) && !/^ui\//.test(value)) {
          return; // only the service's own SVG QR codes and shipped icons
        } else if (name === "href" && !/^(https:\/\/|#|ui\/)/.test(value)) {
          return; // links open only HTTPS pages or in-app routes
        } else if (value === true) el.setAttribute(name, "");
        else el.setAttribute(name, String(value));
      });
    }
    for (var i = 2; i < arguments.length; i++) append(el, arguments[i]);
    return el;
  };

  function append(el, child) {
    if (child === null || child === undefined || child === false) return;
    if (Array.isArray(child)) { child.forEach(function (c) { append(el, c); }); return; }
    el.appendChild(child instanceof Node ? child : document.createTextNode(String(child)));
  }

  M.clear = function (el) { while (el.firstChild) el.removeChild(el.firstChild); return el; };
  M.mount = function (el, child) { M.clear(el); append(el, child); return el; };

  /* A command the operator runs on their own computer, rendered as code with a copy button. */
  M.command = function (text) {
    return M.h("span", { class: "cmd" }, M.h("code", { text: text }),
      M.h("button", { class: "cmd-copy", type: "button", "aria-label": M.t("action.copy"),
        onclick: function () { M.copy(text); } }, M.icon("copy")));
  };

  var ICONS = {
    copy: "M9 9h10v10H9zM5 15V5h10",
    check: "M5 12l5 5L20 7",
    plus: "M12 5v14M5 12h14",
    out: "M15 4h4v16h-4M10 8l-4 4 4 4M6 12h10",
    back: "M15 6l-6 6 6 6",
    link: "M10 14a4 4 0 0 0 5.66 0l3-3a4 4 0 0 0-5.66-5.66l-1 1M14 10a4 4 0 0 0-5.66 0l-3 3a4 4 0 0 0 5.66 5.66l1-1",
    terminal: "M4 6l5 5-5 5M11 17h9",
    shield: "M12 3l7 3v6c0 4.5-3 7.5-7 9-4-1.5-7-4.5-7-9V6z",
    x: "M6 6l12 12M18 6L6 18",
  };
  M.icon = function (name) {
    return M.h("svg", { viewBox: "0 0 24 24", "aria-hidden": "true", class: "icon" },
      M.h("path", { d: ICONS[name] || "" }));
  };

  // -- requests ---------------------------------------------------------------------------

  function ApiError(status, code, message, retryAfter) {
    this.status = status;
    this.code = code;
    this.message = message;
    this.retryAfter = retryAfter;
  }
  ApiError.prototype = Object.create(Error.prototype);
  M.ApiError = ApiError;

  function request(method, path, body) {
    var options = {
      method: method,
      credentials: "same-origin",
      cache: "no-store",
      redirect: "error",
      headers: { "X-Meridian-Request": "1", Accept: "application/json" },
    };
    if (body !== undefined) {
      options.headers["Content-Type"] = "application/json";
      options.body = JSON.stringify(body);
    }
    return fetch(path, options).then(function (response) {
      return response.text().then(function (text) {
        var data = null;
        try { data = text ? JSON.parse(text) : null; } catch (e) { data = null; }
        if (response.ok) return data;
        var error = (data && data.error) || {};
        throw new ApiError(response.status, error.code || "http_" + response.status, error.message || "",
          Number(response.headers.get("Retry-After")) || 0);
      });
    }, function () {
      throw new ApiError(0, "network", "");
    });
  }

  /* Call the service; a 401 returns to sign-in, a 403 `reauth_required` asks for the
   * password (and code) once, then repeats the request. */
  M.api = function (method, path, body) {
    return request(method, path, body).catch(function (error) {
      if (error.status === 401 && M.onSignedOut) M.onSignedOut();
      if (error.code === "reauth_required" && M.confirmIdentity) {
        return M.confirmIdentity().then(function () { return request(method, path, body); });
      }
      throw error;
    });
  };
  M.rawApi = request;

  M.errorText = function (error) {
    if (!error || !error.code) return M.t("error.generic");
    if (error.code === "rate_limited") {
      return M.t("error.rate_limited", { wait: M.duration(error.retryAfter || 60) });
    }
    var key = "error." + error.code;
    var text = M.t(key);
    return text === key ? (error.message || M.t("error.generic")) : text;
  };

  // -- formatting --------------------------------------------------------------------------

  M.bytes = function (n) {
    n = Number(n) || 0;
    var units = ["B", "KB", "MB", "GB", "TB", "PB"];
    var i = 0;
    while (n >= 1000 && i < units.length - 1) { n /= 1000; i++; }
    var digits = n < 10 && i > 0 ? 1 : 0;
    // Isolated (FSI…PDI), so "64 GB" keeps its order inside Farsi sentences.
    return "\u2068" + new Intl.NumberFormat(M.i18n.locale(), { maximumFractionDigits: digits }).format(n) + " " +
      units[i] + "\u2069";
  };

  M.count = function (n) {
    return n === null || n === undefined ? "—" : new Intl.NumberFormat(M.i18n.locale()).format(n);
  };

  M.date = function (value, withTime) {
    if (!value) return "";
    var d = typeof value === "number" ? new Date(value * 1000) : new Date(value);
    var options = withTime ? { dateStyle: "medium", timeStyle: "short" } : { dateStyle: "medium" };
    return new Intl.DateTimeFormat(M.i18n.locale(), options).format(d);
  };

  M.ago = function (value) {
    if (!value) return M.t("time.never");
    var d = typeof value === "number" ? new Date(value * 1000) : new Date(value);
    var seconds = Math.round((d.getTime() - Date.now()) / 1000);
    var rtf = new Intl.RelativeTimeFormat(M.i18n.locale(), { numeric: "auto" });
    var steps = [[60, "second"], [3600, "minute"], [86400, "hour"], [2592000, "day"], [31536000, "month"]];
    var abs = Math.abs(seconds);
    if (abs < 45) return M.t("time.just_now");
    for (var i = 1; i < steps.length; i++) {
      if (abs < steps[i][0]) return rtf.format(Math.round(seconds / steps[i - 1][0]), steps[i][1]);
    }
    return rtf.format(Math.round(seconds / 31536000), "year");
  };

  M.duration = function (seconds) {
    var minutes = Math.max(1, Math.ceil(seconds / 60));
    return new Intl.NumberFormat(M.i18n.locale(), { style: "unit", unit: "minute", unitDisplay: "long" })
      .format(minutes);
  };

  // -- feedback ----------------------------------------------------------------------------

  var toastTimer = null;
  M.toast = function (text, kind) {
    var host = document.getElementById("toast") || document.body.appendChild(M.h("div", { id: "toast", role: "status" }));
    M.mount(host, M.h("div", { class: "toast " + (kind === "error" ? "toast-error" : "toast-ok") }, text));
    host.classList.add("show");
    clearTimeout(toastTimer);
    toastTimer = setTimeout(function () { host.classList.remove("show"); }, kind === "error" ? 6000 : 2600);
  };

  M.copy = function (text) {
    var done = function () { M.toast(M.t("toast.copied")); };
    if (navigator.clipboard && window.isSecureContext) {
      navigator.clipboard.writeText(text).then(done, function () { M.toast(M.t("error.copy"), "error"); });
    } else {
      M.toast(M.t("error.copy"), "error");
    }
  };

  /* A modal <dialog>; `build(close)` returns its body. Resolves with what close() was given. */
  M.dialog = function (title, build, options) {
    return new Promise(function (resolve) {
      var dialog = M.h("dialog", { class: "dialog" + (options && options.wide ? " dialog-wide" : ""),
        "aria-labelledby": "dialog-title" });
      var settled = false;
      function close(value) {
        if (settled) return;
        settled = true;
        dialog.close();
        dialog.remove();
        resolve(value);
      }
      dialog.addEventListener("cancel", function (e) { e.preventDefault(); close(undefined); });
      dialog.appendChild(M.h("div", { class: "dialog-head" },
        M.h("h2", { id: "dialog-title", text: title }),
        M.h("button", { class: "btn-icon", type: "button", "aria-label": M.t("action.close"),
          onclick: function () { close(undefined); } }, M.icon("x"))));
      dialog.appendChild(M.h("div", { class: "dialog-body" }, build(close)));
      document.body.appendChild(dialog);
      dialog.showModal();
      var first = dialog.querySelector("input, select, textarea");
      if (first) first.focus();
    });
  };

  /* Ask before a change that cannot be undone. */
  M.confirm = function (title, text, actionLabel, danger) {
    return M.dialog(title, function (close) {
      return [
        M.h("p", { class: "muted", text: text }),
        M.h("div", { class: "actions" },
          M.h("button", { class: "btn btn-ghost", type: "button", onclick: function () { close(false); } },
            M.t("action.cancel")),
          M.h("button", { class: "btn " + (danger ? "btn-danger" : "btn-primary"), type: "button",
            onclick: function () { close(true); } }, actionLabel)),
      ];
    }).then(Boolean);
  };

  /* A labelled form field. */
  M.field = function (label, input, hint) {
    var id = "f" + Math.random().toString(36).slice(2, 9);
    input.id = id;
    return M.h("div", { class: "field" },
      M.h("label", { for: id, text: label }), input, hint ? M.h("p", { class: "hint", text: hint }) : null);
  };

  /* Disable a form's buttons while `work` runs; show its error under the form. */
  M.busy = function (form, work) {
    var buttons = form.querySelectorAll("button");
    var error = form.querySelector(".form-error");
    buttons.forEach(function (b) { b.disabled = true; });
    if (error) error.textContent = "";
    return Promise.resolve().then(work).catch(function (e) {
      if (e && e.code === "cancelled") throw e; // the owner closed the password check: nothing failed
      if (error) error.textContent = M.errorText(e);
      else M.toast(M.errorText(e), "error");
      throw e;
    }).finally(function () {
      buttons.forEach(function (b) { b.disabled = false; });
    });
  };

  M.pill = function (kind, text) { return M.h("span", { class: "pill pill-" + kind, text: text }); };

  // -- routing -------------------------------------------------------------------------------

  function decodePart(part) {
    try { return decodeURIComponent(part); } catch (e) { return ""; } // "%" alone: no such route, not a crash
  }

  /* Own properties only: a person named "toString" must not read Object.prototype. */
  M.has = function (object, key) { return Object.prototype.hasOwnProperty.call(object, key); };

  M.route = function () {
    var parts = location.hash.replace(/^#\/?/, "").split("/").map(decodePart);
    return { name: parts[0] || "people", arg: parts[1] || "", extra: parts[2] || "" };
  };
  M.go = function (name, arg) {
    var hash = "#/" + name + (arg ? "/" + encodeURIComponent(arg) : "");
    if (location.hash === hash && M.render) M.render();
    else location.hash = hash;
  };
})();
