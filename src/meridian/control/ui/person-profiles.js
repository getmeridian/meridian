/* Membership drafts stay in memory; the same service review drives single and bulk edits. */
(function () {
  "use strict";
  var M = window.M, h = M.h;

  M.profileChoices = function (initial) {
    var names = (initial || []).slice(), loaded = false;
    var list = h("fieldset", { class: "stack-sm choice-group" }, h("legend", { text: M.t("profiles.title") }));
    var error = h("p", { class: "form-error", role: "alert" });
    var nodes = h("div", { class: "stack-sm" }, list, error);
    var ready = Promise.resolve().then(function () {
      return M.api("GET", "api/access-profiles", undefined, { passive: true });
    }).then(function (catalog) {
      M.mount(list, [h("legend", { text: M.t("profiles.title") }), catalog.profiles.map(function (profile) {
        return h("label", { class: "check-row" },
          h("input", { type: "checkbox", name: "profile", value: profile.id, checked: names.indexOf(profile.id) >= 0 }),
          h("bdi", { text: profile.id }), profile.built_in ? h("span", { class: "muted small", text: M.t("profiles.all") }) : null);
      })]);
      loaded = true;
    }, function (failure) { error.textContent = M.errorText(failure); throw failure; });
    return { nodes: nodes, ready: ready,
      value: function () {
        if (!loaded) { error.textContent = M.t("profiles.loading"); return null; }
        var chosen = Array.from(list.querySelectorAll("input:checked")).map(function (input) { return input.value; }).sort();
        if (!chosen.length) { error.textContent = M.t("profiles.membership_invalid"); return null; }
        error.textContent = ""; return chosen;
      },
      reset: function (values) {
        names = values.slice();
        list.querySelectorAll("input").forEach(function (input) { input.checked = names.indexOf(input.value) >= 0; });
      }
    };
  };

  M.accessChangeDisclosure = function (person) {
    if (!person.access) return [];
    var content = [];
    [["gained", person.access.gained], ["lost", person.access.lost]].forEach(function (pair) {
      if (pair[1].length) content.push(h("p", { class: "small" }, h("b", { text: M.t("profiles." + pair[0]) + ": " }), h("bdi", { text: pair[1].join(", ") })));
    });
    if (person.access.lost.length) {
      content.push(h("p", { class: "hint", text: M.t("profiles.revocation") }));
      if (person.cut_off && person.cut_off.nodes.length) {
        content.push(h("p", { class: "hint", text: M.t("profiles.hard_cut_scope") }));
        person.cut_off.nodes.forEach(function (node) {
          content.push(h("p", { class: "hint", text: M.t(node.effect === "core_restart" ? "commands.core_scope" : "commands.inbound_scope", { node: node.node_id, inbounds: node.inbound_tags.join(", ") }) }));
        });
      }
    }
    return content;
  };

  M.personProfiles = function (initial, changed) {
    var user = initial, username = initial.username, busy = false, ready = false, version = 0;
    var choices = M.profileChoices(user.profiles || ["default"]);
    var effective = h("div", { class: "stack-sm", "data-effective-access": "" });
    var error = h("p", { class: "form-error", role: "alert" });
    var save = h("button", { type: "submit", class: "btn btn-primary", disabled: true, text: M.t("bulk.preview") });
    var form = h("form", { class: "stack", novalidate: true, onsubmit: function (event) {
      event.preventDefault();
      if (busy || !ready) return;
      var profiles = choices.value();
      if (!profiles) return;
      busy = true; save.disabled = true; error.textContent = "";
      M.reviewPeople({ usernames: [username], action: { action: "set-profiles", profiles: profiles } }, function () {
        M.api("GET", "api/users/" + encodeURIComponent(username)).then(function (updated) {
          if (!card.isConnected) return;
          user = updated; choices.reset(user.profiles); M.resetDraft(form); refresh(user);
          if (changed) changed(user);
        }, function (failure) { if (card.isConnected) error.textContent = M.errorText(failure); });
      }).finally(function () { busy = false; save.disabled = false; });
    } }, choices.nodes, error, h("div", { class: "actions" }, save));
    var card = h("section", { class: "card" }, M.titled("h2", M.t("profiles.title"), M.t("tip.profiles")),
      h("p", { class: "muted small", text: M.t("profiles.card_hint") }), form,
      h("h3", { text: M.t("profiles.effective") }), effective,
      h("p", { class: "hint", text: M.t("profiles.topology_hint", { command: "meridian profile add|set|remove|list" }) }));
    function refresh(current) {
      user = current;
      if (ready && !busy && !M.formDirty(form)) { choices.reset(user.profiles || ["default"]); M.resetDraft(form); }
      var mine = ++version;
      M.api("GET", "api/users/" + encodeURIComponent(username) + "/access", undefined, { passive: true }).then(function (access) {
        if (!card.isConnected || mine !== version) return;
        M.mount(effective, access.connections.length ? access.connections.map(function (connection) {
          return h("div", { class: "stack-sm" }, h("bdi", { text: connection.remark }),
            h("p", { class: "muted small", text: M.t("profiles.granted_by", { profiles: connection.granted_by.join(", ") }) }),
            connection.available ? null : h("p", { class: "hint", text: M.t("profiles.unavailable") }));
        }) : h("p", { class: "muted small", text: M.t("profiles.no_connections") }));
      }, function (failure) { if (card.isConnected && mine === version) error.textContent = M.errorText(failure); });
    }
    choices.ready.then(function () { ready = true; save.disabled = false; M.trackForm(form); refresh(user); }, function () {});
    return { node: card, refresh: refresh };
  };
})();
