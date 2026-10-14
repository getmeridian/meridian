/**
 * The site's one copy-to-clipboard behavior for landing widgets: `.copy-btn` buttons
 * copy their `data-copy` text and show `[data-copied]`. Markdown code blocks (docs and
 * blog) get Expressive Code's own copy button.
 */

/** Clipboard API, else a hidden textarea and execCommand (older or insecure contexts). */
export async function copyText(text: string): Promise<boolean> {
  try {
    await navigator.clipboard.writeText(text);
    return true;
  } catch {
    const area = document.createElement('textarea');
    area.value = text;
    area.setAttribute('readonly', '');
    area.style.position = 'fixed';
    area.style.opacity = '0';
    document.body.append(area);
    area.select();
    const ok = document.execCommand('copy');
    area.remove();
    return ok;
  }
}

export function flashCopied(button: HTMLElement, label?: HTMLElement | null, copiedText?: string): void {
  const original = label?.textContent;
  button.setAttribute('data-copied', '');
  if (label && copiedText) label.textContent = copiedText;
  setTimeout(() => {
    button.removeAttribute('data-copied');
    if (label && original != null) label.textContent = original;
  }, 1500);
}

let wired = false;

export function wireCopyButtons(): void {
  if (wired) return;
  wired = true;
  document.addEventListener('click', async (event) => {
    const button = (event.target as Element | null)?.closest<HTMLElement>('.copy-btn');
    if (!button) return;
    const source = button.dataset.copy ?? '';
    if (source && (await copyText(source))) {
      flashCopied(button, button.querySelector<HTMLElement>('.copy-btn__label'), button.dataset.copiedLabel);
    }
  });
}
