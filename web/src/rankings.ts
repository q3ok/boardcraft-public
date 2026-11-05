// Rankings: the global ranking of one game (picked in the filter bar), counted by the server from every recorded
// tournament game. A separate ranking for each table size: solo players are ordered by their mean score, players at
// tables of 2 or more by their rating from the places (Bradley-Terry on the Elo scale). The server computes and stores
// everything (when a tournament completes and at its start); this page only shows the stored answer.
import { button, element } from "./dom";
import { gameIcon } from "./ui/gameicon";

export interface RankRow {
  identity: string; name: string; kind: string; games: number; ranked: boolean;
  mean_score: number; ci: number; median: number; best_score: number; wins: number; win_rate: number;
  elo?: number | null; avg_place?: number;
  paired?: { vs: string; vs_name?: string; delta: number; ci: number; pairs: number } | null;
}
export interface RankTable {
  key: { game_id: string; variant: string; players: number; rules_version: string; settings_digest?: string };
  engine_version: string; games: number; reference_name: string | null; rows: RankRow[];
}
export interface GlobalRanking { game_id: string; players: number; min_ranked_games: number; computed_at: number | null; tables: RankTable[] }
interface GameInfo { game_id: string; name: string; min_players?: number; max_players?: number }
export interface RankingsPageOptions {
  request: <T = any>(path: string) => Promise<T>;
  games: () => Promise<GameInfo[]>;
  game: string;
  /** The table size from the address (1 = solo). */
  players?: string;
  /** Keeps the game and the table size in the address (#/rankings?game=…&players=…). */
  setAddress: (game: string, players: number) => void;
}

const KIND: Record<string, string> = { bot: "🤖 bot", model: "🧠 RL model", hybrid: "🧩 combined player", agent: "🔌 AI agent", human: "👤 human" };
const esc = (s: unknown) => String(s ?? "").replace(/[&<>"]/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[c]!);
const pm = (v: number, d = 1) => `${v > 0 ? "+" : ""}${v.toFixed(d)}`;
const num = (v: number) => (Number.isInteger(v) ? String(v) : v.toFixed(1));
const barCell = (value: number, top: number, text: string) =>
  `<div class="bar-cell"><i style="width:${Math.max(0, Math.min(100, (value / (top || 1)) * 100)).toFixed(1)}%"></i><span>${text}</span></div>`;
const withInterval = (mean: number, half: number) => `${mean.toFixed(1)} <span class="muted small">± ${half.toFixed(1)}</span>`;

interface Col { h: string; tip?: string; num?: boolean; cell: (r: RankRow) => string }

/** One ranking table: the ranked rows numbered, below them the rows with too few games (without a place). */
export function rankTableHtml(rows: RankRow[], cols: Col[], notRanked: string): string {
  const ranked = rows.filter(r => r.ranked), rest = rows.filter(r => !r.ranked);
  const head = cols.map(c => `<th${c.num ? ` class="num"` : ""}${c.tip ? ` title="${esc(c.tip)}"` : ""}>${c.h}</th>`).join("");
  const line = (r: RankRow, n: string, cls = "") => `<tr class="${cls}"><td>${n}</td>${cols.map(c => `<td${c.num ? ` class="num"` : ""}>${c.cell(r)}</td>`).join("")}</tr>`;
  const sep = rest.length ? `<tr><td colspan="${cols.length + 1}" class="muted small"><b>${esc(notRanked)}</b></td></tr>` : "";
  return `<table class="rank-table"><thead><tr><th>#</th>${head}</tr></thead><tbody>${ranked.map((r, i) => line(r, String(i + 1))).join("")}${sep}${rest.map(r => line(r, "-", "prov")).join("")}</tbody></table>`;
}

const player = (r: RankRow) => `<b title="${esc(`Identity ${r.identity}`)}">${esc(r.name)}</b>`;

function soloCols(table: RankTable, dice: boolean): Col[] {
  const top = Math.max(1, ...table.rows.map(r => r.mean_score + r.ci));
  const vs = table.reference_name ?? "Greedy";
  return [
    { h: "Player", cell: player },
    { h: "Kind", cell: r => KIND[r.kind] ?? esc(r.kind) },
    { h: "Games", num: true, cell: r => String(r.games) },
    { h: "Mean score ± 95%", num: true, tip: "Average final score over the player's solo games; ± is the 95% interval of that average. Players are ordered by this value.", cell: r => barCell(r.mean_score, top, withInterval(r.mean_score, r.ci)) },
    { h: "Median", num: true, tip: "The middle final score: half of the games scored more, half less.", cell: r => num(r.median) },
    { h: `vs ${esc(vs)}, same ${dice ? "dice" : "seeds"}`, num: true, tip: `Mean difference of the final score in games played on identical ${dice ? "dice " : ""}seeds, with its 95% interval and the number of pairs. Only where such games exist.`,
      cell: r => (r.paired ? `${pm(r.paired.delta)} <span class="muted small">± ${r.paired.ci.toFixed(1)} · ${r.paired.pairs} pairs</span>` : `<span class="muted">-</span>`) },
    { h: "Best score", num: true, tip: "The record: the highest single game. Shown for information only - it is not used for the order.", cell: r => `<span class="muted">${num(r.best_score)}</span>` },
  ];
}

function multiCols(): Col[] {
  return [
    { h: "Player", cell: player },
    { h: "Kind", cell: r => KIND[r.kind] ?? esc(r.kind) },
    { h: "Rating", num: true, tip: "Bradley-Terry strength on the Elo scale, computed from the final places (1500 = average player). Players are ordered by this value.", cell: r => (r.elo == null ? "-" : r.elo.toFixed(0)) },
    { h: "Games", num: true, cell: r => String(r.games) },
    { h: "Wins", num: true, cell: r => String(r.wins) },
    { h: "% wins", num: true, cell: r => `${(r.win_rate * 100).toFixed(1)} %` },
    { h: "Avg. place", num: true, cell: r => (r.avg_place ?? 0).toFixed(2) },
    { h: "Mean score", num: true, tip: "Average final score in these games - an extra column, not used for the order.", cell: r => r.mean_score.toFixed(1) },
  ];
}

export function mountRankings(root: HTMLElement, options: RankingsPageOptions) {
  const life = new AbortController();
  const card = element("section", "bc-panel bc-rankings");
  const bar = element("div", "fbar");
  const picker = element("div", "gpicker");
  picker.setAttribute("role", "group");
  picker.setAttribute("aria-label", "Game");
  const pick = element("div", "ff");
  pick.append(element("span", "fl", "Game"), picker);
  bar.append(pick);
  const panel = element("div", "bc-ranking");
  card.append(element("h2", "", "Rankings"), bar, panel);
  root.append(card);
  let games: GameInfo[] = [], game = options.game, players = Number(options.players) || 0, epoch = 0;

  const info = () => games.find(g => g.game_id === game);
  function sizes() {
    const g = info(), lo = Math.max(1, g?.min_players ?? 1), hi = Math.max(lo, g?.max_players ?? 4);
    return Array.from({ length: hi - lo + 1 }, (_, i) => lo + i);
  }

  /** The panel of the chosen game: the table-size picker, the explanation, the table and how it is calculated. */
  function drawPanel() {
    const g = info(), all = sizes();
    if (!all.includes(players)) players = all[0];
    options.setAddress(game, players);
    const h3 = element("h3", "", "Ranking ");
    h3.append(element("span", "muted", `${g?.name ?? game}, recorded games - a separate ranking for each table size`));
    const sizeBar = element("div", "fbar");
    const sizePick = element("div", "gpicker");
    sizePick.setAttribute("role", "group");
    sizePick.setAttribute("aria-label", "Table size");
    for (const n of all) {
      const b = button(n === 1 ? "Solo" : `${n} players`, () => {
        sizePick.querySelectorAll("button").forEach(x => x.setAttribute("aria-pressed", String(x === b)));
        players = n;
        options.setAddress(game, players);
        void load();
      }, "gp");
      b.setAttribute("aria-pressed", String(n === players));
      sizePick.append(b);
    }
    const field = element("div", "ff");
    field.append(element("span", "fl", "Table size"), sizePick);
    sizeBar.append(field);
    panel.replaceChildren(h3, sizeBar, element("p", "muted small bc-rank-why"), element("div", "bc-rank-table mt-5"), element("p", "muted mt-5 bc-rank-how"));
    void load();
  }

  async function load() {
    const mine = ++epoch, n = players, dice = game === "dice_circuit";
    const why = panel.querySelector<HTMLElement>(".bc-rank-why"), box = panel.querySelector<HTMLElement>(".bc-rank-table"), how = panel.querySelector<HTMLElement>(".bc-rank-how");
    if (!why || !box || !how) return;
    why.textContent = n === 1
      ? "Solo: there is no rival, so a place means nothing - players are ordered by their mean score."
      : `${n} players: players are ordered by their places against rivals (rating); mean score is only an extra column. Each table size has its own ranking.`;
    box.replaceChildren(element("p", "muted", "Loading the ranking…"));
    how.replaceChildren();
    let data: GlobalRanking;
    try {
      data = await options.request<GlobalRanking>(`/api/rankings/global?game_id=${encodeURIComponent(game)}&players=${n}`);
    } catch {
      if (mine === epoch && !life.signal.aborted) box.replaceChildren(element("p", "muted", "The ranking could not be loaded."));
      return;
    }
    if (mine !== epoch || life.signal.aborted) return;
    const min = data.min_ranked_games;
    const notRanked = `Too few games (fewer than ${min}) - not ranked yet`;
    const tables = data.tables.filter(t => t.rows.length);
    box.innerHTML = tables.length
      ? tables.map(t => {
          const caption = tables.length > 1
            ? `<p class="muted small">${esc(t.key.variant)} · rules ${esc(t.key.rules_version)}${t.key.settings_digest ? " · own rule options" : ""} · ${t.games} games</p>` : "";
          return caption + rankTableHtml(t.rows, n === 1 ? soloCols(t, dice) : multiCols(), notRanked);
        }).join("")
      : `<div class="muted">No recorded ${n === 1 ? "solo" : `${n}-player`} games yet.</div>`;
    const vs = tables[0]?.reference_name ?? "Greedy";
    how.innerHTML = n === 1
      ? `<b>How it is calculated:</b> the order is the mean final score of the player's recorded solo games, with a 95% interval for that mean (mean ± 1.96 × standard deviation ÷ √games). A player needs at least ${min} games to be ranked; below that the row is listed without a place. Two players whose intervals overlap are not clearly different - the “vs ${esc(vs)}, same ${dice ? "dice" : "seeds"}” column compares games played on identical ${dice ? "dice " : ""}seeds, which removes most of the luck of the ${dice ? "dice" : "draw"} (shown only where such games exist). The median is the middle score; the best score is a record, shown for information and never used for the order.`
      : `<b>How it is calculated:</b> every game is a set of head-to-head duels by final place - not only who won, but whom you finished ahead of. From all duels at once a strength is computed (a Bradley-Terry model) on the Elo scale: 1500 is an average player, a 100-point advantage ≈ 64% chance of finishing higher. Games of different table sizes are never mixed here. A player needs at least ${min} games to be ranked. The mean score is shown as an extra column and does not change the order.${dice ? " Ties on points are broken by the highest single colour area (see the rules)." : ""}`;
  }

  void options.games().then(items => {
    if (life.signal.aborted) return;
    games = items;
    if (!games.some(g => g.game_id === game) && games[0]) game = games[0].game_id;
    for (const g of games) {
      const b = button("", () => {
        picker.querySelectorAll("button").forEach(x => x.setAttribute("aria-pressed", String(x === b)));
        if (game !== g.game_id) { game = g.game_id; players = 0; drawPanel(); }
      }, "gp");
      b.append(gameIcon(g.game_id, 16), element("span", "", g.name));
      b.setAttribute("aria-pressed", String(g.game_id === game));
      picker.append(b);
    }
    drawPanel();
  }).catch(() => { if (!life.signal.aborted) panel.replaceChildren(element("p", "bc-empty", "Rankings could not be loaded.")); });
  return { dispose() { life.abort(); card.remove(); } };
}
