// Solo tournament: several contestants (bots, bundled or host-registered models) each play the SAME N chance seeds
// alone. It is a group of single-seat tournaments that share one seed list (the server runs them in the background with
// its tournament executor); the page joins their match results into one leaderboard by mean score.
import { button, element } from "./dom";
import { gameIcon } from "./ui/gameicon";
import { mountReplayBar } from "./ui/replaybar";
import type { BotDescriptor } from "./play-types";

export const SOLO_PREFIX = "Solo: ";
const SEP = " · ";
const MAX_GAMES = 5000;

export interface SoloOptions {
  request: <T = any>(path: string, body?: unknown, credential?: string) => Promise<T>;
  games: () => Promise<{ game_id: string; name: string; min_players: number; rules_version?: string; engine_version?: string; variants?: string[] }[]>;
}
interface Tournament { tournament_id: string; status: string; revision: number; spec_sha: string; spec: { name: string; seeds: number[]; config: { game_id: string; players: number } }; matches?: Match[] }
interface Match { match_id: string; ordinal: number; status: string; result?: { scores: number[] } }

export interface ContestantStats { name: string; tournamentId: string; status: string; games: number; total: number; mean: number; std: number; ci: number;
  diff: number | null; diffCi: number | null; scores: (number | null)[]; best: number | null; worst: number | null }

/** Mean, standard deviation and the 95% interval half-width of a list of scores. */
export function describe(scores: number[]) {
  const n = scores.length;
  const mean = n ? scores.reduce((a, b) => a + b, 0) / n : 0;
  const std = n > 1 ? Math.sqrt(scores.reduce((a, b) => a + (b - mean) ** 2, 0) / (n - 1)) : 0;
  return { n, mean, std, ci: n > 1 ? 1.96 * std / Math.sqrt(n) : 0 };
}

/** The leaderboard of one solo group: by mean score, with the paired difference to the best on the seeds both played. */
export function soloStats(entries: { name: string; tournamentId: string; status: string; total: number; scores: (number | null)[] }[]): ContestantStats[] {
  const rows = entries.map(e => {
    const played = e.scores.filter((s): s is number => s != null);
    const d = describe(played);
    let best: number | null = null, worst: number | null = null;
    e.scores.forEach((s, i) => {
      if (s == null) return;
      if (best == null || s > e.scores[best]!) best = i;
      if (worst == null || s < e.scores[worst]!) worst = i;
    });
    return { ...e, games: d.n, mean: d.mean, std: d.std, ci: d.ci, diff: null as number | null, diffCi: null as number | null, best, worst };
  }).sort((a, b) => b.mean - a.mean);
  const top = rows[0];
  for (const row of rows.slice(1)) {
    const pairs = row.scores.flatMap((s, i) => (s != null && top.scores[i] != null ? [s - top.scores[i]!] : []));
    if (pairs.length) { const d = describe(pairs); row.diff = d.mean; row.diffCi = d.ci; }
  }
  return rows;
}

/** Score histogram bins shared by every contestant of the group. */
export function histogram(values: number[], bins = 12) {
  if (!values.length) return { edges: [] as number[], counts: [] as number[] };
  const lo = Math.min(...values), hi = Math.max(...values);
  const width = Math.max(1, Math.ceil((hi - lo + 1) / bins));
  const counts = Array.from({ length: Math.ceil((hi - lo + 1) / width) }, () => 0);
  for (const v of values) counts[Math.floor((v - lo) / width)]++;
  return { edges: counts.map((_, i) => lo + i * width), counts, width };
}

const credentialKey = "boardcraft.solo.credentials";
function saveCredential(id: string, credential: string) {
  try { const all = JSON.parse(localStorage.getItem(credentialKey) ?? "{}"); all[id] = credential; localStorage.setItem(credentialKey, JSON.stringify(all)); } catch { /* only this visit */ }
}
function credentialOf(id: string): string | undefined {
  try { return JSON.parse(localStorage.getItem(credentialKey) ?? "{}")[id]; } catch { return undefined; }
}

export function mountSoloTournaments(root: HTMLElement, options: SoloOptions) {
  const life = new AbortController();
  const section = element("section", "bc-panel bc-solo");
  section.append(element("h2", "", "Solo tournaments "), element("p", "bc-small",
    "Each contestant plays the same games alone (the same dice for everyone), so the mean scores compare fairly. Models always play their best move."));
  const form = element("form", "bc-solo-form");
  const results = element("div", "bc-solo-results");
  const viewer = element("div", "bc-solo-replay");
  section.append(form, results, viewer);
  root.append(section);
  const status = element("p", "bc-small");
  status.setAttribute("role", "status");
  let timer: ReturnType<typeof setTimeout> | undefined;

  void options.games().then(games => {
    const solo = games.filter(g => g.min_players <= 1);
    if (!solo.length) { form.append(element("p", "bc-small", "No game on this server can be played alone.")); return; }
    const name = element("input", "fc"); name.required = true; name.maxLength = 80; name.value = "Solo check"; name.setAttribute("aria-label", "Name");
    const game = element("select", "fc"); game.setAttribute("aria-label", "Game");
    for (const g of solo) game.append(Object.assign(element("option", "", g.name), { value: g.game_id }));
    const count = element("input", "fc"); count.type = "number"; count.min = "1"; count.max = String(MAX_GAMES); count.value = "200"; count.setAttribute("aria-label", "Games per contestant");
    const players = element("fieldset", "bc-solo-players");
    players.append(element("legend", "", "Contestants"));
    const submit = element("button", "bc-primary", "Create and run"); submit.type = "submit";
    const field = (label: string, control: HTMLElement) => { const box = element("label", "ff"); box.append(element("span", "fl", label), control); return box; };
    const row = element("div", "fbar");
    row.append(field("Name", name), field("Game", game), field("Games per contestant", count));
    form.append(row, players, submit, status);
    const loadBots = async () => {
      players.replaceChildren(element("legend", "", "Contestants"), element("p", "bc-small", "Loading bots and models…"));
      const bots = await options.request<BotDescriptor[]>(`/api/games/${encodeURIComponent(game.value)}/bots`).catch(() => []);
      players.replaceChildren(element("legend", "", "Contestants"));
      for (const b of bots) {
        const label = element("label", "fcheck");
        const box = element("input"); box.type = "checkbox"; box.value = b.choice_id; box.dataset.name = b.display_name;
        box.checked = players.querySelectorAll("input:checked").length < 3;
        label.append(box, ` ${b.display_name}${b.kind === "model" ? " (model)" : ""}`);
        players.append(label);
      }
    };
    game.onchange = () => void loadBots();
    void loadBots();
    form.onsubmit = async e => {
      e.preventDefault();
      const chosen = [...players.querySelectorAll<HTMLInputElement>("input:checked")];
      const n = Math.round(Number(count.value));
      if (!chosen.length) { status.textContent = "Choose at least one contestant."; return; }
      if (!(n >= 1 && n <= MAX_GAMES)) { status.textContent = `Choose between 1 and ${MAX_GAMES} games.`; return; }
      const info = solo.find(g => g.game_id === game.value)!;
      // One shared list of distinct seeds: every contestant meets exactly the same chance.
      const seeds = new Set<number>();
      const random = new Uint32Array(1);
      while (seeds.size < n) { crypto.getRandomValues(random); seeds.add(random[0]); }
      const list = [...seeds];
      submit.disabled = true;
      status.textContent = "Creating…";
      try {
        for (const box of chosen) {
          const spec = {
            name: `${SOLO_PREFIX}${name.value.trim()}${SEP}${box.dataset.name}`.slice(0, 128),
            config: { game_id: info.game_id, variant: info.variants?.[0] ?? "base", players: 1 },
            rules_version: info.rules_version ?? "1", engine_version: info.engine_version ?? "1",
            seats: [box.value], seeds: list, sampler_seeds: list.map((_, i) => [i]), rotations: [0], match_count: n,
            schedule_cap: n, max_inflight: 4, max_record_bytes: 50_000_000, max_decisions: 200_000,
          };
          // One request: the server saves the tournament and starts it (start: true).
          const made = await options.request<{ tournament_id: string; creator_credential: string; start_error?: string }>("/api/tournaments", { ...spec, start: true });
          saveCredential(made.tournament_id, made.creator_credential);
          if (made.start_error) throw new Error(made.start_error);
        }
        status.textContent = "Running in the background. Results fill in below.";
        void refresh();
      } catch (error) {
        status.textContent = `The solo tournament could not be created: ${(error as Error).message}`;
      } finally { submit.disabled = false; }
    };
  });

  async function refresh() {
    clearTimeout(timer);
    let list: Tournament[];
    try { list = await options.request<Tournament[]>("/api/tournaments"); } catch { return; }
    if (life.signal.aborted) return;
    const groups = new Map<string, Tournament[]>();
    for (const t of list) {
      if (!t.spec?.name?.startsWith(SOLO_PREFIX) || t.spec.config?.players !== 1 || t.status === "deleted") continue;
      const base = t.spec.name.slice(SOLO_PREFIX.length).split(SEP)[0];
      const key = `${base}|${t.spec.config.game_id}|${t.spec.seeds.length}|${t.spec.seeds[0]}`;
      groups.set(key, [...(groups.get(key) ?? []), t]);
    }
    const cards: HTMLElement[] = [];
    let running = false;
    for (const [key, members] of groups) {
      const details = await Promise.all(members.map(m => options.request<Tournament>(`/api/tournaments/${m.tournament_id}`).catch(() => m)));
      if (life.signal.aborted) return;
      running ||= details.some(d => !["completed", "stopped", "failed", "deleted"].includes(d.status));
      cards.push(groupCard(key.split("|")[0], details));
    }
    results.replaceChildren(...(cards.length ? cards : [element("p", "bc-small", "No solo tournament yet.")]));
    if (running) timer = setTimeout(() => void refresh(), 3000);
  }

  function groupCard(base: string, members: Tournament[]) {
    const card = element("article", "bc-solo-group");
    const gameId = members[0].spec.config.game_id;
    const total = members[0].spec.seeds.length;
    const entries = members.map(t => {
      const scores: (number | null)[] = Array.from({ length: total }, () => null);
      for (const m of t.matches ?? []) if (m.status === "completed" && m.result) scores[m.ordinal] = m.result.scores[0];
      return { name: t.spec.name.split(SEP).slice(1).join(SEP) || t.spec.name, tournamentId: t.tournament_id, status: t.status, total, scores };
    });
    const rows = soloStats(entries);
    const done = rows.reduce((a, r) => a + r.games, 0), all = total * rows.length;
    const headLine = element("h3");
    headLine.append(gameIcon(gameId, 18), ` ${base} `, element("span", "bc-small", `${total} games each · ${done} of ${all} played`));
    const bar = element("div", "bar"); const fill = element("i"); fill.style.width = `${all ? (done / all) * 100 : 0}%`; bar.append(fill);
    card.append(headLine, bar);
    const table = element("table", "rank-table");
    const head = element("tr");
    for (const h of ["#", "Contestant", "Mean", "± 95%", "Std", "Games", "Paired vs best", "Best game", "Worst game", ""]) head.append(element("th", "", h));
    const thead = element("thead"); thead.append(head);
    const tbody = element("tbody");
    rows.forEach((r, i) => {
      const tr = element("tr");
      const cell = (text: string) => tr.append(element("td", "", text));
      cell(String(i + 1)); cell(r.name); cell(r.mean.toFixed(1)); cell(r.ci.toFixed(1)); cell(r.std.toFixed(1)); cell(`${r.games}/${r.total}`);
      cell(r.diff == null ? (i === 0 ? "best" : "-") : `${r.diff >= 0 ? "+" : ""}${r.diff.toFixed(1)} ± ${(r.diffCi ?? 0).toFixed(1)}`);
      for (const which of [r.best, r.worst]) {
        const td = element("td");
        if (which != null) {
          const match = members.find(t => t.tournament_id === r.tournamentId)?.matches?.find(m => m.ordinal === which);
          const link = button(`${r.scores[which]} ▶`, () => match && void showReplay(r.tournamentId, match.match_id, gameId));
          link.setAttribute("aria-label", `Replay of ${r.name}, game ${which + 1}, ${r.scores[which]} points`);
          td.append(link);
        }
        tr.append(td);
      }
      const act = element("td");
      const running = !["completed", "stopped", "failed"].includes(r.status);
      const owned = credentialOf(r.tournamentId);
      const member = members.find(t => t.tournament_id === r.tournamentId) as { can_manage?: boolean } | undefined;
      if (running && (owned || member?.can_manage)) act.append(button("Stop", () => void stop(r.tournamentId, owned)));
      else act.append(element("span", "bc-small", r.status));
      tr.append(act);
      tbody.append(tr);
    });
    table.append(thead, tbody);
    card.append(table, histogramSvg(rows));
    return card;
  }

  function histogramSvg(rows: ContestantStats[]) {
    const all = rows.flatMap(r => r.scores.filter((s): s is number => s != null));
    const box = element("div", "bc-solo-hist");
    if (!all.length) return box;
    const { edges } = histogram(all);
    const width = edges.length > 1 ? edges[1] - edges[0] : 1;
    const series = rows.map(r => {
      const c = edges.map(() => 0);
      for (const s of r.scores) if (s != null) c[Math.min(edges.length - 1, Math.floor((s - edges[0]) / width))]++;
      return c;
    });
    const peak = Math.max(1, ...series.flat());
    const W = 520, H = 140, gw = W / edges.length, bw = Math.max(2, (gw - 6) / rows.length);
    let svg = `<svg viewBox="0 0 ${W} ${H + 18}" width="100%" role="img" aria-label="Score distribution">`;
    series.forEach((c, k) => c.forEach((v, i) => {
      const h = (v / peak) * H;
      svg += `<rect class="tone-seat-${k % 4}" x="${(i * gw + 3 + k * bw).toFixed(1)}" y="${(H - h).toFixed(1)}" width="${bw.toFixed(1)}" height="${h.toFixed(1)}"><title>${rows[k].name}: ${v} games ${edges[i]}-${edges[i] + width - 1}</title></rect>`;
    }));
    edges.forEach((e, i) => { if (i % 2 === 0) svg += `<text class="ch-tick" x="${(i * gw + 3).toFixed(1)}" y="${H + 14}">${e}</text>`; });
    box.innerHTML = svg + "</svg>";
    const legend = element("div", "legend");
    rows.forEach((r, k) => { const item = element("span"); item.append(element("i", `tone-seat-${k % 4}`), r.name); legend.append(item); });
    box.append(legend);
    return box;
  }

  async function stop(id: string, credential: string | undefined) {
    try {
      const t = await options.request<Tournament>(`/api/tournaments/${id}`);
      await options.request(`/api/tournaments/${id}/stop`, { revision: t.revision }, credential);
    } catch { status.textContent = "The contestant could not be stopped."; }
    void refresh();
  }

  let closeViewer: (() => void) | null = null;
  async function showReplay(tournamentId: string, matchId: string, gameId: string) {
    closeViewer?.();
    const gamePath = encodeURIComponent(gameId);
    const [boardSpec, rulesText, settings, widget] = await Promise.all([
      options.request(`/api/games/${gamePath}/board`), options.request<string>(`/api/games/${gamePath}/rules`), options.request(`/api/games/${gamePath}/settings`), import("./game-view"),
    ]);
    const stage = element("section", "bc-embedded-stage bc-stage tc-screen");
    const holder = element("div", "bc-embedded-board");
    stage.append(holder);
    viewer.replaceChildren(stage);
    const game = widget.mountGameView(holder, { gameId, boardSpec, rulesText, settings, readOnly: true, fullscreenRoot: stage, onExit: () => closeViewer?.() });
    let epoch = 0;
    const bar = mountReplayBar({
      load: async position => {
        const mine = ++epoch;
        const frame = await options.request<any>(`/api/tournaments/${tournamentId}/matches/${matchId}/replay?position=${position}`);
        if (mine !== epoch) return;
        game.update({ table_id: tournamentId, status: "replay", config: frame.observation ? { game_id: gameId, variant: "base", players: 1 } : undefined,
          observation: frame.observation, replay_position: frame.position } as any, frame.events, { decision_id: null, actions: [] });
        bar.shown(frame.position, frame.total_positions, "");
      },
      close: () => closeViewer?.(),
    });
    const place = () => (stage.querySelector(".bc-bottom") ?? stage).prepend(bar.element);
    closeViewer = () => { bar.dispose(); game.dispose(); viewer.replaceChildren(); closeViewer = null; };
    await (async () => { try { const frame = await options.request<any>(`/api/tournaments/${tournamentId}/matches/${matchId}/replay?position=0`); game.update({ table_id: tournamentId, status: "replay", config: { game_id: gameId, variant: "base", players: 1 }, observation: frame.observation, replay_position: 0 } as any, frame.events, { decision_id: null, actions: [] }); bar.shown(0, frame.total_positions, ""); place(); } catch { viewer.replaceChildren(element("p", "bc-small", "The replay could not be loaded.")); } })();
    stage.scrollIntoView?.({ block: "start" });
  }

  void refresh();
  return { refresh, dispose() { life.abort(); clearTimeout(timer); closeViewer?.(); section.remove(); } };
}
