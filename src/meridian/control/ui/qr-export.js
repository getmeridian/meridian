/* Export the service's local SVG QR with a caption. Generated URLs stay in this helper. */
(function () {
  "use strict";
  var M = (window.M = window.M || {});
  var h = M.h;
  var SIZE = 512;
  var SVG_NAMESPACE = "http://www.w3.org/2000/svg";

  function refused() { return new M.ApiError(0, "qr_export", ""); }

  function localSvg(uri) {
    var match = /^data:image\/svg\+xml(?:;charset=(?:utf-8|us-ascii))?(;base64)?,(.+)$/i.exec(uri || "");
    if (!match || uri.length > 300000) throw refused();
    var text;
    try { text = match[1] ? atob(match[2]) : decodeURIComponent(match[2]); } catch (e) { throw refused(); }
    if (/<!DOCTYPE|<!ENTITY/i.test(text)) throw refused();
    var source = new DOMParser().parseFromString(text, "image/svg+xml");
    var svg = source.documentElement;
    if (svg.localName !== "svg" || source.querySelector("parsererror")) throw refused();
    var tags = { svg: true, path: true, rect: true, g: true };
    [svg].concat(Array.from(svg.querySelectorAll("*"))).forEach(function (node) {
      if (node.namespaceURI !== SVG_NAMESPACE || !M.has(tags, node.localName)) throw refused();
      Array.from(node.attributes).forEach(function (attribute) {
        if (/^(?:on|href$|src$|style$)/i.test(attribute.localName) || /url\s*\(/i.test(attribute.value)) throw refused();
        if (/^(?:fill|stroke)$/i.test(attribute.localName)
          && !/^(?:#[a-f0-9]{3,8}|none|white|black|transparent)$/i.test(attribute.value)) throw refused();
      });
    });
    if (!svg.hasAttribute("viewBox")) {
      var width = Number(svg.getAttribute("width")), height = Number(svg.getAttribute("height"));
      if (!(width > 0 && width <= 4096 && height > 0 && height <= 4096)) throw refused();
      svg.setAttribute("viewBox", "0 0 " + width + " " + height);
    }
    return document.importNode(svg, true);
  }

  function captionLines(text, size) {
    var context;
    try { context = document.createElement("canvas").getContext("2d"); } catch (e) { context = null; }
    if (context) context.font = size + "px system-ui, sans-serif";
    var width = function (line) { return context ? context.measureText(line).width : Array.from(line).length * size * 0.65; };
    var lines = [], line = "";
    Array.from(String(text || "")).forEach(function (character) {
      if (line && width(line + character) > SIZE - 48) {
        var space = line.lastIndexOf(" ");
        lines.push(space > 0 ? line.slice(0, space) : line);
        line = (space > 0 ? line.slice(space + 1) : "") + character;
      } else line += character;
    });
    if (line) lines.push(line);
    return lines;
  }

  function captionedSvg(uri, caption) {
    var qr = localSvg(uri);
    qr.setAttribute("x", "16"); qr.setAttribute("y", "16");
    qr.setAttribute("width", String(SIZE - 32)); qr.setAttribute("height", String(SIZE - 32));
    var lines = [];
    var y = SIZE + 24;
    [[caption.name, 24, 30], [caption.title, 20, 26]].forEach(function (part) {
      captionLines(part[0], part[1]).forEach(function (line) {
        lines.push(h("text", { x: String(SIZE / 2), y: String(y), "text-anchor": "middle",
          "font-family": "system-ui, sans-serif", "font-size": String(part[1]), fill: "#1A1D2E",
          direction: M.i18n.lang === "fa" ? "rtl" : "ltr", "unicode-bidi": "plaintext", text: line }));
        y += part[2];
      });
      y += 6;
    });
    var height = y + 12;
    var svg = h("svg", { width: String(SIZE), height: String(height), viewBox: "0 0 " + SIZE + " " + height },
      h("rect", { width: String(SIZE), height: String(height), fill: "#FFFFFF" }), qr, lines);
    return { text: new XMLSerializer().serializeToString(svg), height: height };
  }

  function pngImage(svg) {
    var image = h("img", { src: "data:image/svg+xml," + encodeURIComponent(svg.text) });
    var ready = image.decode ? image.decode() : new Promise(function (resolve, reject) {
      image.addEventListener("load", resolve); image.addEventListener("error", reject);
    });
    return ready.then(function () {
      var canvas = document.createElement("canvas");
      canvas.width = SIZE; canvas.height = svg.height;
      var context = canvas.getContext("2d");
      if (!context || !canvas.toBlob) throw refused();
      context.fillStyle = "#FFFFFF";
      context.fillRect(0, 0, SIZE, svg.height);
      context.drawImage(image, 0, 0, SIZE, svg.height);
      return new Promise(function (resolve, reject) {
        canvas.toBlob(function (blob) { if (blob) resolve(blob); else reject(refused()); }, "image/png");
      });
    });
  }

  /* Only this helper assigns a generated download href. It accepts image blobs, never a URL. */
  function downloadImage(blob, username, format) {
    if (!(blob instanceof Blob) || ["image/png", "image/svg+xml"].indexOf(blob.type) < 0) throw refused();
    var url = URL.createObjectURL(blob);
    var link = h("a", { download: "meridian-" + String(username || "person").replace(/[^A-Za-z0-9_.-]/g, "_") + "-qr." + format });
    link.setAttribute("href", url);
    document.body.appendChild(link);
    try { link.click(); } finally {
      link.remove();
      setTimeout(function () { URL.revokeObjectURL(url); }, 1000);
    }
    return "downloaded";
  }

  function canCopy() {
    try {
      return window.isSecureContext && navigator.clipboard && navigator.clipboard.write && window.ClipboardItem
        && (!ClipboardItem.supports || ClipboardItem.supports("image/png"));
    } catch (e) { return false; }
  }

  function exportImage(uri, caption, format, copy) {
    var svg = captionedSvg(uri, caption);
    if (format === "svg") return Promise.resolve(downloadImage(new Blob([svg.text], { type: "image/svg+xml" }), caption.username, format));
    var png = pngImage(svg);
    function download() { return png.then(function (blob) { return downloadImage(blob, caption.username, "png"); }); }
    if (!copy || !canCopy()) return download();
    try {
      // Supplying a promise keeps clipboard.write inside the click's user activation (Safari included).
      return Promise.resolve(navigator.clipboard.write([new ClipboardItem({ "image/png": png })]))
        .then(function () { return "copied"; }, download);
    } catch (e) { return download(); }
  }

  M.qrExportActions = function (uri, caption) {
    var actions = h("div", { class: "row-actions qr-export-actions" });
    function button(format, copy, label) {
      return h("button", { class: "btn btn-ghost btn-sm", type: "button", dataset: { qrAction: copy ? "copy" : format },
        onclick: function () {
          M.busy(actions, function () {
            return Promise.resolve().then(function () { return exportImage(uri, caption, format, copy); })
              .catch(function () { throw refused(); });
          }).then(function (result) {
            M.toast(M.t(result === "copied" ? "person.qr_copied" : "person.qr_downloaded",
              { format: format.toUpperCase(), name: caption.name }));
          }, function () { /* M.busy shows the local export error. */ });
        } }, M.t(label));
    }
    M.mount(actions, [button("png", false, "person.download_png"), button("svg", false, "person.download_svg"),
      canCopy() ? button("png", true, "person.copy_qr_image") : null]);
    return actions;
  };
})();
