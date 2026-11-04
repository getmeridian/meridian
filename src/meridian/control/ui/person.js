/* Meridian console: one person: share their link, QR, and page; status, expiry, limits,
 * traffic reset, a new link, and removal. */
(function () {
  "use strict";
  var M = (window.M = window.M || {});
  var h = M.h;
  var generation = 0;

  function path(username, suffix) { return "api/users/" + encodeURIComponent(username) + (suffix || ""); }

  M.renderPerson = function (view, username, tab) {
    var screen = { view: view, username: username, tab: tab, generation: ++generation, stopped: false, version: 0, mounted: {} };
    view.meridianPersonGeneration = screen.generation;
    view.classList.add("person-view");
    screen.liveStatus = h("div", { class: "live-status" });
    var loading = h("div", { class: "empty" }, h("span", { class: "spinner" }));
    M.mount(view, [back(), screen.liveStatus, loading]);
    function mode() {
      if (screen.tabs) screen.tabs.mode();
      if (screen.access) screen.access.mode();
    }
    window.addEventListener("meridian:mode", mode);
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
    function cleanup() {
      screen.stopped = true; stop(); M.discardDrafts(view);
      window.removeEventListener("meridian:mode", mode); view.classList.remove("person-view");
    }
    cleanup.route = function (route) {
      screen.tab = route.extra;
      if (screen.tabs) screen.tabs.select(route.extra);
    };
    return cleanup;
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

  function completed(screen, user, toastKey, label, savedForm, action) {
    if (toastKey === "person.enabled_toast" && user.status === "expired") toastKey = "person.enabled_expired_toast";
    if (toastKey === "person.enabled_toast" && user.status === "limited") toastKey = "person.enabled_limited_toast";
    M.toast(M.t(toastKey, { name: label }), null, action);
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

  function act(screen, work, toastKey, action) {
    if (!current(screen)) return Promise.resolve();
    var label = M.displayName(screen.username);
    return work().then(function (result) {
      completed(screen, result.user || result, toastKey, label, undefined, action);
      if (current(screen) && result.commands) M.showCommandResults(result.commands);
    }, function (e) { failed(label, e); });
  }

  function draw(screen, user, share, savedForm) {
    var previous = screen.user;
    screen.user = user; screen.share = share;
    if (!previous) {
      screen.summary = summary(screen);
      screen.tabs = M.personTabs(user.username, screen.tab, function (next, old, panel, oldPanel) {
        if (oldPanel && M.hasUnsavedEdits(oldPanel)) {
          M.discardDrafts(oldPanel);
          if (old === "access") screen.access.reset();
          else {
            M.clear(oldPanel); screen.mounted[old] = false;
            if (old === "routing") { screen.profileCard = null; screen.deliveryCard = null; }
          }
        }
        mountPanel(screen, next, panel);
      });
      M.mount(screen.view, h("div", { class: "person-page" }, screen.summary, screen.liveStatus, screen.tabs.node));
      screen.tabs.select(screen.tab);
    }
    M.rememberName(user);
    drawSummary(screen, user, share);
    if (screen.access) screen.access.refresh(user, savedForm);
    if (previous && screen.profileCard) screen.profileCard.refresh(user);
    if (previous && screen.deliveryCard) screen.deliveryCard.refresh(user);
    if (screen.mounted.usage) M.mount(screen.usageSlot, usageCard(user, screen));
    if (screen.mounted.keys) {
      M.mount(screen.flowSlot, flowCard(share.flow_blocks));
      M.mount(screen.dangerSlot, dangerCard(user, screen));
    }
    screen.tabs.mode();
  }

  function mountPanel(screen, tab, panel) {
    if (screen.mounted[tab]) return;
    screen.mounted[tab] = true;
    var user = screen.user, name = user.username;
    if (tab === "access") {
      screen.access = M.personAccess(user, function (changed, form) {
        M.rememberName(changed);
        completed(screen, changed, "person.saved_toast", M.displayName(name), form);
      }, function () { return current(screen); });
      M.mount(panel, screen.access.node);
    } else if (tab === "usage") {
      screen.usageSlot = h("div");
      M.mount(screen.usageSlot, usageCard(user, screen));
      M.mount(panel, [screen.usageSlot, M.personHistory(name)]);
    } else if (tab === "devices") {
      M.mount(panel, M.devicesCard(user, function (changed) {
        if (!current(screen)) return;
        screen.version++; draw(screen, changed, screen.share);
      }));
    } else if (tab === "routing") {
      if (M.personProfiles) {
        screen.profileCard = M.personProfiles(user, function (changed) {
          completed(screen, changed, "person.saved_toast", M.displayName(name));
        });
        panel.appendChild(screen.profileCard.node);
      }
      if (M.personDeliveryPolicy) {
        screen.deliveryCard = M.personDeliveryPolicy(user, function (changed) {
          completed(screen, changed, "person.saved_toast", M.displayName(name));
        });
        panel.appendChild(screen.deliveryCard.node);
      }
    } else {
      screen.flowSlot = h("div"); screen.dangerSlot = h("div");
      M.mount(screen.flowSlot, flowCard(screen.share.flow_blocks));
      M.mount(screen.dangerSlot, dangerCard(user, screen));
      M.mount(panel, [screen.dangerSlot, screen.flowSlot]);
    }
  }

  function usage(user) {
    var used = user.used_traffic_bytes || 0, limit = user.traffic_limit_bytes || 0;
    var percent = limit ? used * 100 / limit : 0;
    var text = limit ? M.t("people.used_of", { used: M.bytes(used), limit: M.bytes(limit) }) : M.bytes(used);
    return h("div", { class: "usage" }, h("span", { class: "usage-text", text: text }),
      limit ? M.meter(percent, text, percent >= 100 ? "danger" : percent >= 90 ? "warn" : "success")
        : h("span", { class: "muted small", text: M.t("people.no_limit") }));
  }

  function summary(screen) {
    screen.titleText = h("bdi"); screen.title = h("h1", {}, screen.titleText); screen.badge = h("span"); screen.tag = h("bdi", { class: "tag", dir: "ltr", "data-person-tag": "" });
    screen.usernameLabel = h("bdi", { class: "muted small mono", dir: "ltr" });
    screen.summaryUsage = h("div", { class: "person-summary-usage" });
    screen.expiry = h("bdi", { class: "person-summary-expiry", tabindex: "0", "data-person-expiry": "" });
    screen.expiryTooltip = h("span", { class: "person-date-tooltip", role: "tooltip", id: "person-expiry-" + screen.generation });
    screen.expiry.setAttribute("aria-describedby", screen.expiryTooltip.id);
    screen.reset = h("span", { class: "muted small", "data-person-reset": "" });
    screen.receipt = h("a", { href: M.personHash(screen.username, "keys"), class: "muted small", hidden: true,
      "data-person-receipts": "", text: M.t("person.flow_title") });
    var shareButton = h("button", { class: "btn btn-primary", type: "button", "data-person-action": "share", onclick: function () {
      if (screen.sharing || !current(screen)) return;
      screen.sharing = true; shareButton.disabled = true;
      M.openShare(screen.username).finally(function () {
        screen.sharing = false; shareButton.disabled = false;
        // Native dialog restoration runs while this trigger is disabled.
        if (current(screen) && document.activeElement === document.body) shareButton.focus();
      });
    } }, M.icon("link"), M.t("person.share"));
    function editExpiry(months) {
      screen.extend.open = false;
      screen.tabs.request("access").then(function (accepted) {
        if (!accepted || !current(screen)) return;
        screen.access.open("expiry", months, !months).catch(function () { /* the preview shows its error */ });
      });
    }
    var extendToggle = h("summary", { class: "btn btn-ghost", "data-person-action": "extend" },
      M.t("person.extend"), h("span", { "aria-hidden": "true", text: "▾" }));
    var menuButtons = [1, 3].map(function (months) {
      return h("button", { type: "button", class: "btn btn-ghost", "data-months": months,
        onclick: function () { editExpiry(months); } }, M.t(months === 1 ? "person.plus_month" : "person.plus_three_months"));
    });
    menuButtons.push(h("button", { type: "button", class: "btn btn-ghost", "data-person-custom-expiry": "",
      onclick: function () { editExpiry(); } }, M.t("person.custom_date")));
    screen.extend = h("details", { class: "person-extend" }, extendToggle,
      h("div", { class: "person-extend-menu card" }, menuButtons));
    screen.extend.addEventListener("keydown", function (event) {
      if (event.key === "Escape") { event.preventDefault(); screen.extend.open = false; extendToggle.focus(); }
      else if (event.key === "ArrowDown" && event.target === extendToggle) {
        event.preventDefault(); screen.extend.open = true; menuButtons[0].focus();
      }
    });
    screen.toggle = h("button", { class: "btn btn-ghost", type: "button", "data-person-action": "toggle", onclick: function () {
      if (screen.toggling || !current(screen)) return;
      var disabled = screen.user.status === "disabled";
      screen.toggling = true; screen.toggle.disabled = true;
      var name = screen.username, label = M.displayName(name);
      // Undo sends the opposite request once; it still works after leaving this person's page.
      var undo = { label: M.t("action.undo"), run: function () {
        if (screen.toggling) return;
        screen.toggling = true; if (current(screen)) screen.toggle.disabled = true;
        M.api("PATCH", path(name), { status: disabled ? "disabled" : "active" }).then(function (changed) {
          completed(screen, changed.user || changed, disabled ? "person.disabled_toast" : "person.enabled_toast", label);
          // Each route mounts a new view; ask the one showing now to re-read so it shows the restored status.
          var view = !current(screen) && document.getElementById("view"), live = view && view.meridianLiveStop;
          if (live && live.refresh) live.refresh(true);
        }, function (e) { failed(label, e); }).finally(function () {
          screen.toggling = false; if (current(screen)) screen.toggle.disabled = false;
        });
      } };
      act(screen, function () { return M.api("PATCH", path(name), { status: disabled ? "active" : "disabled" }); },
        disabled ? "person.enabled_toast" : "person.disabled_toast", undo).finally(function () {
        screen.toggling = false; if (current(screen)) screen.toggle.disabled = false;
      });
    } });
    return h("header", { class: "person-summary", "data-person-summary": "" }, back(),
      h("div", { class: "page-head person-summary-head" },
        h("div", { class: "stack-sm" }, h("div", { class: "person-title-row" }, screen.title, screen.badge, screen.tag), screen.usernameLabel),
        h("div", { class: "row-actions person-primary-actions", role: "group", "aria-label": M.t("person.actions") },
          shareButton, screen.extend, screen.toggle)),
      h("div", { class: "person-summary-facts" }, screen.summaryUsage,
        h("p", { class: "muted small person-summary-meta", "data-person-meta": "" }, screen.reset, " · ",
          h("span", { class: "person-expiry-detail" }, screen.expiry, screen.expiryTooltip)), screen.receipt));
  }

  function drawSummary(screen, user, share) {
    screen.titleText.textContent = M.displayName(user.username);
    screen.usernameLabel.hidden = M.displayName(user.username) === user.username;
    screen.usernameLabel.textContent = user.username;
    if (screen.badge.dataset.status !== user.status) {
      M.mount(screen.badge, M.statusPill(user.status)); screen.badge.dataset.status = user.status;
    }
    screen.badge.title = M.t("person.status_" + user.status);
    screen.tag.hidden = !user.tag; screen.tag.textContent = user.tag || "";
    M.mount(screen.summaryUsage, usage(user));
    var schedule = user.schedule, expiry = schedule ? schedule.expire_at : user.expire_at;
    screen.expiry.textContent = M.expiryText(Object.assign({}, user, { expire_at: expiry }));
    var exact = expiry ? "\u2068" + M.date(expiry, true) + "\u2069 · \u2068" + expiry + " UTC\u2069" : screen.expiry.textContent;
    screen.expiry.title = exact; screen.expiryTooltip.textContent = exact;
    screen.reset.textContent = schedule && schedule.next_reset_at
      ? M.t("person.next_reset", { when: "\u2068" + M.date(schedule.next_reset_at) + "\u2069" })
      : M.t("people.reset") + " · " + M.t("reset." + user.traffic_limit_strategy);
    screen.reset.title = schedule && schedule.next_reset_at ? schedule.next_reset_at + " UTC" : "";
    screen.receipt.hidden = !(share.flow_blocks && share.flow_blocks.nodes && share.flow_blocks.nodes.length);
    screen.toggle.textContent = M.t(user.status === "disabled" ? "person.enable" : "person.disable");
  }

  function usageCard(user, screen) {
    var name = user.username;
    return h("section", { class: "card" },
      h("h2", { text: M.t("person.traffic") }),
      M.scheduleFacts(user.schedule, { compact: true, expiry: false }),
      h("p", { class: "muted small", text: M.t("person.since", { when: "\u2068" + M.date(user.created_at) + "\u2069" }) }),
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
        h("p", { class: "muted small", "data-person-carried": "", text: M.t("person.traffic_unattributed", {
          bytes: M.bytes(user.period_traffic.unattributed_bytes) }) }) : null,
      h("button", { class: "btn btn-ghost btn-sm", type: "button", onclick: function () {
        M.confirm(M.t("person.reset_title"), M.t("person.reset_text"), M.t("person.reset_traffic"), true).then(function (ok) {
          if (ok) act(screen, function () { return M.api("POST", path(name, "/reset-traffic"), {}); }, "person.reset_toast");
        });
      } }, M.t("person.reset_traffic")));
  }

  function flowCard(state) {
    if (!state || !state.nodes || !state.nodes.length) return null;
    var labels = { blocked: M.t("person.flow_blocked"), pending_node: M.t("person.flow_pending_node"),
      agent_too_old: M.t("person.flow_agent_too_old") };
    var reasons = {
      credentials: M.t("person.flow_reason_credentials"), disabled: M.t("person.flow_reason_disabled"),
      expired: M.t("person.flow_reason_expired"), limited: M.t("person.flow_reason_limited"),
      removed: M.t("person.flow_reason_removed"), access_changed: M.t("person.flow_reason_access_changed")
    };
    return h("section", { class: "card flow-block-status", "aria-live": "polite" },
      M.titled("h2", M.t("person.flow_title"), M.t("tip.flow_block")),
      M.learnMore(h("p", { text: M.t("person.flow_text") })),
      h("dl", { class: "facts" }, state.nodes.map(function (node) {
        var generation = Number.isSafeInteger(node.credential_generation) ?
          h("bdi", { class: "muted small", "data-flow-generation": node.credential_generation,
            text: " · " + M.t("person.flow_generation", {
              generation: new Intl.NumberFormat(M.i18n.locale()).format(node.credential_generation)
            }) }) : null;
        var reason = reasons[node.reason] ?
          h("span", { class: "muted small", "data-flow-reason": node.reason, text: " · " + reasons[node.reason] }) : null;
        return [h("dt", {}, h("bdi", { text: node.node_id }), generation, reason),
          h("dd", { "data-flow-state": node.status, text: labels[node.status] || labels.pending_node })];
      })));
  }

  function dangerCard(user, screen) {
    var name = user.username;
    return h("section", { class: "card card-danger" },
      h("h2", { text: M.t("person.danger") }),
      rotationRow(user, screen),
      h("div", { class: "danger-row" },
        h("div", {}, h("p", { class: "strong", text: M.t("person.reissue") }),
          h("p", { class: "muted small", text: M.t("person.reissue_text") })),
        h("button", { class: "btn btn-ghost btn-sm", type: "button", onclick: function () {
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
          h("p", { class: "muted small", text: M.t("person.remove_text") })),
        h("button", { class: "btn btn-danger btn-sm", type: "button", onclick: function () {
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

  function rotationRow(user, screen) {
    var name = user.username;
    var button = h("button", { class: "btn btn-ghost btn-sm", type: "button", "data-credential-rotation": "1",
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
        h("p", { class: "muted small", text: M.t("person.rotate_text") })), button);
  }
})();
