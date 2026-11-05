// Replays: every recorded game of this server (tables and tournament matches), 20 to a page. The server orders them
// (newest first), filters them (game, player, only wins, tournament) and names the players; the page only shows them.
// On a server that allows managing (a private one) replays are selected and deleted by the server.
import { button, element } from "./dom";
import { gameIcon } from "./ui/gameicon";

export interface ReplayItem {
  source: "table" | "tournament";
  table_id?: string;
  tournament_id?: string;
  match_id?: string;
  tournament_name?: string;
  game_id: string;
  participants: { display_name: string | null; kind: string; seat: number }[];
  players: number;
  played_at?: number;
  result: { placements: number[]; scores: number[] };
  replay_path: string;
}
export interface ReplayPage { items: ReplayItem[]; total: number; offset: number; limit: number; can_manage?: boolean }
export interface ReplaysPageOptions {
  request: <T = any>(path: string) => Promise<T>;
  /** POST with a JSON body (deleting replays). */
  send?: <T = any>(path: string, body: unknown) => Promise<T>;
  games: () => Promise<{ game_id: string; name: string }[]>;
  game: string;
  /** Opens a table replay (the table screen at position 0). */
  openTable: (tableId: string, gameId: string) => void;
  /** Opens a tournament (its match replays live there). */
  openTournament: (tournamentId: string) => void;
  /** Keeps the game filter in the address. */
  setGame: (game: string) => void;
}
const PAGE = 20;
const SEARCH_DELAY = 300; // ms after the last keystroke before the query is sent

const resultText = (r: ReplayItem, names: string[]) => {
  const winner = r.result.placements.indexOf(1);
  if (r.players === 1) return `Solo · ${r.result.scores[0]} points`;
  return `Won by ${names[winner] ?? "?"}`;
};

/** The address of one page of the replay list with the search (empty fields are not sent). */
export function replaysUrl(offset: number, filter: { game: string; q: string; won: boolean; tournament: string }) {
  const p = new URLSearchParams({ offset: String(offset), limit: String(PAGE) });
  if (filter.game) p.set("game_id", filter.game);
  if (filter.q.trim()) {
    p.set("q", filter.q.trim());
    if (filter.won) p.set("won", "1");
  }
  if (filter.tournament.trim()) p.set("tournament", filter.tournament.trim());
  return `/api/replays?${p}`;
}

/** What the server deletes for one replay row. */
const replayRef = (r: ReplayItem) => r.source === "table"
  ? { source: "table", table_id: r.table_id }
  : { source: "tournament", tournament_id: r.tournament_id, match_id: r.match_id };

export function mountReplays(root: HTMLElement, options: ReplaysPageOptions) {
  const life = new AbortController();
  const card = element("section", "bc-panel bc-replays");
  const head = element("h2", "", "Game replays ");
  head.append(element("span", "bc-small", "every game is recorded by the server"));
  const deleteSelected = button("🗑 Delete selected", () => void remove(selected()), "sm");
  deleteSelected.disabled = true;
  deleteSelected.hidden = true;
  head.append(element("span", "sp"), deleteSelected);
  const bar = element("div", "fbar");
  const field = (label: string, ...controls: HTMLElement[]) => { const box = element("div", "ff"); box.append(element("span", "fl", label), ...controls); return box; };
  const picker = element("div", "gpicker");
  picker.setAttribute("role", "group");
  picker.setAttribute("aria-label", "Game");
  const q = element("input", "fc");
  q.type = "search"; q.placeholder = "Bot, model, agent (e.g. Strategist)"; q.setAttribute("aria-label", "Player");
  const wonLabel = element("label", "fcheck");
  const won = element("input"); won.type = "checkbox";
  wonLabel.append(won, " only wins");
  wonLabel.title = "Only games won by the player you search for";
  const t = element("input", "fc");
  t.type = "search"; t.placeholder = "Tournament name"; t.setAttribute("aria-label", "Tournament");
  const count = element("span", "fcount");
  bar.append(field("Game", picker), field("Player", q), field("Result", wonLabel), field("Tournament", t), count);
  bar.children[1].classList.add("grow"); bar.children[3].classList.add("grow");
  const list = element("div", "bc-replay-list");
  list.setAttribute("aria-live", "polite");
  list.append(element("p", "bc-small", "Loading replays…"));
  const pager = element("div", "bc-pager");
  card.append(head, bar, list, pager);
  root.append(card);
  let offset = 0, game = options.game, epoch = 0, rows: ReplayItem[] = [];
  const filter = () => ({ game, q: q.value, won: won.checked, tournament: t.value });
  const searching = () => !!(q.value.trim() || t.value.trim() || game);

  async function load() {
    const mine = ++epoch;
    let page: ReplayPage;
    try {
      page = await options.request<ReplayPage>(replaysUrl(offset, filter()));
    } catch {
      if (mine === epoch && !life.signal.aborted) list.replaceChildren(element("p", "bc-empty", "No connection to the game server."));
      return;
    }
    if (life.signal.aborted || mine !== epoch) return;
    if (!page.items.length && offset > 0 && page.total > 0) {
      // The last page emptied (deleted replays): show the last page that still has games.
      offset = Math.max(0, Math.floor((page.total - 1) / PAGE) * PAGE);
      return load();
    }
    render(page);
  }
  const namesOf = (r: ReplayItem) => [...r.participants].sort((a, b) => a.seat - b.seat).map(p => p.display_name ?? `Player ${p.seat + 1}`);
  const checks = () => [...list.querySelectorAll<HTMLInputElement>("[data-sel]")];
  const selected = () => checks().filter(c => c.checked).map(c => rows[Number(c.dataset.sel)]);
  const updateSelected = () => {
    const n = selected().length;
    deleteSelected.disabled = !n;
    deleteSelected.textContent = n ? `🗑 Delete selected (${n})` : "🗑 Delete selected";
  };
  function render(page: ReplayPage) {
    const total = page.total, manage = !!page.can_manage && !!options.send, noun = searching() ? "matching games" : "games";
    rows = page.items;
    deleteSelected.hidden = !manage;
    count.textContent = searching() ? `${total} ${noun}` : "";
    if (!rows.length) {
      list.replaceChildren(element("p", "bc-empty", searching() ? "No game matches the search. Try a shorter part of the name or clear the fields."
        : "There are no recorded games yet. Play one, or watch a bots-only table in Play."));
      pager.replaceChildren();
      updateSelected();
      return;
    }
    list.replaceChildren(...rows.map((r, index) => {
      const names = namesOf(r);
      const winner = r.result.placements.indexOf(1);
      const row = element("div", "table-row rec");
      if (manage) {
        const check = element("input");
        check.type = "checkbox"; check.dataset.sel = String(index); check.title = "Select for deletion";
        check.setAttribute("aria-label", "Select for deletion");
        check.onchange = updateSelected;
        row.append(check);
      }
      const info = element("div", "info");
      const title = element("div", "t");
      title.append(gameIcon(r.game_id, 18), element("span", "bc-small", ` ${gameName(r.game_id)} `), element("b", "", resultText(r, names)));
      const chips = element("div", "chips");
      names.forEach((n, i) => {
        const chip = element("span", `chip${i === winner && r.players > 1 ? " win" : ""}`);
        chip.append(element("span", `av pcbg pc-${i % 4}`), `${i === winner && r.players > 1 ? "🏆 " : ""}${n}${r.result.scores ? ` · ${r.result.scores[i]}` : ""}`);
        chips.append(chip);
      });
      const where = r.source === "tournament" ? `tournament ${r.tournament_name ?? (r.tournament_id ?? "").slice(0, 8)}` : `table ${(r.table_id ?? "").slice(0, 8)}`;
      const when = r.played_at ? `${new Date(r.played_at * 1000).toLocaleString("en-GB")} · ` : "";
      info.append(title, chips, element("div", "bc-small", `${when}${where}`));
      const play = button("▶ Play", () => r.source === "table" && r.table_id ? options.openTable(r.table_id, r.game_id) : r.tournament_id && options.openTournament(r.tournament_id), "bc-primary");
      play.setAttribute("aria-label", `Play the replay of ${where}`);
      row.append(info, play);
      if (manage) {
        const del = button("🗑", () => void remove([r]), "sm del");
        del.title = "Delete the replay"; del.setAttribute("aria-label", "Delete the replay");
        row.append(del);
      }
      return row;
    }));
    updateSelected();
    const pages = Math.max(1, Math.ceil(total / PAGE)), pageNo = Math.floor(offset / PAGE) + 1;
    pager.replaceChildren();
    if (total > PAGE) {
      const newer = button("← Newer", () => { offset = Math.max(0, offset - PAGE); void load(); });
      const older = button("Older →", () => { offset += PAGE; void load(); });
      newer.disabled = pageNo === 1; older.disabled = pageNo === pages;
      pager.append(newer, element("span", "bc-small", `page ${pageNo} of ${pages} · ${total} ${noun}`), older);
    } else pager.append(element("span", "bc-small", `${total} ${noun}`));
  }
  async function remove(items: ReplayItem[]) {
    if (!items.length || !options.send) return;
    const what = items.length === 1 ? "this replay" : `${items.length} replays`;
    if (!confirm(`Delete ${what}? This cannot be undone, and the rankings will be recalculated without these games.`)) return;
    deleteSelected.disabled = true;
    try { await options.send("/api/replays/delete", { replays: items.map(replayRef) }); }
    catch (error) { alert(`The replays could not be deleted: ${(error as Error).message}`); }
    if (!life.signal.aborted) await load();
  }
  let gameNames = new Map<string, string>();
  const gameName = (id: string) => gameNames.get(id) ?? id;
  let typing: ReturnType<typeof setTimeout> | undefined;
  const search = () => { clearTimeout(typing); offset = 0; void load(); };
  const later = () => { clearTimeout(typing); typing = setTimeout(search, SEARCH_DELAY); };
  q.oninput = t.oninput = later;
  q.onkeydown = t.onkeydown = e => { if (e.key === "Enter") search(); };
  won.onchange = () => { if (q.value.trim()) search(); };
  void options.games().then(games => {
    if (life.signal.aborted) return;
    gameNames = new Map(games.map(g => [g.game_id, g.name]));
    for (const g of [{ game_id: "", name: "All games" }, ...games]) {
      const b = button("", () => {
        picker.querySelectorAll("button").forEach(x => x.setAttribute("aria-pressed", String(x === b)));
        game = g.game_id;
        options.setGame(game);
        offset = 0;
        void load();
      }, "gp");
      if (g.game_id) b.append(gameIcon(g.game_id, 16));
      b.append(element("span", "", g.name));
      b.setAttribute("aria-pressed", String(g.game_id === game));
      picker.append(b);
    }
    void load();
  }).catch(() => void load());
  return { dispose() { life.abort(); clearTimeout(typing); card.remove(); } };
}
