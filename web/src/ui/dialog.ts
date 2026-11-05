import { button, element } from "../dom";

/** A keyboard-accessible modal whose lifetime belongs to the mounting screen. */
export function openDialog(parent: HTMLElement, title: string) {
  const previous = document.activeElement as HTMLElement | null;
  const shade = element("div", "bc-modal-shade");
  const box = element("section", "bc-modal");
  box.setAttribute("role", "dialog"); box.setAttribute("aria-modal", "true"); box.setAttribute("aria-label", title);
  const close = () => { shade.remove(); previous?.focus(); };
  box.append(button("Close", close, "bc-modal-close"), element("h2", "", title));
  shade.append(box); parent.append(shade);
  shade.onclick = e => { if (e.target === shade) close(); };
  shade.onkeydown = e => {
    if (e.key === "Escape") close();
    if (e.key !== "Tab") return;
    const items = [...box.querySelectorAll<HTMLElement>('button, a[href], input, select, textarea, [tabindex="0"]')].filter(el => !el.hidden && !(el as HTMLButtonElement).disabled);
    const first = items[0], last = items.at(-1);
    if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last?.focus(); }
    if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first?.focus(); }
  };
  box.querySelector("button")!.focus();
  return { box, close };
}
