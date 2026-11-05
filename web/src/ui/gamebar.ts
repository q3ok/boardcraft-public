// The shared game bar of every table screen (Rentcraft, Dice Circuit, any later game): back, the game's own mark and
// the table name, connection state, Rules, Surrender and optional host buttons. Look: ui/table.css (`.tc-bar`).
import { button, element } from "../dom";
import { gameIcon } from "./gameicon";
import "./table.css";
import { markdownToHtml } from "./rules";

export type Connection = "connected" | "connecting" | "offline" | null;

export interface GameBar {
  readonly element: HTMLElement;
  setTitle(title: string): void;
  setConnection(state: Connection): void;
  /** The Rules button opens this; null hides it. */
  setRules(open: (() => void) | null): void;
  /** A player gets Surrender while the game asks for it; null hides it. */
  setSurrender(confirm: (() => void) | null): void;
  /** Host buttons placed before Rules (for example the replay entry of a finished table). */
  setExtra(items: HTMLElement[]): void;
}

export interface GameBarOptions {
  gameId: string;
  title?: string;
  onBack?: () => void;
  backLabel?: string;
}

const GAME_NAMES: Record<string, string> = { rentcraft: "Rentcraft", dice_circuit: "Dice Circuit" };
const CONNECTION_TEXT: Record<Exclude<Connection, null>, string> = { connected: "● connected", connecting: "◌ connecting…", offline: "○ reconnecting…" };

/** The game's own mark: Rentcraft keeps its red logo, other games show their icon and name. */
export function gameBadge(gameId: string): HTMLElement {
  if (gameId === "rentcraft") return element("span", "logo", "RENTCRAFT");
  const badge = element("span", "gbadge");
  badge.append(gameIcon(gameId, 18), element("span", "", GAME_NAMES[gameId] ?? gameId));
  return badge;
}

export function mountGameBar(options: GameBarOptions): GameBar {
  const bar = element("div", "tc-bar topbar");
  bar.setAttribute("role", "toolbar");
  bar.setAttribute("aria-label", "Game bar");
  const back = button(options.backLabel ?? "← Play", () => options.onBack?.(), "btn sm tc-back");
  back.hidden = !options.onBack;
  const title = element("span", "title");
  const name = element("span", "tc-title", options.title ?? "");
  title.append(gameBadge(options.gameId), name);
  const conn = element("span", "conn");
  conn.setAttribute("role", "status");
  const extra = element("span", "tc-extra");
  const rules = iconButton("📖", "Rules", "btn sm tc-rules");
  const resign = iconButton("🏳", "Surrender", "btn sm resign tc-resign");
  rules.hidden = resign.hidden = true;
  let onRules: (() => void) | null = null, onResign: (() => void) | null = null;
  rules.onclick = () => onRules?.();
  resign.onclick = () => onResign?.();
  bar.append(back, title, conn, extra, rules, resign);
  return {
    element: bar,
    setTitle(text) { name.textContent = text; },
    setConnection(state) { conn.textContent = state ? CONNECTION_TEXT[state] : ""; },
    setRules(open) { onRules = open; rules.hidden = !open; },
    setSurrender(confirm) { onResign = confirm; resign.hidden = !confirm; },
    setExtra(items) { extra.replaceChildren(...items); },
  };
}

function iconButton(icon: string, label: string, className: string) {
  const b = element("button", className);
  b.type = "button";
  b.title = label;
  b.setAttribute("aria-label", label);
  b.append(icon, element("span", "lbl", " " + label));
  return b;
}

/** A confirmation dialog over the table; `onYes` runs after the confirm button. Returns the dialog element. */
export function confirmDialog(host: HTMLElement, title: string, text: string, yesLabel: string, onYes: () => void): HTMLElement {
  const shade = element("div", "modal confirm");
  const box = element("div", "modal-box");
  box.setAttribute("role", "dialog");
  box.setAttribute("aria-modal", "true");
  box.setAttribute("aria-label", title);
  const close = () => shade.remove();
  const yes = button(yesLabel, () => { close(); onYes(); }, "btn pri");
  const row = element("div", "btns end");
  row.append(button("Cancel", close, "btn"), yes);
  box.append(element("h3", "", title), element("p", "", text), row);
  shade.append(box);
  shade.addEventListener("click", e => { if (e.target === shade) close(); });
  shade.addEventListener("keydown", e => { if (e.key === "Escape") close(); });
  host.append(shade);
  yes.focus();
  return shade;
}

/** A short note over the table (not an error). */
export function toast(host: HTMLElement, text: string, ms = 3500) {
  const t = element("div", "toast", text);
  t.setAttribute("role", "status");
  host.append(t);
  setTimeout(() => t.remove(), ms);
}

/** The rules of the game in a window over the table, with the table's own settings when they are known. */
export function rulesDialog(host: HTMLElement, gameName: string, text: string, settings: [string, string][] = []) {
  const shade = element("div", "modal");
  const box = element("div", "modal-box rules");
  box.setAttribute("role", "dialog");
  box.setAttribute("aria-modal", "true");
  box.setAttribute("aria-label", gameName + " rules");
  const close = () => shade.remove();
  const head = element("h3", "rules-head");
  head.append(element("span", "", gameName + " rules"));
  box.append(button("✕", close, "btn sm close"), head);
  if (settings.length) {
    const details = element("details");
    details.append(element("summary", "", "This table's settings"));
    const list = element("dl", "kv");
    for (const [k, v] of settings) list.append(element("dt", "", k), element("dd", "", v));
    details.append(list);
    box.append(details);
  }
  const rendered = element("div", "rules-text bc-rules-md");
  rendered.innerHTML = markdownToHtml(text);
  box.append(rendered);
  shade.append(box);
  shade.addEventListener("click", e => { if (e.target === shade) close(); });
  shade.addEventListener("keydown", e => { if (e.key === "Escape") close(); });
  host.append(shade);
  box.querySelector<HTMLButtonElement>("button")!.focus();
  return shade;
}
