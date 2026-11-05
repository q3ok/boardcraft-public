// Rules window: the canonical English rules Markdown rendered as text (headings, lists, tables, bold, code), with the
// game's mark in the header when opened for one game, or a tab per game when opened from the platform bar.
import { button, element } from "../dom";
import { openDialog } from "./dialog";
import { gameIcon } from "./gameicon";

const esc = (s: string) => s.replace(/[&<>"]/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[c]!);

export function markdownToHtml(md: string): string {
  const out: string[] = [];
  let list = false;
  let table: string[][] | null = null;
  const inline = (s: string) =>
    esc(s)
      .replace(/\[([^\]]+)\]\([^)]*\)/g, "$1")
      .replace(/\*\*(.+?)\*\*/g, "<b>$1</b>")
      .replace(/`([^`]+)`/g, "<code>$1</code>");
  const flushTable = () => {
    if (!table) return;
    const [head, ...rows] = table;
    out.push(`<table class="rank-table"><thead><tr>${head.map(c => `<th>${inline(c)}</th>`).join("")}</tr></thead><tbody>${rows.map(r => `<tr>${r.map(c => `<td>${inline(c)}</td>`).join("")}</tr>`).join("")}</tbody></table>`);
    table = null;
  };
  for (const raw of md.split("\n")) {
    const line = raw.trimEnd();
    if (/^<!--/.test(line)) continue;
    if (line.startsWith("|")) {
      const cells = line.replace(/^\||\|$/g, "").split("|").map(c => c.trim());
      if (cells.every(c => /^:?-+:?$/.test(c))) continue; // the --- separator row
      (table ??= []).push(cells);
      continue;
    }
    flushTable();
    const li = line.match(/^\s*[-*]\s+(.*)$/);
    if (li) {
      if (!list) out.push("<ul>");
      list = true;
      out.push(`<li>${inline(li[1])}</li>`);
      continue;
    }
    if (list) {
      out.push("</ul>");
      list = false;
    }
    const h = line.match(/^(#{1,4})\s+(.*)$/);
    if (h) out.push(`<h${h[1].length}>${inline(h[2])}</h${h[1].length}>`);
    else if (line.match(/^\d+\.\s+/)) out.push(`<p class="num">${inline(line)}</p>`);
    else if (line.trim()) out.push(`<p>${inline(line)}</p>`);
  }
  flushTable();
  if (list) out.push("</ul>");
  return out.join("\n");
}

export interface RulesGame { game_id: string; name: string }
const GAME_NAMES: Record<string, string> = { rentcraft: "Rentcraft", dice_circuit: "Dice Circuit" };

/** The game's mark for the rules header: Rentcraft's red logo, another game's icon and name. */
export function gameMark(gameId: string, name = GAME_NAMES[gameId] ?? gameId): HTMLElement {
  if (gameId === "rentcraft") return element("span", "logo", "RENTCRAFT");
  const mark = element("span", "gbadge");
  mark.append(gameIcon(gameId, 18), element("span", "", name));
  return mark;
}

/** Opens the rules of one game (its mark in the header) or of every game (a tab each). `load` reads the Markdown. */
export function openRules(parent: HTMLElement, games: RulesGame[], load: (gameId: string) => Promise<string>, selected?: string) {
  const modal = openDialog(parent, "Rules");
  modal.box.classList.add("bc-rules-window");
  modal.box.querySelector("h2")!.remove();
  const close = modal.box.querySelector<HTMLButtonElement>(".bc-modal-close")!;
  close.textContent = "✕ Close";
  const head = element("div", "rules-head");
  const body = element("div", "bc-rules-md");
  modal.box.append(head, body);
  let epoch = 0;
  const show = async (id: string) => {
    const mine = ++epoch;
    body.replaceChildren(element("p", "bc-small", "Loading rules…"));
    let text: string;
    try { text = await load(id); } catch { text = "# Rules\n\nThe rules could not be loaded from the server. Close and try again."; }
    if (mine === epoch && body.isConnected) body.innerHTML = markdownToHtml(text);
  };
  if (selected) {
    head.append(gameMark(selected, games.find(g => g.game_id === selected)?.name), element("b", "", " Rules"));
    void show(selected);
  } else {
    const tabs = element("div", "bc-rule-tabs");
    tabs.setAttribute("role", "group");
    tabs.setAttribute("aria-label", "Game");
    for (const g of games) {
      const tab = button("", () => {
        tabs.querySelectorAll("button").forEach(b => b.setAttribute("aria-pressed", String(b === tab)));
        void show(g.game_id);
      });
      tab.append(gameIcon(g.game_id, 16), " " + g.name);
      tab.dataset.game = g.game_id;
      tab.setAttribute("aria-pressed", "false");
      tabs.append(tab);
    }
    head.append(tabs);
    if (games[0]) {
      tabs.querySelector("button")!.setAttribute("aria-pressed", "true");
      void show(games[0].game_id);
    }
  }
  return modal;
}
