// Tournaments (the shared design): the list on the left (game filter, progress, leader), on the right a new tournament
// or the open tournament's results. Three line-ups: a fixed line-up whose seats rotate over shared seeds (with one player
// per table a solo tournament: every entrant plays the same seeds alone, compared by mean score); line-ups drawn from a
// pool (every combination in turn); and a knockout that plays rounds of candidates with sparring partners.
import { button, element } from "./dom";
import { gameIcon } from "./ui/gameicon";
import { gameMark } from "./ui/rules";
import { mountReplayBar } from "./ui/replaybar";
import { SOLO_PREFIX, describe, histogram, soloStats } from "./solo";
import type { BotDescriptor } from "./play-types";
import { timestampName } from "./ui/time";

/** A prefilled new-tournament form (for example "Do turnieju" of a host). Only public game and choice IDs. */
export interface TournamentDraft {
  game_id: string;
  /** Players at each table; 1 = solo tournament (each entrant alone on the same seeds). */
  players?: number;
  entrants: (string | { choice_id: string; display_name?: string })[];
  /** Games: per entrant in a solo tournament, in all for a line-up. */
  games?: number;
  /** fixed (default): every entrant at every table; pool: tables of `players` drawn from the entrants; knockout: candidates with sparring partners. */
  mode?: "fixed" | "pool" | "knockout";
  /** Knockout: the entrants (choice IDs) that are sparring partners; the others are candidates. */
  sparring?: string[];
  name?: string;
  seed?: number | null;
}
interface GameInfo { game_id: string; name: string; min_players: number; max_players: number; variants?: string[]; rules_version?: string; engine_version?: string }
interface Match { match_id: string; ordinal: number; status: string; result?: { scores: number[]; placements: number[]; identities: string[] } }
/** Per entrant (in spec order) as the server stores it: games, wins and the sums of places and scores. */
interface EntrantResult { games: number; wins: number; place: number; score: number }
interface Tournament {
  tournament_id: string; status: string; revision: number; spec_sha: string; matches?: Match[];
  /** When the server saved it (seconds); the server lists the newest first. */
  created?: number | null;
  /** The list row the server keeps: completed games and per-entrant results (no match rows). */
  done?: number; results?: EntrantResult[];
  /** A solo tournament read on its own: the score of every game by ordinal (null: not played yet). */
  scores?: (number | null)[] | null;
  /** The server's answer: may any client manage it (a private server with open management)? */
  can_manage?: boolean; management?: "open" | "credential";
  /** The pinned entrants as the server names them (in spec order). */
  entrants?: { choice_id: string; display_name: string }[];
  spec: { name: string; seeds?: number[]; seed_count?: number; first_seed?: number | null; pool?: boolean; rotations: number[]; seats?: (string | { choice_id: string })[]; lineups?: number[][]; match_count: number; max_inflight: number;
    config: { game_id: string; players: number; variant: string; settings?: Record<string, unknown> } };
}
interface KnockoutRow { candidate: number; label: string; games: number; wins: number; win_rate: number; avg_place: number; rank: number }
interface Knockout {
  knockout_id: string; status: string; revision: number; error: string | null; created?: number; can_manage?: boolean; management?: "open" | "credential";
  spec: { name: string; config: { game_id: string; players: number; variant: string; settings?: Record<string, unknown> }; candidates: (string | { choice_id: string })[];
    sparring: (string | { choice_id: string })[]; games: number; keep: number; min_candidates: number; metric: "wins" | "place"; seed: number; random_seed: boolean; max_inflight: number };
  labels: { candidates: string[]; sparring: string[] };
  plan: { round: number; candidates: number; survivors: number; games: number }[];
  rounds: { round: number; candidates: number; games: number; planned_games: number; finished: boolean; survivors: number[]; table: KnockoutRow[]; tournaments: Record<string, string> }[];
  winner: number | null; top: number[]; games_done: number; games_total: number;
}
/** Rounds of a knockout: candidates, survivors and the game budget of each (the same plan as the server). */
export function halvingPlan(n: number, games: number, keep: number, minCandidates: number) {
  const sizes = [n];
  while (sizes[sizes.length - 1] > minCandidates) {
    const cur = sizes[sizes.length - 1];
    sizes.push(Math.min(cur - 1, Math.max(minCandidates, Math.ceil(cur * keep - 1e-9))));
  }
  const rounds = sizes.length - 1, base = Math.floor(games / rounds), extra = games % rounds;
  return sizes.slice(0, -1).map((c, i) => ({ round: i + 1, candidates: c, survivors: sizes[i + 1], games: base + (i >= rounds - extra ? 1 : 0) }));
}
/** Line-ups of a pool tournament: every combination of `size` entrants in turn; with more combinations than groups a seeded draw. */
export function poolLineups(entrants: number, size: number, groups: number, seed: number): number[][] {
  const combos: number[][] = [];
  const walk = (start: number, picked: number[]) => {
    if (combos.length > 5000) return;
    if (picked.length === size) { combos.push(picked); return; }
    for (let i = start; i < entrants; i++) walk(i + 1, [...picked, i]);
  };
  walk(0, []);
  if (combos.length <= groups) return Array.from({ length: groups }, (_, g) => combos[g % combos.length]);
  let x = (seed >>> 0) || 1;
  const next = () => { x ^= x << 13; x >>>= 0; x ^= x >>> 17; x ^= x << 5; x >>>= 0; return x; };
  return Array.from({ length: groups }, () => {
    const pool = Array.from({ length: entrants }, (_, i) => i);
    for (let i = pool.length - 1; i > 0; i--) { const j = next() % (i + 1); [pool[i], pool[j]] = [pool[j], pool[i]]; }
    return pool.slice(0, size).sort((a, b) => a - b);
  });
}
export interface TournamentsPageOptions {
  request: <T = any>(path: string, body?: unknown, credential?: string) => Promise<T>;
  games: () => Promise<GameInfo[]>;
  game: string;
  selected: string | null;
  draft?: TournamentDraft | null;
  /** Keeps the open tournament and the game filter in the address. */
  setAddress: (selected: string | null, game: string) => void;
}

const STATE: Record<string, string> = { running: "● running", pending: "ready", draining: "⏳ stopping…", stopping: "⏳ stopping…", completed: "finished", stopped: "stopped", failed: "error" };
const GAME_COUNTS = [100, 200, 500, 1000];
const PAGE = 12;
const pct = (x: number) => `${(x * 100).toFixed(1)}%`;
const esc = (s: unknown) => String(s ?? "").replace(/[&<>"]/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[c]!);
const choiceOf = (seat: string | { choice_id: string }) => (typeof seat === "string" ? seat : seat.choice_id);
/** The entrants of a tournament in spec order: the server's names, or the seats a test or an older server sent. */
const choicesOf = (t: Tournament) => t.entrants?.map(e => e.choice_id) ?? (t.spec.seats ?? []).map(choiceOf);
const KO = "ko:";
const busy = (status: string) => ["running", "draining", "stopping", "starting"].includes(status);

/** 95% Wilson interval for the win rate (wins w of n games). */
export function wilson(w: number, n: number): [number, number] {
  if (!n) return [0, 0];
  const z = 1.96, p = w / n, den = 1 + (z * z) / n;
  const c = (p + (z * z) / (2 * n)) / den;
  const h = (z * Math.sqrt((p * (1 - p)) / n + (z * z) / (4 * n * n))) / den;
  return [Math.max(0, c - h), Math.min(1, c + h)];
}

/** Seeds (seed groups) of a tournament and the first one: the list row carries the counts, a full spec the seeds. */
const seedCount = (t: Tournament) => t.spec.seed_count ?? t.spec.seeds?.length ?? 0;
const firstSeed = (t: Tournament) => t.spec.first_seed ?? t.spec.seeds?.[0];
const isPool = (t: Tournament) => t.spec.pool ?? !!t.spec.lineups?.length;
/** Completed games: the server's count, or the completed matches of a full status. */
const doneOf = (t: Tournament) => t.done ?? (t.matches ?? []).filter(m => m.status === "completed").length;

/** The results of a line-up tournament per entrant: games, wins, win rate and average place (seats rotate). The server
 *  keeps them per entrant (`results`); a full status with its matches is counted here the same way. */
export function lineupResults(t: Tournament, names: (choice: string) => string) {
  const n = t.spec.config.players, R = t.spec.rotations.length;
  const choices = choicesOf(t);
  const label = (i: number) => t.entrants?.[i]?.display_name ?? names(choices[i]);
  const rows = choices.map((choice, i) => ({ label: choice, name: label(i) + (choices.indexOf(choice) !== i ? ` #${i + 1}` : ""), games: 0, wins: 0, place: 0, score: 0 }));
  const rates = () => rows.map(r => ({ ...r, win_rate: r.games ? r.wins / r.games : 0, avg_place: r.games ? r.place / r.games : 0, mean: r.games ? r.score / r.games : 0 }));
  if (t.results) {
    t.results.forEach((r, i) => { if (rows[i]) Object.assign(rows[i], { games: r.games, wins: r.wins, place: r.place, score: r.score }); });
    return rates();
  }
  for (const m of t.matches ?? []) {
    if (m.status !== "completed" || !m.result) continue;
    const rotation = t.spec.rotations[m.ordinal % R] ?? 0;
    // A pool tournament seats the group's line-up (indices into the entrants); a fixed one seats everybody.
    const lineup = t.spec.lineups?.[Math.floor(m.ordinal / R)] ?? choices.map((_, i) => i);
    for (let seat = 0; seat < n; seat++) {
      // The schedule turns the line-up by `rotation`: seat s holds line-up member (s - rotation) mod n.
      const row = rows[lineup[((seat - rotation) % n + n) % n]];
      row.games++;
      row.place += m.result.placements[seat];
      row.score += m.result.scores[seat];
      if (m.result.placements[seat] === 1) row.wins++;
    }
  }
  return rates();
}

const credKey = "boardcraft.tournament.credentials";
const owned = (): Record<string, string> => { try { return JSON.parse(localStorage.getItem(credKey) ?? "{}"); } catch { return {}; } };
const remember = (id: string, credential: string) => { try { localStorage.setItem(credKey, JSON.stringify({ ...owned(), [id]: credential })); } catch { /* this visit only */ } };
// The solo form of an earlier version kept its own credentials.
const soloOwned = (): Record<string, string> => { try { return JSON.parse(localStorage.getItem("boardcraft.solo.credentials") ?? "{}"); } catch { return {}; } };
const credentialOf = (id: string): string | undefined => owned()[id] ?? soloOwned()[id];
/** Management buttons follow the server: everything when it says so (open management), otherwise only with the
 *  creator credential this browser received when it created the tournament (the public server). */
const manageable = (item: { can_manage?: boolean }, id: string) => !!item.can_manage || !!credentialOf(id);

/** Seeds for a tournament: random, or derived from one fixed seed so that the same seed repeats the same games. */
export function seedList(count: number, fixed: number | null): number[] {
  const out = new Set<number>();
  if (fixed == null) {
    const buffer = new Uint32Array(1);
    while (out.size < count) { crypto.getRandomValues(buffer); out.add(buffer[0]); }
  } else {
    let x = (fixed >>> 0) || 1;
    while (out.size < count) { x ^= x << 13; x >>>= 0; x ^= x >>> 17; x ^= x << 5; x >>>= 0; out.add(x); }
  }
  return [...out];
}

export function mountTournamentsPage(root: HTMLElement, options: TournamentsPageOptions) {
  const life = new AbortController();
  const grid = element("div", "bc-tournaments train-grid");
  const listCard = element("section", "bc-panel");
  const head = element("h2", "", "Tournaments");
  const newButton = button("+ New tournament", () => { selected = null; options.setAddress(null, game); renderNew(); }, "gold");
  head.append(element("span", "sp"), newButton);
  const filter = element("div", "fbar");
  const pickBox = element("div", "ff");
  const picker = element("div", "gpicker"); picker.setAttribute("role", "group"); picker.setAttribute("aria-label", "Game");
  pickBox.append(element("span", "fl", "Game"), picker);
  filter.append(pickBox);
  const list = element("div", "bc-tlist");
  const pager = element("div", "bc-pager");
  listCard.append(head, filter, list, pager);
  const main = element("section", "bc-panel bc-tmain");
  grid.append(listCard, main);
  root.append(grid);
  let games: GameInfo[] = [], game = options.game, selected = options.selected, page = 0, all: Tournament[] = [], knockouts: Knockout[] = [];
  let catalogs = new Map<string, BotDescriptor[]>();
  let timer: ReturnType<typeof setTimeout> | undefined;
  let pendingDraft = options.draft ?? null;
  const gameName = (id: string) => games.find(g => g.game_id === id)?.name ?? id;
  const catalog = async (id: string) => {
    if (!catalogs.has(id)) catalogs.set(id, await options.request<BotDescriptor[]>(`/api/games/${encodeURIComponent(id)}/bots`).catch(() => []));
    return catalogs.get(id)!;
  };
  const nameOf = (gameId: string) => (choice: string) => catalogs.get(gameId)?.find(d => d.choice_id === choice)?.display_name ?? choice;

  // ---------- the list: solo groups appear as one entry ----------
  interface Entry { key: string; title: string; game: string; status: string; done: number; total: number; leader: string; members: Tournament[]; order?: number }
  function entries(): Entry[] {
    // The server lists tournaments and knockouts newest first; the page keeps that order and only places each
    // knockout among the tournaments by the creation time the server gave it.
    const tours: Entry[] = [], groups = new Map<string, Entry>();
    for (const t of all) {
      if (game && t.spec.config.game_id !== game) continue;
      // The rounds of a knockout are its own tournaments; the list shows the knockout once.
      if (t.spec.name.startsWith("Knockout: ")) continue;
      if (t.spec.config.players === 1 && t.spec.name.startsWith(SOLO_PREFIX)) {
        const base = t.spec.name.slice(SOLO_PREFIX.length).split(" · ")[0];
        const key = `solo:${base}:${t.spec.config.game_id}:${firstSeed(t)}:${seedCount(t)}`;
        const group = groups.get(key);
        if (group) { group.members.push(t); continue; }
        const entry: Entry = { key, title: "", game: t.spec.config.game_id, status: "", done: 0, total: 0, leader: "", members: [t], order: t.created ?? 0 };
        groups.set(key, entry); tours.push(entry);
        continue;
      }
      const done = doneOf(t);
      const rows = lineupResults(t, nameOf(t.spec.config.game_id)).sort((a, b) => b.win_rate - a.win_rate);
      const lead = rows[0] && rows[0].games ? (t.status === "completed" ? `🏆 ${rows[0].name}` : `leading ${rows[0].name} ${pct(rows[0].win_rate)}`) : "";
      tours.push({ key: t.tournament_id, title: t.spec.name, game: t.spec.config.game_id, status: t.status, done, total: t.spec.match_count, leader: lead, members: [t], order: t.created ?? 0 });
    }
    for (const e of groups.values()) {
      const members = e.members;
      const stats = soloLeaders(members);
      e.done = members.reduce((a, t) => a + doneOf(t), 0);
      e.total = members.reduce((a, t) => a + t.spec.match_count, 0);
      e.status = members.some(t => busy(t.status)) ? "running" : members.every(t => t.status === "completed") ? "completed" : members[0].status;
      e.title = `${members[0].spec.name.slice(SOLO_PREFIX.length).split(" · ")[0]} (solo)`;
      e.leader = stats[0]?.games ? `${e.status === "completed" ? "🏆" : "leading"} ${stats[0].name} · mean ${stats[0].mean.toFixed(1)}` : "";
    }
    const knocks: Entry[] = [];
    for (const k of knockouts) {
      if (game && k.spec.config.game_id !== game) continue;
      const round = k.rounds.length;
      const lead = k.winner != null ? `🏆 ${k.labels.candidates[k.winner]}` : round ? `round ${round}/${k.plan.length}` : "";
      knocks.push({ key: KO + k.knockout_id, title: k.spec.name, game: k.spec.config.game_id, status: k.status, done: k.games_done, total: k.games_total,
        leader: lead, members: [], order: k.created ?? 0 });
    }
    const out: Entry[] = [];
    let i = 0;
    for (const e of tours) {
      while (i < knocks.length && (knocks[i].order ?? 0) >= (e.order ?? 0)) out.push(knocks[i++]);
      out.push(e);
    }
    return [...out, ...knocks.slice(i)];
  }
  const soloName = (t: Tournament) => t.spec.name.split(" · ").slice(1).join(" · ") || t.entrants?.[0]?.display_name || nameOf(t.spec.config.game_id)(choicesOf(t)[0]);
  function soloEntry(t: Tournament) {
    const scores: (number | null)[] = t.scores ? [...t.scores] : Array.from({ length: seedCount(t) }, () => null);
    if (!t.scores) for (const m of t.matches ?? []) if (m.status === "completed" && m.result) scores[m.ordinal] = m.result.scores[0];
    return { name: soloName(t), tournamentId: t.tournament_id, status: t.status, total: seedCount(t), scores };
  }
  /** The leader of a solo group in the list: the best mean score, from the server's per-entrant sums. */
  function soloLeaders(members: Tournament[]) {
    if (members.some(t => !t.results)) return soloStats(members.map(t => soloEntry(t)));
    return members.map(t => { const r = t.results![0] ?? { games: 0, score: 0 }; return { name: soloName(t), games: r.games, mean: r.games ? r.score / r.games : 0 }; })
      .sort((a, b) => b.mean - a.mean);
  }
  function renderList() {
    const items = entries();
    const pages = Math.max(1, Math.ceil(items.length / PAGE));
    page = Math.min(page, pages - 1);
    list.replaceChildren(...(items.length ? items.slice(page * PAGE, page * PAGE + PAGE).map(e => {
      const a = element("a", `exp${e.key === selected ? " on" : ""}`);
      a.href = "#"; a.onclick = ev => { ev.preventDefault(); selected = e.key; options.setAddress(selected, game); renderList(); void renderDetails(); };
      const n = element("div", "n"), title = element("b");
      title.append(gameIcon(e.game, 16), ` ${e.title}`);
      n.append(title, element("span", `tag${e.status === "running" ? " running" : ""}`, STATE[e.status] ?? e.status));
      const bar = element("div", "bar"), fill = element("i"); fill.style.width = `${e.total ? Math.round((e.done / e.total) * 100) : 0}%`; bar.append(fill);
      a.append(n, element("div", "bc-small", `${e.done} / ${e.total} games${e.leader ? ` · ${e.leader}` : ""}`), bar);
      return a;
    }) : [element("div", "bc-small", "There are no tournaments yet.")]));
    pager.replaceChildren();
    if (items.length > PAGE) {
      const newer = button("← Newer", () => { page--; renderList(); }), older = button("Older →", () => { page++; renderList(); });
      newer.disabled = page === 0; older.disabled = page >= pages - 1;
      pager.append(newer, element("span", "bc-small", `page ${page + 1} of ${pages}`), older);
    }
  }
  async function refresh() {
    clearTimeout(timer);
    try { all = await options.request<Tournament[]>("/api/tournaments"); } catch { return; }
    // An older server has no knockouts.
    knockouts = await options.request<Knockout[]>("/api/knockouts").then(k => (Array.isArray(k) ? k : [])).catch(() => []);
    if (life.signal.aborted) return;
    for (const id of new Set(all.map(t => t.spec.config.game_id))) await catalog(id);
    renderList();
    if (selected && !main.querySelector(".tform")) void renderDetails(true);
    if (all.some(t => busy(t.status)) || knockouts.some(k => busy(k.status))) timer = setTimeout(() => void refresh(), 1500);
  }

  // ---------- new tournament ----------
  function renderNew() {
    const draft = pendingDraft;
    pendingDraft = null;
    const startGame = draft?.game_id && games.some(g => g.game_id === draft.game_id) ? draft.game_id : game || games[0]?.game_id || "";
    const form = element("div", "tform");
    const title = element("h3");
    const gameSelect = element("select", "fc"); gameSelect.setAttribute("aria-label", "Game");
    for (const g of games) gameSelect.append(Object.assign(element("option", "", g.name), { value: g.game_id }));
    gameSelect.value = startGame;
    let entrants: { choice: string; name: string; sparring?: boolean }[] = [];
    const modeRadio = (value: string, checked: boolean) => { const r = element("input"); r.type = "radio"; r.name = "tmode"; r.value = value; r.checked = checked; return r; };
    const modeFixed = modeRadio("fixed", !draft?.mode || draft.mode === "fixed"), modePool = modeRadio("pool", draft?.mode === "pool"), modeKnockout = modeRadio("knockout", draft?.mode === "knockout");
    modeFixed.setAttribute("aria-label", "Fixed line-up"); modePool.setAttribute("aria-label", "Line-ups from a pool"); modeKnockout.setAttribute("aria-label", "Knockout");
    const mode = () => (modePool.checked ? "pool" : modeKnockout.checked ? "knockout" : "fixed");
    const keep = element("select", "fc"); keep.setAttribute("aria-label", "Candidates going on");
    for (const [v, t] of [["0.5", "half"], ["0.33", "one third"], ["0.66", "two thirds"]]) keep.append(Object.assign(element("option", "", t), { value: v }));
    const minLeft = element("select", "fc"); minLeft.setAttribute("aria-label", "Candidates left at the end");
    for (const v of ["1", "2", "3"]) minLeft.append(Object.assign(element("option", "", v), { value: v }));
    const metric = element("select", "fc"); metric.setAttribute("aria-label", "Elimination criterion");
    for (const [v, t] of [["wins", "wins (default)"], ["place", "average place"]]) metric.append(Object.assign(element("option", "", t), { value: v }));
    const plan = element("div", "bc-small");
    const knockoutBox = element("div", "bc-knockout-opts");
    const chips = element("div", "chips");
    const addSelect = element("select", "fc"); addSelect.setAttribute("aria-label", "Entrant");
    const add = button("+ Add", () => {
      const opt = addSelect.selectedOptions[0];
      if (opt) { entrants.push({ choice: opt.value, name: opt.textContent ?? opt.value }); draw(); }
    }, "gold");
    const players = element("select", "fc"); players.setAttribute("aria-label", "Players at each table");
    const gamesInput = element("input", "fc w-md"); gamesInput.type = "number"; gamesInput.min = "1"; gamesInput.max = "100000"; gamesInput.value = String(draft?.games ?? 200);
    gamesInput.setAttribute("aria-label", "Number of games");
    const counts = element("span", "seg");
    for (const c of GAME_COUNTS) { const b = button(String(c), () => { gamesInput.value = String(c); draw(); }); b.dataset.games = String(c); counts.append(b); }
    const seedRandom = element("input"); seedRandom.type = "radio"; seedRandom.name = "tseed"; seedRandom.checked = draft?.seed == null;
    const seedFixed = element("input"); seedFixed.type = "radio"; seedFixed.name = "tseed"; seedFixed.checked = draft?.seed != null;
    const seedValue = element("input", "fc w-sm"); seedValue.type = "number"; seedValue.value = String(draft?.seed ?? 42); seedValue.setAttribute("aria-label", "Fixed seed");
    const rounds = element("input", "fc w-sm"); rounds.type = "number"; rounds.min = "1"; rounds.value = "200"; rounds.setAttribute("aria-label", "Round limit");
    const noLimit = element("input"); noLimit.type = "checkbox";
    const windows = element("select", "fc"); windows.setAttribute("aria-label", "Offer windows");
    for (const [v, t] of [["off", "off"], ["payments", "on payments"], ["full", "full"]]) windows.append(Object.assign(element("option", "", t), { value: v }));
    windows.value = "full";
    const workers = element("input", "fc w-xs"); workers.type = "number"; workers.min = "1"; workers.max = "32";
    workers.value = String(Math.max(1, Math.min(8, (navigator.hardwareConcurrency || 4) - 2))); workers.setAttribute("aria-label", "Parallel processes");
    const nameInput = element("input", "fc w-lg"); nameInput.placeholder = "tournament-…"; nameInput.value = draft?.name ?? ""; nameInput.setAttribute("aria-label", "Name");
    const message = element("span", "err"); message.setAttribute("role", "alert");
    const estimate = element("span", "bc-small");
    const start = button("▶ Start the tournament", () => void submit(), "bc-primary");
    const rentcraftOnly = element("div");
    const q = (label: string, ...content: (HTMLElement | string)[]) => { const box = element("div", "q"); box.append(element("b", "", label), ...content); return box; };
    const radio = (...content: (HTMLElement | string)[]) => { const l = element("label", "radio"); l.append(...content); return l; };
    const hint = (text: string) => element("div", "bc-small", text);
    const addRow = element("div", "tadd"); addRow.append(addSelect, add);
    rentcraftOnly.append(radio("Round limit ", rounds, noLimit, " no limit (at the limit the richest player wins)"), radio("Offer windows ", windows));
    knockoutBox.append(radio("After each round ", keep, " of the candidates go on"), radio("The end, when ", minLeft, " candidate(s) are left - 2-3 show a close top"),
      radio("Elimination criterion ", metric), plan);
    form.append(
      q("1. Entrants", chips, addRow, hint("Bots and models. The same player can appear several times. With one player per table every entrant plays the same games alone. In a knockout mark the sparring partners (☆): they fill the other seats of every table.")),
      q("2. Table line-up", radio("Players at each table ", players),
        radio(modeFixed, " Fixed line-up - all entrants at every table, seats rotate (one player per table: a solo tournament)"),
        radio(modePool, " Line-ups from a pool - at every table the chosen number of players from the entrants, every combination in turn"),
        radio(modeKnockout, " Knockout - pick the best of many candidates: they play in rounds with sparring partners, the weaker drop out"),
        knockoutBox,
        hint("Fixed: the seats rotate over shared seeds. Pool: each combination of entrants plays in turn, so everybody meets everybody. Knockout: at every table sits one candidate and the sparring partners; after each round the weaker part drops out and the results add up.")),
      q("3. Number of games", (() => { const row = element("div", "seg-row"); row.append(counts, gamesInput); return row; })(), hint("More games = a narrower uncertainty interval. In a solo tournament this is the number of games of each entrant.")),
      q("4. Randomness and rules", radio(seedRandom, " random seed - every tournament plays different games"), radio(seedFixed, " fixed seed ", seedValue, " - the same dice rolls, a repeatable comparison"), rentcraftOnly),
      q("5. Execution", radio("Parallel processes ", workers, element("span", "bc-small", " (processor cores minus 1-2)")),
        hint("Every game is recorded: it counts towards the rankings and appears in the replays."), radio("Name (optional) ", nameInput)),
    );
    const actions = element("div", "btns end"); actions.append(message, estimate, start);
    main.replaceChildren(title, form, actions);
    async function loadGame() {
      const info = games.find(g => g.game_id === gameSelect.value);
      title.replaceChildren(info ? gameMark(info.game_id, info.name) : "", " New tournament ", element("span", "bc-small", "entrants, rules and execution"));
      title.prepend(gameSelect);
      players.replaceChildren(...Array.from({ length: (info?.max_players ?? 4) - (info?.min_players ?? 1) + 1 }, (_, i) => {
        const n = (info?.min_players ?? 1) + i;
        return Object.assign(element("option", "", n === 1 ? "1 (solo)" : String(n)), { value: String(n) });
      }));
      const bots = await catalog(gameSelect.value);
      addSelect.replaceChildren();
      const groups = new Map<string, BotDescriptor[]>();
      for (const d of bots) { const g = d.group ?? (d.kind === "model" ? "Models" : "Bots"); groups.set(g, [...(groups.get(g) ?? []), d]); }
      for (const [label, items] of groups) {
        const og = element("optgroup"); og.label = label;
        for (const d of items) og.append(Object.assign(element("option", "", d.display_name), { value: d.choice_id }));
        addSelect.append(og);
      }
      rentcraftOnly.hidden = gameSelect.value !== "rentcraft";
      entrants = draft && draft.game_id === gameSelect.value
        ? draft.entrants.map(e => (typeof e === "string" ? { choice: e, name: nameOf(gameSelect.value)(e) } : { choice: e.choice_id, name: e.display_name ?? nameOf(gameSelect.value)(e.choice_id) }))
        : bots.filter(d => d.kind === "builtin").slice(0, 2).map(d => ({ choice: d.choice_id, name: d.display_name }));
      if (draft?.sparring && draft.game_id === gameSelect.value) {
        const left = [...draft.sparring];
        for (const e of entrants) { const at = left.indexOf(e.choice); if (at >= 0) { e.sparring = true; left.splice(at, 1); } }
        for (const choice of left) entrants.push({ choice, name: nameOf(gameSelect.value)(choice), sparring: true });
      }
      players.value = String(draft?.players ?? (gameSelect.value === "dice_circuit" ? 1 : Math.min(info?.max_players ?? 4, Math.max(2, entrants.length))));
      draw();
    }
    function draw() {
      const m = mode();
      chips.replaceChildren(...(entrants.length ? entrants.map((e, i) => {
        const chip = element("span", `chip${m === "knockout" && e.sparring ? " sparring" : ""}`);
        chip.append(e.name);
        if (m === "knockout") {
          const role = button(e.sparring ? "☆" : "★", () => { e.sparring = !e.sparring; draw(); }, "x");
          role.title = e.sparring ? "Sparring partner (click: candidate)" : "Candidate (click: sparring partner)";
          role.setAttribute("aria-label", `${e.name}: ${e.sparring ? "sparring partner" : "candidate"}`);
          chip.append(role);
        }
        const x = button("✕", () => { entrants.splice(i, 1); draw(); }, "x"); x.title = "Remove"; x.setAttribute("aria-label", `Remove ${e.name}`);
        chip.append(x);
        return chip;
      }) : [element("span", "bc-small", "Add at least one entrant.")]));
      counts.querySelectorAll("button").forEach(b => b.classList.toggle("on", b.dataset.games === gamesInput.value));
      knockoutBox.hidden = m !== "knockout";
      const n = Number(players.value), total = Math.max(1, Number(gamesInput.value) || 1);
      const solo = n === 1 && m === "fixed";
      let matches = solo ? total * entrants.length : Math.ceil(total / Math.max(1, n)) * n;
      message.textContent = problem();
      if (m === "knockout") {
        const c = entrants.filter(e => !e.sparring).length, min = Number(minLeft.value);
        if (c >= 2 && min < c) {
          const rounds = halvingPlan(c, total, Number(keep.value), min);
          const per = Math.floor(rounds[0].games / rounds[0].candidates);
          matches = rounds.reduce((a, r) => a + r.candidates * Math.ceil(Math.max(n, Math.floor(r.games / r.candidates)) / n) * n, 0);
          plan.textContent = `${rounds.length === 1 ? "1 round" : `${rounds.length} rounds`}: ${[...rounds.map(r => r.candidates), rounds[rounds.length - 1].survivors].join(" → ")} candidates. In round 1 ${per} games per candidate, in the last ${Math.floor(rounds[rounds.length - 1].games / rounds[rounds.length - 1].candidates)}.`;
        } else plan.textContent = "";
      }
      const seconds = matches / (Math.max(1, Number(workers.value) || 1) * 3);
      estimate.textContent = `${solo ? `${entrants.length} × ${total} solo games` : `${matches} games`} · estimated time: ${seconds < 90 ? `about ${Math.max(1, Math.round(seconds))} s` : `about ${Math.round(seconds / 60)} min`}`;
    }
    /** Why the form cannot start yet (empty when it can). */
    function problem() {
      const m = mode(), n = Number(players.value);
      if (!entrants.length) return "Add at least one entrant.";
      if (m === "fixed") return n > 1 && entrants.length !== n ? `A line-up of ${n} needs exactly ${n} entrants (now ${entrants.length}).` : "";
      if (n < 2) return "Choose at least 2 players at each table.";
      if (m === "pool") return entrants.length <= n ? `A pool needs more entrants than players at a table (now ${entrants.length} for ${n}).` : "";
      const sparring = entrants.filter(e => e.sparring).length, candidates = entrants.length - sparring;
      if (sparring !== n - 1) return `Mark ${n - 1} sparring partner${n === 2 ? "" : "s"} (☆): every table seats 1 candidate and ${n - 1} sparring partner${n === 2 ? "" : "s"} (now ${sparring}).`;
      if (candidates < 2) return "Mark at least 2 candidates.";
      if (Number(minLeft.value) >= candidates) return `With ${candidates} candidates the tournament can end at ${candidates - 1} at most.`;
      const first = halvingPlan(candidates, Math.max(1, Number(gamesInput.value) || 1), Number(keep.value), Number(minLeft.value))[0];
      if (Math.floor(first.games / first.candidates) < n) return "Too few games: every candidate needs at least one full rotation in the first round.";
      return "";
    }
    async function submit() {
      const info = games.find(g => g.game_id === gameSelect.value)!;
      const n = Number(players.value), total = Math.max(1, Math.round(Number(gamesInput.value) || 1));
      const m = mode(), why = problem();
      if (why) { message.textContent = why; return; }
      const settings: Record<string, unknown> = {};
      if (info.game_id === "rentcraft") { settings.max_rounds = noLimit.checked ? null : Math.max(1, Number(rounds.value) || 200); settings.trade_windows = windows.value; }
      const base = nameInput.value.trim() || timestampName("tournament");
      const fixed = seedFixed.checked ? Number(seedValue.value) || 0 : null;
      const common = { config: { game_id: info.game_id, variant: info.variants?.[0] ?? "base", players: n, ...(Object.keys(settings).length ? { settings } : {}) },
        rules_version: info.rules_version ?? "1", engine_version: info.engine_version ?? "1", max_inflight: Math.max(1, Number(workers.value) || 1),
        max_record_bytes: 50_000_000, max_decisions: 200_000 };
      start.disabled = true;
      message.textContent = "";
      try {
        if (m === "knockout") {
          const made = await options.request<{ knockout_id: string; creator_credential: string; revision: number }>("/api/knockouts", {
            name: base.slice(0, 128), config: common.config, candidates: entrants.filter(e => !e.sparring).map(e => e.choice), sparring: entrants.filter(e => e.sparring).map(e => e.choice),
            games: total, keep: Number(keep.value), min_candidates: Number(minLeft.value), metric: metric.value, ...(fixed != null ? { seed: fixed } : {}),
            max_inflight: Math.min(64, common.max_inflight), start: true });
          // One request: the server saves the knockout and starts it.
          remember(KO + made.knockout_id, made.creator_credential);
          selected = KO + made.knockout_id;
          options.setAddress(selected, game);
          await refresh();
          void renderDetails();
          return;
        }
        const specs = m === "pool"
          ? (() => { const groups = Math.ceil(total / n), seeds = seedList(groups, fixed);
              return [{ ...common, name: base.slice(0, 128), seats: entrants.map(e => e.choice), seeds, lineups: poolLineups(entrants.length, n, groups, seeds[0]),
                sampler_seeds: seeds.map((_, g) => Array.from({ length: n }, (_, k) => g * n + k)), rotations: Array.from({ length: n }, (_, r) => r), match_count: groups * n, schedule_cap: groups * n }]; })()
          : n === 1
          ? (() => { const seeds = seedList(total, fixed); return entrants.map(e => ({ ...common, name: `${SOLO_PREFIX}${base} · ${e.name}`.slice(0, 128), seats: [e.choice], seeds,
              sampler_seeds: seeds.map((_, i) => [i]), rotations: [0], match_count: total, schedule_cap: total })); })()
          : (() => { const groups = Math.ceil(total / n), seeds = seedList(groups, fixed); return [{ ...common, name: base.slice(0, 128), seats: entrants.map(e => e.choice), seeds,
              sampler_seeds: seeds.map((_, g) => entrants.map((_, k) => g * n + k)), rotations: Array.from({ length: n }, (_, r) => r), match_count: groups * n, schedule_cap: groups * n }]; })();
        let first = "";
        const startErrors: string[] = [];
        for (const spec of specs) {
          // One request per tournament: the server saves it and starts it (start: true); a crash in between is
          // repaired by the server at its next start.
          const made = await options.request<{ tournament_id: string; creator_credential: string; start_error?: string }>("/api/tournaments", { ...spec, start: true });
          remember(made.tournament_id, made.creator_credential);
          first ||= made.tournament_id;
          if (made.start_error) startErrors.push(made.start_error);
        }
        selected = n === 1 ? `solo:${base}:${info.game_id}:${specs[0].seeds[0]}:${specs[0].seeds.length}` : first;
        options.setAddress(selected, game);
        await refresh();
        await renderDetails();
        if (startErrors.length) main.prepend(element("div", "err", `Saved but not started: ${startErrors[0]}`));
      } catch (error) {
        message.textContent = (error as Error).message;
      } finally { start.disabled = false; }
    }
    gameSelect.onchange = () => void loadGame();
    players.onchange = gamesInput.oninput = workers.oninput = keep.onchange = minLeft.onchange = () => draw();
    for (const r of [modeFixed, modePool, modeKnockout]) r.onchange = () => {
      // A knockout starts with the last entrants as sparring partners (one fewer than the table).
      if (mode() === "knockout" && !entrants.some(e => e.sparring)) entrants.slice(-(Math.max(2, Number(players.value)) - 1)).forEach(e => (e.sparring = true));
      draw();
    };
    void loadGame();
  }

  // ---------- details ----------
  async function renderDetails(live = false) {
    if (selected?.startsWith(KO)) return renderKnockout(selected.slice(KO.length));
    const entry = entries().find(e => e.key === selected) ?? null;
    let members = entry?.members ?? [];
    if (!entry && selected && !selected.startsWith("solo:")) {
      const one = await options.request<Tournament>(`/api/tournaments/${encodeURIComponent(selected)}?matches=false`).catch(() => null);
      if (one) members = [one];
    }
    if (!members.length) { if (!live) main.replaceChildren(element("p", "bc-small", "This tournament could not be found.")); return; }
    if (live && main.querySelector(".bc-solo-replay .bc-embedded-stage")) return;
    // A solo group needs every game's score: each member is read on its own (stored scores, no match rows).
    if (members[0].spec.config.players === 1 && members.some(t => !t.scores && !t.matches)) {
      members = await Promise.all(members.map(t => options.request<Tournament>(`/api/tournaments/${encodeURIComponent(t.tournament_id)}?matches=false`).catch(() => t)));
      if (live && main.querySelector(".bc-solo-replay .bc-embedded-stage")) return;
    }
    const first = members[0], gameId = first.spec.config.game_id;
    await catalog(gameId);
    const solo = first.spec.config.players === 1;
    const status = solo ? (members.some(t => busy(t.status)) ? "running" : members.every(t => t.status === "completed") ? "completed" : first.status) : first.status;
    const done = members.reduce((a, t) => a + doneOf(t), 0);
    const total = members.reduce((a, t) => a + t.spec.match_count, 0);
    const title = element("h3");
    title.append(gameMark(gameId, gameName(gameId)), ` ${solo ? first.spec.name.slice(SOLO_PREFIX.length).split(" · ")[0] + " (solo)" : first.spec.name} `,
      element("span", `tag${status === "running" ? " running" : ""}`, STATE[status] ?? status), element("span", "sp"));
    const ownedIds = members.filter(t => manageable(t, t.tournament_id));
    const pending = ownedIds.filter(t => t.status === "pending");
    if (pending.length) title.append(button("▶ Start", () => void act("start", pending), "bc-primary"));
    if (busy(status) && ownedIds.length) title.append(button("⏹ Stop", () => void act("stop", ownedIds)));
    title.append(button("⧉ Create similar", () => {
      pendingDraft = { game_id: gameId, players: first.spec.config.players, games: solo ? seedCount(first) : first.spec.match_count,
        entrants: solo ? members.map(t => choicesOf(t)[0]) : choicesOf(first), name: "", mode: isPool(first) ? "pool" : "fixed" };
      selected = null; options.setAddress(null, game); renderNew();
    }));
    if (!busy(status) && ownedIds.length) title.append(button("🗑 Delete", () => { if (confirm(`Delete “${title.textContent}”? Its games stay in the replays.`)) void act("delete", ownedIds); }));
    const kpis = element("div", "kpis");
    const kpi = (label: string, value: string, note = "") => { const k = element("div", "kpi"); k.append(element("span", "", label), element("b", "", value), element("small", "", note)); kpis.append(k); };
    kpi("Progress", `${done} / ${total}`);
    const pool = isPool(first);
    kpi("Line-up", solo ? `solo · ${members.length} entrants` : pool ? `pool · ${first.spec.config.players} per table` : `fixed · ${first.spec.config.players} per table`,
      solo ? "same seeds for everybody" : pool ? `${choicesOf(first).length} entrants, every combination in turn` : "seats rotate");
    kpi("Games", solo ? `${seedCount(first)} each` : String(first.spec.match_count), `${seedCount(first)} seed${seedCount(first) === 1 ? "" : "s"}`);
    const bar = element("div", "bar big"), fill = element("i"); fill.style.width = `${total ? (done / total) * 100 : 0}%`; bar.append(fill);
    const body = element("div");
    if (solo) body.append(soloTable(members), soloHistogram(members));
    else body.append(lineupTable(first));
    body.append(element("div", "bc-small", "The games are recorded: they count towards the Rankings and appear in the Replays."));
    const viewer = element("div", "bc-solo-replay");
    main.replaceChildren(title, kpis, bar, body, viewer);
    async function act(action: "start" | "stop" | "delete", targets: Tournament[]) {
      let failure = "";
      for (const t of targets) {
        const now = await options.request<Tournament>(`/api/tournaments/${t.tournament_id}?matches=false`).catch(() => t);
        const body = action === "start" ? { spec_sha: now.spec_sha, revision: now.revision } : { revision: now.revision };
        await options.request(`/api/tournaments/${t.tournament_id}/${action}`, body, credentialOf(t.tournament_id)).catch(error => { failure ||= (error as Error).message; });
      }
      if (action === "delete" && !failure) { selected = null; options.setAddress(null, game); }
      await refresh();
      if (action === "delete" && !failure) renderNew(); else await renderDetails();
      // The server's answer is shown (for example a bot that is no longer available), never swallowed.
      if (failure) main.prepend(element("div", "err", `${action === "start" ? "Not started" : action === "stop" ? "Not stopped" : "Not deleted"}: ${failure}`));
    }
    function soloTable(group: Tournament[]) {
      const rows = soloStats(group.map(t => soloEntry(t)));
      const table = element("table", "rank-table tres");
      table.innerHTML = `<thead><tr><th>#</th><th>Player</th><th>Mean score</th><th>± 95%</th><th>Std</th><th>Games</th><th>Paired vs best</th><th>Best</th><th>Worst</th></tr></thead>`;
      const tbody = element("tbody");
      rows.forEach((r, i) => {
        const tr = element("tr");
        tr.innerHTML = `<td>${i + 1}</td><td><b>${esc(r.name)}</b></td><td class="num"><b>${r.mean.toFixed(1)}</b></td><td class="num">${r.ci.toFixed(1)}</td><td class="num">${r.std.toFixed(1)}</td><td class="num">${r.games}/${r.total}</td>
          <td class="num">${r.diff == null ? (i === 0 ? "best" : "-") : `${r.diff >= 0 ? "+" : ""}${r.diff.toFixed(1)} ± ${(r.diffCi ?? 0).toFixed(1)}`}</td>`;
        for (const which of [r.best, r.worst]) {
          const td = element("td");
          const member = group.find(t => t.tournament_id === r.tournamentId);
          const match = which == null ? null : member?.matches?.find(m => m.ordinal === which);
          if (which != null && (match || member?.scores?.[which] != null)) {
            // Stored scores carry no match IDs: the one game is looked up when its replay is opened.
            const matchId = async () => match?.match_id ?? (await options.request<{ items: Match[] }>(`/api/tournaments/${encodeURIComponent(r.tournamentId)}/matches?offset=${which}&limit=1`)).items[0]?.match_id;
            const b = button(`${r.scores[which]} ▶`, () => void matchId().then(id => { if (id) void showReplay(r.tournamentId, id, gameId, viewer); }));
            b.setAttribute("aria-label", `Replay of ${r.name}, game ${which + 1}, ${r.scores[which]} points`);
            td.append(b);
          }
          tr.append(td);
        }
        tbody.append(tr);
      });
      table.append(tbody);
      return table;
    }
    function soloHistogram(group: Tournament[]) {
      const rows = soloStats(group.map(t => soloEntry(t)));
      const values = rows.flatMap(r => r.scores.filter((s): s is number => s != null));
      const box = element("div", "bc-solo-hist");
      if (!values.length) return box;
      const { edges } = histogram(values);
      const width = edges.length > 1 ? edges[1] - edges[0] : 1;
      const series = rows.map(r => { const c = edges.map(() => 0); for (const s of r.scores) if (s != null) c[Math.min(edges.length - 1, Math.floor((s - edges[0]) / width))]++; return c; });
      const peak = Math.max(1, ...series.flat()), W = 520, H = 140, gw = W / edges.length, bw = Math.max(2, (gw - 6) / rows.length);
      let svg = `<svg viewBox="0 0 ${W} ${H + 18}" width="100%" role="img" aria-label="Score distribution">`;
      series.forEach((c, k) => c.forEach((v, i) => { const h = (v / peak) * H; svg += `<rect class="tone-seat-${k % 4}" x="${(i * gw + 3 + k * bw).toFixed(1)}" y="${(H - h).toFixed(1)}" width="${bw.toFixed(1)}" height="${h.toFixed(1)}"><title>${esc(rows[k].name)}: ${v} games ${edges[i]}-${edges[i] + width - 1}</title></rect>`; }));
      edges.forEach((e, i) => { if (i % 2 === 0) svg += `<text class="ch-tick" x="${(i * gw + 3).toFixed(1)}" y="${H + 14}">${e}</text>`; });
      box.innerHTML = `<b>Score distribution</b>${svg}</svg>`;
      const legend = element("div", "legend");
      rows.forEach((r, k) => { const item = element("span"); item.append(element("i", `tone-seat-${k % 4}`), r.name); legend.append(item); });
      box.append(legend);
      return box;
    }
    function lineupTable(t: Tournament) {
      const rows = lineupResults(t, nameOf(gameId)).sort((a, b) => b.win_rate - a.win_rate);
      const base = 1 / Math.max(1, t.spec.config.players);
      const wrap = element("div");
      if (!rows.some(r => r.games)) { wrap.append(element("div", "bc-small", "The first results will appear in a moment…")); return wrap; }
      const table = element("table", "rank-table tres");
      table.innerHTML = `<thead><tr><th>Player</th><th>Games</th><th>Wins</th><th class="w-wide">Win rate (95% interval)</th><th></th><th>Avg. place</th><th>Mean score</th></tr></thead><tbody>${rows.map(r => {
        const [lo, hi] = wilson(r.wins, r.games), sig = lo > base ? "up" : hi < base ? "down" : "";
        return `<tr><td><b>${esc(r.name)}</b></td><td class="num">${r.games}</td><td class="num">${r.wins}</td>
          <td><div class="wr"><div class="wr-ci" style="left:${lo * 100}%;width:${(hi - lo) * 100}%"></div><div class="wr-dot ${sig}" style="left:${r.win_rate * 100}%"></div><div class="wr-base" style="left:${base * 100}%"></div></div></td>
          <td class="num"><b>${pct(r.win_rate)}</b> <span class="muted">(${pct(lo)}-${pct(hi)})</span></td><td class="num">${r.avg_place.toFixed(2)}</td><td class="num">${r.mean.toFixed(0)}</td></tr>`;
      }).join("")}</tbody>`;
      wrap.append(table, element("div", "bc-small", `The vertical line = the result of chance (${pct(base)}). The coloured dot: a result clearly above (green) or below (red) chance. The grey bar = the interval in which the true win rate lies with 95% confidence - the more games, the narrower.`));
      return wrap;
    }
  }

  /** A knockout: the rounds, who went on and who dropped out, with results; the winner highlighted. */
  async function renderKnockout(id: string) {
    const k = knockouts.find(x => x.knockout_id === id) ?? await options.request<Knockout>(`/api/knockouts/${encodeURIComponent(id)}`).catch(() => null);
    if (!k) { main.replaceChildren(element("p", "bc-small", "This tournament could not be found.")); return; }
    const gameId = k.spec.config.game_id, credential = credentialOf(KO + id), mayManage = manageable(k, KO + id);
    const title = element("h3");
    title.append(gameMark(gameId, gameName(gameId)), ` ${k.spec.name} `, element("span", `tag${k.status === "running" ? " running" : ""}`, STATE[k.status] ?? k.status), element("span", "sp"));
    const act = async (action: "start" | "stop" | "delete") => {
      const now = await options.request<Knockout>(`/api/knockouts/${id}`).catch(() => k);
      await options.request(`/api/knockouts/${id}/${action}`, { revision: now.revision }, credential).catch(() => undefined);
      if (action === "delete") { selected = null; options.setAddress(null, game); }
      await refresh();
      if (action === "delete") renderNew(); else void renderDetails();
    };
    if (k.status === "pending" && mayManage) title.append(button("▶ Start", () => void act("start"), "bc-primary"));
    if (busy(k.status) && mayManage) title.append(button("⏹ Stop", () => void act("stop")));
    title.append(button("⧉ Create similar", () => {
      pendingDraft = { game_id: gameId, players: k.spec.config.players, games: k.spec.games, mode: "knockout", name: "",
        entrants: [...k.spec.candidates.map(choiceOf), ...k.spec.sparring.map(choiceOf)], sparring: k.spec.sparring.map(choiceOf) };
      selected = null; options.setAddress(null, game); renderNew();
    }));
    if (!busy(k.status) && mayManage) title.append(button("🗑 Delete", () => { if (confirm(`Delete “${k.spec.name}”? Its games stay in the replays.`)) void act("delete"); }));
    const kpis = element("div", "kpis");
    const kpi = (label: string, value: string, note = "") => { const box = element("div", "kpi"); box.append(element("span", "", label), element("b", "", value), element("small", "", note)); kpis.append(box); };
    kpi("Progress", `${k.games_done} / ${k.games_total}`);
    kpi("Line-up", `knockout · ${k.spec.config.players} per table`, "1 candidate and sparring partners");
    kpi("Round", `${Math.max(1, k.rounds.length)} of ${k.plan.length}`, `${k.labels.candidates.length} candidates`);
    kpi("Seed", String(k.spec.seed), k.spec.random_seed ? "random" : "fixed");
    const bar = element("div", "bar big"), fill = element("i"); fill.style.width = `${k.games_total ? Math.min(100, (k.games_done / k.games_total) * 100) : 0}%`; bar.append(fill);
    const body = element("div", "halving");
    const name = (c: number) => esc(k.labels.candidates[c] ?? `Candidate ${c + 1}`);
    if (k.winner != null) body.insertAdjacentHTML("beforeend", `<div class="winner">🏆 Winner: <b>${name(k.winner)}</b>${k.top.length > 1 ? ` · top: ${k.top.map(name).join(", ")}` : ""}</div>`);
    if (k.error) body.append(element("div", "err", k.error));
    body.append(element("div", "bc-small", `Elimination criterion: ${k.spec.metric === "wins" ? "wins" : "average place"}. Results of consecutive rounds add up.`));
    const byWins = k.spec.metric === "wins";
    for (const r of k.rounds) {
      const planned = k.plan.find(p => p.round === r.round);
      const last = r.round === k.plan.length;
      const rows = r.table.map(t => {
        const [lo, hi] = wilson(t.wins, t.games);
        const tag = !r.finished ? "" : last && t.candidate === k.winner ? `<span class="tag waiting">winner</span>`
          : r.survivors.includes(t.candidate) ? `<span class="tag running">${last ? "top" : "goes on"}</span>` : `<span class="tag out">out</span>`;
        return `<tr><td><b>${esc(t.label)}</b> ${tag}</td><td class="num">${t.games}</td><td class="num">${byWins ? t.avg_place.toFixed(2) : `<b>${t.avg_place.toFixed(2)}</b>`}</td>
          <td class="num">${byWins ? `<b>${pct(t.win_rate)}</b>` : pct(t.win_rate)} <span class="muted">(${pct(lo)}-${pct(hi)})</span></td></tr>`;
      }).join("");
      body.insertAdjacentHTML("beforeend", `<div class="row"><b>Round ${r.round} of ${k.plan.length} · ${r.candidates} candidates · ${r.games}${planned ? ` / ${r.planned_games}` : ""} games${r.finished ? "" : " · in progress"}</b></div>
        <table class="rank-table tres"><thead><tr><th>Candidate (including previous rounds)</th><th>Games</th><th>Avg. place</th><th>Wins (95% interval)</th></tr></thead><tbody>${rows}</tbody></table>`);
    }
    if (!k.rounds.length) body.append(element("div", "bc-small", "The first round starts in a moment…"));
    body.append(element("div", "bc-small", `Sparring partners: ${k.labels.sparring.join(", ")}. The games are recorded: they appear in the Replays.`));
    main.replaceChildren(title, kpis, bar, body);
  }

  let closeViewer: (() => void) | null = null;
  async function showReplay(tournamentId: string, matchId: string, gameId: string, host: HTMLElement) {
    closeViewer?.();
    const gamePath = encodeURIComponent(gameId);
    const [boardSpec, rulesText, settings, widget] = await Promise.all([
      options.request(`/api/games/${gamePath}/board`), options.request<string>(`/api/games/${gamePath}/rules`), options.request(`/api/games/${gamePath}/settings`), import("./game-view"),
    ]);
    const stage = element("section", "bc-embedded-stage bc-stage tc-screen"), holder = element("div", "bc-embedded-board");
    stage.append(holder);
    host.replaceChildren(stage);
    const game = widget.mountGameView(holder, { gameId, boardSpec, rulesText, settings, readOnly: true, fullscreenRoot: stage, onExit: () => closeViewer?.() });
    let epoch = 0;
    const show = async (position: number) => {
      const mine = ++epoch;
      const frame = await options.request<any>(`/api/tournaments/${tournamentId}/matches/${matchId}/replay?position=${position}`);
      if (mine !== epoch) return;
      game.update({ table_id: tournamentId, status: "replay", config: { game_id: gameId, variant: "base", players: frame.observation?.data?.sheets?.length ?? 1 },
        observation: frame.observation, replay_position: frame.position } as any, frame.events, { decision_id: null, actions: [] });
      bar.shown(frame.position, frame.total_positions, "");
      (stage.querySelector(".bc-bottom") ?? stage).prepend(bar.element);
    };
    const bar = mountReplayBar({ load: show, close: () => closeViewer?.() });
    closeViewer = () => { bar.dispose(); game.dispose(); host.replaceChildren(); closeViewer = null; };
    try { await show(0); } catch { host.replaceChildren(element("p", "bc-small", "The replay could not be loaded.")); }
    stage.scrollIntoView?.({ block: "start" });
  }

  void options.games().then(items => {
    if (life.signal.aborted) return;
    games = items;
    for (const g of [{ game_id: "", name: "All games" }, ...items]) {
      const b = button("", () => {
        picker.querySelectorAll("button").forEach(x => x.setAttribute("aria-pressed", String(x === b)));
        game = g.game_id; page = 0; options.setAddress(selected, game); renderList();
      }, "gp");
      if (g.game_id) b.append(gameIcon(g.game_id, 16));
      b.append(element("span", "", g.name));
      b.setAttribute("aria-pressed", String(g.game_id === game));
      picker.append(b);
    }
    if (pendingDraft || !selected) renderNew();
    void refresh().then(() => { if (selected && !pendingDraft) void renderDetails(); });
  });
  return {
    /** Opens the new-tournament form filled from a draft. */
    open(draft: TournamentDraft) { pendingDraft = draft; selected = null; options.setAddress(null, game); if (games.length) renderNew(); },
    dispose() { life.abort(); clearTimeout(timer); closeViewer?.(); grid.remove(); },
  };
}

export { describe };
