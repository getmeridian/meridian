/* Resolve width and pointer media queries for jsdom's computed-style checks. */
import fs from 'node:fs';
import path from 'node:path';

export function loadStyles(window, ui, names, width = 1040, coarse = width <= 640) {
  const css = names.map(name => fs.readFileSync(path.join(ui, name), 'utf8')).join('\n')
    .replace(/@media\s*\(pointer:\s*coarse\)(?:\s*,\s*\(max-width:\s*(\d+)px\))?/g,
      (_, limit) => coarse || Number(limit) >= width ? '@media all' : '@media not all')
    .replace(/@media\s*\(max-width:\s*(\d+)px\)/g,
      (_, limit) => Number(limit) >= width ? '@media all' : '@media not all');
  const style = window.M.h('style', { text: css });
  window.document.head.appendChild(style);
  function resolved(rules) {
    return [...rules].map(rule => rule.type === window.CSSRule.MEDIA_RULE
      ? rule.conditionText === 'all' ? resolved(rule.cssRules) : ''
      : rule.selectorText ? rule.selectorText.split(/,(?![^(]*\))/)
        .map(selector => selector + ' { ' + rule.style.cssText + ' }').join('\n') : rule.cssText).join('\n');
  }
  // jsdom misorders media declarations and gives grouped selectors one specificity.
  style.textContent = resolved(style.sheet.cssRules);
}
