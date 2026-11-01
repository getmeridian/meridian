/* Meridian console: one person: share their link, QR, and page; status, expiry, limits,
 * traffic reset, a new link, and removal. */
(function () {
  "use strict";
  var M = (window.M = window.M || {});
  var h = M.h;
  var generation = 0;

  function path(username, suffix) { return "api/users/" + encodeURIComponent(username) + (suffix || ""); }

  M.renderPerson = function (view, username) {
    var screen = { view: view, username: username, generation: ++generation, stopped: false, version: 0 };
    view.meridianPersonGeneration = screen.generation;
    screen.liveStatus = h("div", { class: "live-status" });
    var loading = h("div", { class: "empty" }, h("span", { class: "spinner" }));
    M.mount(view, [back(), screen.liveStatus, loading]);
    var stop = M.liveView(view, {
      status: screen.liveStatus, interval: 30000,
      load: function (passive) {
        var version = screen.version;
        return Promise.all([M.api("GET", path(username), undefined, { passive: passive }),
          M.api("GET", "ui/api/people/" + encodeURIComponent(username) + "/share", undefined, { passive: passive })])
          .then(function (results) { return { version: version, results: results }; });
      },
      accept: function (data) { return current(screen) && data.version === screen.version; },
      draw: function (data) { draw(screen, data.results[0], data.results[1]); },
      empty: function () { loading.remove(); },
    });
    return function () { screen.stopped = true; stop(); M.discardDrafts(view); };
  };

  function back() {
    return h("a", { class: "back", href: M.peopleHash || "#/people" }, M.icon("back"), M.t("nav.people"));
  }

  function current(screen) {
    return !screen.stopped && screen.view.isConnected && screen.view.meridianPersonGeneration === screen.generation;
  }

  function failed(label, error) {
    if (error.code !== "cancelled") M.toast(M.t("person.action_error", { name: label, error: M.errorText(error) }), "error");
  }

  function completed(screen, user, toastKey, label, savedForm) {
    if (toastKey === "person.enabled_toast" && user.status === "expired") toastKey = "person.enabled_expired_toast";
    if (toastKey === "person.enabled_toast" && user.status === "limited") toastKey = "person.enabled_limited_toast";
    M.toast(M.t(toastKey, { name: label }));
    if (!current(screen)) return;
    screen.version++;
    draw(screen, user, screen.share, savedForm);
    if (["person.reissued_toast", "person.rotated_toast", "person.enabled_toast", "person.disabled_toast", "person.saved_toast", "person.reset_toast"].indexOf(toastKey) >= 0) {
      var version = screen.version;
      M.api("GET", "ui/api/people/" + encodeURIComponent(screen.username) + "/share").then(function (share) {
        if (current(screen) && screen.version === version) draw(screen, screen.user, share);
      }, function (e) { if (current(screen)) M.toast(M.errorText(e), "error"); });
    }
  }

  function act(screen, work, toastKey) {
    if (!current(screen)) return Promise.resolve();
    var label = M.displayName(screen.username);
    return work().then(function (result) {
      completed(screen, result.user || result, toastKey, label);
      if (current(screen) && result.commands) M.showCommandResults(result.commands);
    }, function (e) { failed(label, e); });
  }

  function heading(user) {
    var name = user.username;
    return [h("div", {}, h("h1", { text: M.displayName(name) }),
      h("p", { class: "muted" }, M.displayName(name) !== name ? h("span", { class: "mono", text: name + " · " }) : null,
        M.t("person.since", { when: M.date(user.created_at) }))), M.statusPill(user.status)];
  }

  function editable(form) { return !M.formDirty(form) && !form.meridianSaving; }

  function draw(screen, user, share, savedForm) {
    var previous = screen.user, oldShare = screen.share;
    screen.user = user;
    screen.share = share;
    var name = user.username;
    if (!previous) {
      screen.head = h("div", { class: "page-head" });
      screen.shareSlot = h("div"); screen.usageSlot = h("div", { class: "card-slot" });
      screen.accessSlot = h("div", { class: "card-slot" }); screen.metadataSlot = h("div"); screen.dangerSlot = h("div");
      screen.flowSlot = h("div");
      M.mount(screen.view, [back(), screen.head, screen.liveStatus, screen.shareSlot,
        h("div", { class: "cards" }, screen.usageSlot, screen.accessSlot), screen.flowSlot, screen.metadataSlot,
        M.personHistory(name), M.devicesCard(user, function (changed) {
          if (!current(screen)) return;
          screen.version++;
          draw(screen, changed, screen.share);
        }), screen.dangerSlot]);
    }
    M.mount(screen.head, heading(user));
    M.mount(screen.flowSlot, flowCard(share.flow_blocks));
    M.mount(screen.usageSlot, usageCard(user, screen));
    if (!oldShare || JSON.stringify(oldShare) !== JSON.stringify(share)) {
      var disclosure = screen.shareSlot.querySelector("details");
      var open = disclosure && disclosure.open;
      M.mount(screen.shareSlot, shareCard(share, name));
      if (open && screen.shareSlot.querySelector("details")) screen.shareSlot.querySelector("details").open = true;
    }
    var accessForm = screen.accessSlot.querySelector("form");
    var changedAccess = !previous || previous.expire_at !== user.expire_at || previous.traffic_limit_bytes !== user.traffic_limit_bytes
      || previous.traffic_limit_strategy !== user.traffic_limit_strategy || previous.language !== user.language
      || !M.sameLifecycle(previous, user) || JSON.stringify(previous.schedule) !== JSON.stringify(user.schedule);
    if (!accessForm || editable(accessForm) && (changedAccess || accessForm === savedForm)) {
      M.mount(screen.accessSlot, accessCard(user, screen));
    } else {
      screen.accessSlot.querySelector(".card-head").replaceWith(accessHead(user, screen));
      screen.accessSlot.querySelector(".access-status").textContent = M.t("person.status_" + user.status);
    }
    var metadataForm = screen.metadataSlot.querySelector("form");
    var changedMetadata = !previous || previous.note !== user.note || previous.tag !== user.tag;
    if (!metadataForm || editable(metadataForm) && (changedMetadata || metadataForm === savedForm)) {
      M.mount(screen.metadataSlot, metadataCard(user, screen));
    }
    M.mount(screen.dangerSlot, dangerCard(user, share.declared, screen));
  }

  function shareCard(share, username) {
    if (!share.subscription_url) {
      return h("section", { class: "card" }, h("h2", { text: M.t("person.share") }),
        h("p", { class: "muted", text: M.t("person.no_public_url") }), M.connectionDisclosure(share));
    }
    return h("section", { class: "card" },
      h("h2", { text: M.t("person.share") }),
      h("p", { class: "muted small", text: M.t("person.share_text") }),
      h("div", { class: "share" },
        h("div", { class: "stack-sm share-qr" },
          h("img", { class: "qr", src: share.qr_svg, alt: M.t("person.qr_alt"), width: "200", height: "200" }),
          M.qrExportActions(share.qr_svg, { name: M.displayName(username), username: username,
            title: share.profile_title || (M.state.fleet && M.state.fleet.profile_title) || "Meridian" })),
        h("div", { class: "stack-sm share-links" },
          h("p", { class: "label", text: M.t("person.subscription") }),
          h("code", { class: "link-text", text: share.subscription_url }),
          h("div", { class: "row-actions" },
            h("button", { class: "btn btn-soft btn-sm", type: "button",
              onclick: function () { M.copy(share.subscription_url); } }, M.icon("copy"), M.t("person.copy_link"))),
          share.page_url ? [
            h("p", { class: "label", text: M.t("person.page") }),
            h("div", { class: "row-actions" },
              h("a", { class: "btn btn-ghost btn-sm", href: share.page_url, target: "_blank", rel: "noopener noreferrer" },
                M.icon("link"), M.t("person.open_page")),
              h("button", { class: "btn btn-ghost btn-sm", type: "button",
                onclick: function () { M.copy(share.page_url); } }, M.icon("copy"), M.t("person.copy_page"))),
          ] : h("p", { class: "muted small", text: M.t("person.no_page") }))), M.connectionDisclosure(share));
  }

  function usageCard(user, screen) {
    var name = user.username;
    return h("section", { class: "card" },
      h("h2", { text: M.t("person.traffic") }),
      M.usage(user), M.scheduleFacts(user.schedule),
      h("dl", { class: "facts" },
        user.period_traffic ? [
          h("dt", { text: M.t("usage.up") }), h("dd", { text: M.bytes(user.period_traffic.upload_bytes) }),
          h("dt", { text: M.t("usage.down") }), h("dd", { text: M.bytes(user.period_traffic.download_bytes) })
        ] : null,
        h("dt", { text: M.t("people.reset") }), h("dd", { text: M.t("reset." + user.traffic_limit_strategy) }),
        h("dt", { text: M.t("person.period_began") }),
        h("dd", { text: user.traffic_reset_at ? M.date(user.traffic_reset_at, true) : M.t("person.since_created") }),
        h("dt", { text: M.t("person.lifetime") }), h("dd", { text: M.bytes(user.lifetime_traffic_bytes) }),
        h("dt", { text: M.t("person.last_online") }), h("dd", { text: M.ago(user.online_at) })),
      user.period_traffic && user.period_traffic.unattributed_bytes ?
        h("p", { class: "muted small", text: M.t("person.traffic_unattributed", {
          bytes: M.bytes(user.period_traffic.unattributed_bytes) }) }) : null,
      h("button", { class: "btn btn-ghost btn-sm", type: "button", onclick: function () {
        M.confirm(M.t("person.reset_title"), M.t("person.reset_text"), M.t("person.reset_traffic")).then(function (ok) {
          if (ok) act(screen, function () { return M.api("POST", path(name, "/reset-traffic"), {}); }, "person.reset_toast");
        });
      } }, M.t("person.reset_traffic")));
  }

  function flowCard(state) {
    if (!state || !state.nodes || !state.nodes.length) return null;
    var labels = { blocked: M.t("person.flow_blocked"), pending_node: M.t("person.flow_pending_node"),
      agent_too_old: M.t("person.flow_agent_too_old") };
    return h("section", { class: "card flow-block-status", "aria-live": "polite" },
      h("h2", { text: M.t("person.flow_title") }),
      h("p", { class: "muted small", text: M.t("person.flow_text") }),
      h("dl", { class: "facts" }, state.nodes.map(function (node) {
        return [h("dt", {}, h("bdi", { text: node.node_id })),
          h("dd", { "data-flow-state": node.status, text: labels[node.status] || labels.pending_node })];
      })));
  }

  function accessCard(user, screen) {
    var name = user.username;
    var expiry = M.expiryFields(user.expire_at, name);
    var limits = M.limitFields(user);
    var lifecycle = M.lifecycleFields(user, expiry, limits);
    var initialLanguage = user.language || "default";
    var language = h("select", { name: "language" },
      h("option", { value: "default", text: M.t("language.person_default") }),
      M.i18n.langs.map(function (code) {
        return h("option", { value: code, lang: code, text: M.i18n.names[code] });
      }));
    language.value = initialLanguage;
    var form = h("form", { class: "stack", novalidate: true },
      lifecycle.nodes,
      limits.nodes,
      M.field(M.t("language.person"), language, M.t("language.person_hint")),
      h("p", { class: "form-error", role: "alert" }),
      h("div", { class: "row-actions" },
        h("button", { class: "btn btn-primary btn-sm", type: "submit" }, M.t("action.save"))));
    M.trackForm(form);
    form.addEventListener("submit", function (e) {
      e.preventDefault();
      var change = limits.value();
      Object.assign(change, lifecycle.change());
      if (language.value !== initialLanguage) change.language = language.value === "default" ? null : language.value;
      if (!Object.keys(change).length) {
        M.resetDraft(form);
        M.toast(M.t("person.nothing_changed"));
        return;
      }
      var label = M.displayName(name);
      M.saveDraft(form, function () { return M.api("PATCH", path(name), change); }).then(function (changed) {
        completed(screen, changed, "person.saved_toast", label, form);
      }, function (e) { if (!current(screen)) failed(label, e); });
    });
    return h("section", { class: "card" },
      accessHead(user, screen),
      h("p", { class: "muted small access-status", text: M.t("person.status_" + user.status) }), form);
  }

  function accessHead(user, screen) {
    var disabled = user.status === "disabled", name = user.username;
    return h("div", { class: "card-head" },
        h("h2", { text: M.t("person.access") }),
        h("button", { class: "btn btn-sm " + (disabled ? "btn-primary" : "btn-ghost"), type: "button",
          onclick: function () {
            act(screen, function () {
              return M.api("PATCH", path(name), { status: disabled ? "active" : "disabled" });
            }, disabled ? "person.enabled_toast" : "person.disabled_toast");
          } }, M.t(disabled ? "person.enable" : "person.disable")));
  }

  function metadataCard(user, screen) {
    var initialNote = user.note || "", initialTag = user.tag || "";
    var note = h("textarea", { name: "note", rows: "4" }, initialNote);
    var tag = h("input", { name: "tag", type: "text", value: initialTag, dir: "ltr", autocomplete: "off" });
    var error = h("p", { class: "form-error", role: "alert" });
    var form = h("form", { class: "stack", novalidate: true },
      M.field(M.t("person.note"), note, M.t("person.note_hint")),
      M.field(M.t("person.tag"), tag, M.t("person.tag_hint")), error,
      h("button", { class: "btn btn-primary btn-sm", type: "submit" }, M.t("action.save")));
    M.trackForm(form);
    function validate() {
      var characters = Array.from(note.value);
      var invalidText = characters.some(function (character) {
        var code = character.codePointAt(0);
        return code !== 10 && (code < 32 || code >= 127 && code <= 159 || code >= 55296 && code <= 57343);
      });
      var noteError = characters.length > 500 || invalidText ? M.t("person.note_invalid") : "";
      var upper = tag.value.toUpperCase();
      if (upper !== tag.value) {
        // Tags are uppercase; typing `family` becomes FAMILY instead of an error.
        var caret = tag.selectionStart;
        tag.value = upper;
        if (caret !== null && upper.length === Array.from(upper).length) tag.setSelectionRange(caret, caret);
      }
      var tagError = /^[A-Z0-9_]{0,16}$/.test(tag.value) ? "" : M.t("person.tag_invalid");
      note.setCustomValidity(noteError);
      tag.setCustomValidity(tagError);
      note.setAttribute("aria-invalid", noteError ? "true" : "false");
      tag.setAttribute("aria-invalid", tagError ? "true" : "false");
      error.textContent = noteError || tagError;
      return !error.textContent;
    }
    note.addEventListener("input", validate);
    tag.addEventListener("input", validate);
    form.addEventListener("submit", function (e) {
      e.preventDefault();
      if (!validate()) { (note.validationMessage ? note : tag).focus(); return; }
      var change = {};
      if (note.value !== initialNote) change.note = note.value;
      if (tag.value !== initialTag) change.tag = tag.value;
      if (!Object.keys(change).length) { M.resetDraft(form); M.toast(M.t("person.nothing_changed")); return; }
      var label = M.displayName(user.username);
      M.saveDraft(form, function () { return M.api("PATCH", path(user.username), change); }).then(function (changed) {
        completed(screen, changed, "person.saved_toast", label, form);
      }, function (e) { if (!current(screen)) failed(label, e); });
    });
    return h("section", { class: "card" }, h("h2", { text: M.t("person.metadata") }),
      h("p", { class: "muted small", text: M.t("person.metadata_private") }), form);
  }

  /* `declared` comes with this person's share view, fresh from the service: the CLI moves a declared
   * person's connection page with their link, so a new link and removal are its commands. */
  function dangerCard(user, declared, screen) {
    var name = user.username;
    return h("section", { class: "card card-danger" },
      h("h2", { text: M.t("person.danger") }),
      rotationRow(user, declared, screen),
      h("div", { class: "danger-row" },
        h("div", {}, h("p", { class: "strong", text: M.t("person.reissue") }),
          h("p", { class: "muted small", text: M.t(declared ? "person.reissue_declared" : "person.reissue_text") }),
          declared ? M.command("meridian client reissue " + name) : null),
        declared ? null : h("button", { class: "btn btn-ghost btn-sm", type: "button", onclick: function () {
          if (screen.reissuing || !current(screen)) return;
          screen.reissuing = true;
          M.confirmCutOffReissue(name).then(function (choice) {
            if (!choice || !current(screen)) return;
            return act(screen, function () {
              return M.api("POST", path(name, "/reissue"), { cut_off: choice.cut_off });
            }, "person.reissued_toast");
          }, function (e) { failed(M.displayName(name), e); })
            .finally(function () { screen.reissuing = false; });
        } }, M.t("person.reissue"))),
      h("div", { class: "danger-row" },
        h("div", {}, h("p", { class: "strong", text: M.t("person.remove") }),
          h("p", { class: "muted small", text: M.t(declared ? "person.remove_declared" : "person.remove_text") }),
          declared ? M.command("meridian client remove " + name) : null),
        declared ? null : h("button", { class: "btn btn-danger btn-sm", type: "button", onclick: function () {
          M.confirmCutOffRemoval(name).then(function (choice) {
            if (!choice || !current(screen)) return;
            var label = M.displayName(name);
            M.api("DELETE", path(name), { cut_off: choice.cut_off }).then(function (changed) {
              M.toast(M.t("person.removed_toast", { name: label }));
              if (!current(screen)) return;
              M.discardDrafts(screen.view);
              M.go("people");
              if (changed.commands) M.showCommandResults(changed.commands);
            }, function (e) { failed(label, e); });
          }, function (e) { failed(M.displayName(name), e); });
        } }, M.t("person.remove"))));
  }

  function rotationRow(user, declared, screen) {
    var name = user.username;
    var button = declared ? null : h("button", { class: "btn btn-ghost btn-sm", type: "button", "data-credential-rotation": "1",
      disabled: !!screen.rotating, onclick: function () {
        if (screen.rotating || !current(screen)) return;
        if (!Number.isSafeInteger(user.credential_generation) || user.credential_generation < 1) {
          M.toast(M.t("error.credentials_changed"), "error"); return;
        }
        screen.rotating = true; button.disabled = true;
        var expected = user.credential_generation;
        M.confirmCredentialRotation(name).then(function (choice) {
          if (!choice || !current(screen)) return;
          return act(screen, function () {
            return M.api("POST", path(name, "/rotate-credentials"), { expected_generation: expected, cut_off: choice.cut_off }).then(function (result) {
              if (result.commands && result.commands.length) M.showCommandResults(result.commands);
              return result.user;
            });
          }, "person.rotated_toast");
        }).catch(function (error) { if (current(screen)) failed(M.displayName(name), error); })
          .finally(function () {
            screen.rotating = false;
            if (current(screen)) {
              var currentButton = screen.dangerSlot.querySelector("[data-credential-rotation]");
              if (currentButton) currentButton.disabled = false;
            }
          });
      } }, M.t("person.rotate"));
    return h("div", { class: "danger-row" },
      h("div", {}, h("p", { class: "strong", text: M.t("person.rotate") }),
        h("p", { class: "muted small", text: M.t(declared ? "person.rotate_declared" : "person.rotate_text") }),
        declared ? M.command("meridian client rotate-credentials " + name) : null), button);
  }
})();
