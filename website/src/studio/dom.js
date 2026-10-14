// @ts-check
// Tiny DOM helpers. Text only ever goes through textContent: Studio renders
// pasted output and server-provided strings, so no HTML sinks anywhere.

/**
 * @template {keyof HTMLElementTagNameMap} K
 * @param {K} tag
 * @param {{ className?: string, text?: string, dataset?: Record<string, string>, attrs?: Record<string, string> }} [options]
 * @param {Array<Node | string>} [children]
 * @returns {HTMLElementTagNameMap[K]}
 */
export function el(tag, options = {}, children = []) {
  const node = document.createElement(tag);
  if (options.className) node.className = options.className;
  if (options.text !== undefined) node.textContent = options.text;
  for (const [key, value] of Object.entries(options.dataset ?? {})) node.dataset[key] = value;
  for (const [key, value] of Object.entries(options.attrs ?? {})) node.setAttribute(key, value);
  if (children.length > 0) node.append(...children);
  return node;
}

/**
 * Look up a required element by id; a missing one is a page bug worth failing loudly on.
 * @template {HTMLElement} [T=HTMLElement]
 * @param {Document | ParentNode} root
 * @param {string} id
 * @returns {T}
 */
export function byId(root, id) {
  const node = "getElementById" in root ? root.getElementById(id) : root.querySelector(`#${id}`);
  if (!node) throw new Error(`Studio page is missing #${id}`);
  return /** @type {T} */ (node);
}

/**
 * @param {HTMLElement} container
 * @param {{ title: string, detail?: string, tone?: string }} status
 */
export function showStatus(container, { title, detail = "", tone = "ok" }) {
  container.hidden = false;
  container.dataset.tone = tone;
  container.replaceChildren(el("strong", { text: title }), el("span", { text: detail }));
}

/** @param {HTMLElement} container */
export function hideStatus(container) {
  container.hidden = true;
  container.replaceChildren();
}

/**
 * @param {HTMLElement} container
 * @param {Array<{ message: string }>} errors
 */
export function renderErrors(container, errors) {
  container.hidden = errors.length === 0;
  container.replaceChildren(...errors.map((error) => el("p", { text: error.message })));
}

/**
 * Three-line result block (status word, headline, detail).
 * @param {HTMLElement} container
 * @param {{ status: string, title: string, detail: string, tone?: string }} view
 */
export function renderResult(container, view) {
  container.dataset.status = view.tone === "error" ? "failed" : view.tone === "warning" ? "warning" : "ok";
  container.replaceChildren(
    el("strong", { text: view.status }),
    el("span", { text: view.title }),
    el("small", { text: view.detail }),
  );
}
