import { afterEach, expect, it, vi } from "vitest";
import { halvingPlan, lineupResults, mountTournamentsPage, poolLineups, seedList, wilson } from "../src/tournaments-page";

afterEach(() => { document.body.replaceChildren(); vi.unstubAllGlobals(); });
const games = [{ game_id: "dice_circuit", name: "Dice Circuit", min_players: 1, max_players: 4, variants: ["base"] },
  { game_id: "rentcraft", name: "Rentcraft", min_players: 2, max_players: 4, variants: ["base"] }];
const bots = { dice_circuit: [{ choice_id: "greedy", display_name: "Greedy bot", kind: "builtin" }, { choice_id: "random", display_name: "Random bot", kind: "builtin" },
  { choice_id: "private-abc", display_name: "mokosz-gen0", kind: "model", group: "Own trainings" }],
  rentcraft: [{ choice_id: "miser", display_name: "Miser", kind: "builtin" }, { choice_id: "investor", display_name: "Investor", kind: "builtin" }] } as Record<string, any[]>;

function server() {
  const calls: { path: string; body?: any; credential?: string }[] = [];
  const made: any[] = [];
  const request = vi.fn(async (path: string, body?: any, credential?: string) => {
    calls.push({ path, body, credential });
    const bot = /\/api\/games\/(\w+)\/bots/.exec(path);
    if (bot) return bots[bot[1]];
    if (path === "/api/tournaments" && body) { const id = String(made.length + 1).padStart(32, "0"); made.push({ tournament_id: id, spec: body, status: "running", revision: 1, spec_sha: "s", matches: [] }); return { tournament_id: id, creator_credential: `cred-${id}`, spec_sha: "s", revision: 0 }; }
    if (path === "/api/tournaments") return made;
    if (path.startsWith("/api/tournaments/")) return made.find(t => path.includes(t.tournament_id)) ?? {};
    return {};
  });
  return { calls, made, request };
}

it("tournaments: rotating line-up results credit each entrant, with a Wilson interval", () => {
  const t: any = { spec: { config: { players: 2 }, rotations: [0, 1], seats: ["miser", "investor"] }, matches: [
    { ordinal: 0, status: "completed", result: { placements: [1, 2], scores: [900, 100] } },
    { ordinal: 1, status: "completed", result: { placements: [2, 1], scores: [50, 800] } },
  ] };
  const rows = lineupResults(t, c => c);
  expect(rows.map(r => [r.label, r.games, r.wins, r.avg_place])).toEqual([["miser", 2, 2, 1], ["investor", 2, 0, 2]]);
  expect(wilson(5, 10)[0]).toBeCloseTo(0.237, 2);
  expect(seedList(5, 42)).toEqual(seedList(5, 42));
  expect(new Set(seedList(200, null)).size).toBe(200);
});

it("tournaments: a host draft fills the form and a solo tournament creates one seat-tournament per entrant on shared seeds", async () => {
  vi.stubGlobal("localStorage", { getItem: () => null, setItem: () => {} });
  const { calls, request } = server();
  const root = document.createElement("div"); document.body.append(root);
  const address = vi.fn();
  const page = mountTournamentsPage(root, { request: request as any, games: async () => games, game: "", selected: null, setAddress: address,
    draft: { game_id: "dice_circuit", players: 1, entrants: ["greedy", "private-abc"], games: 1000, name: "mokosz check" } });
  await vi.waitFor(() => expect(root.querySelectorAll(".chips .chip")).toHaveLength(2));
  expect([...root.querySelectorAll(".chips .chip")].map(c => c.firstChild!.textContent)).toEqual(["Greedy bot", "mokosz-gen0"]);
  expect(root.querySelector<HTMLSelectElement>('[aria-label="Players at each table"]')!.value).toBe("1");
  expect(root.querySelector<HTMLInputElement>('[aria-label="Number of games"]')!.value).toBe("1000");
  expect(root.querySelector(".tform")!.textContent).toContain("1. Entrants");
  [...root.querySelectorAll("button")].find(b => b.textContent === "▶ Start the tournament")!.click();
  // One request per tournament: the server saves and starts it (start: true); the browser sends no second request.
  await vi.waitFor(() => expect(calls.filter(c => c.path === "/api/tournaments" && c.body?.start === true)).toHaveLength(2));
  expect(calls.filter(c => c.path.endsWith("/start"))).toHaveLength(0);
  const specs = calls.filter(c => c.path === "/api/tournaments" && c.body).map(c => c.body);
  expect(specs.map(s => [s.name, s.seats])).toEqual([["Solo: mokosz check · Greedy bot", ["greedy"]], ["Solo: mokosz check · mokosz-gen0", ["private-abc"]]]);
  expect(specs[0].seeds).toEqual(specs[1].seeds);
  expect(specs[0]).toMatchObject({ config: { game_id: "dice_circuit", players: 1 }, match_count: 1000, rotations: [0] });
  page.dispose();
});

it("tournaments: a line-up rotates every seat over shared seeds and keeps Rentcraft rules", async () => {
  vi.stubGlobal("localStorage", { getItem: () => null, setItem: () => {} });
  const { calls, request } = server();
  const root = document.createElement("div"); document.body.append(root);
  const page = mountTournamentsPage(root, { request: request as any, games: async () => games, game: "", selected: null, setAddress: () => {} });
  page.open({ game_id: "rentcraft", players: 2, entrants: ["miser", "investor"], games: 101 });
  await vi.waitFor(() => expect(root.querySelectorAll(".chips .chip")).toHaveLength(2));
  root.querySelector<HTMLInputElement>('[aria-label="Round limit"]')!.value = "60";
  [...root.querySelectorAll("button")].find(b => b.textContent === "▶ Start the tournament")!.click();
  await vi.waitFor(() => expect(calls.some(c => c.path === "/api/tournaments" && c.body?.start === true)).toBe(true));
  expect(calls.some(c => c.path.endsWith("/start"))).toBe(false);
  const spec = calls.find(c => c.path === "/api/tournaments" && c.body)!.body;
  expect(spec).toMatchObject({ seats: ["miser", "investor"], rotations: [0, 1], match_count: 102, config: { players: 2, settings: { max_rounds: 60, trade_windows: "full" } } });
  expect(spec.seeds).toHaveLength(51);
  expect(spec.sampler_seeds[0]).toHaveLength(2);
  page.dispose();
});

it("tournaments: pool line-ups cover every combination and credit the seated entrants; the server names the entrants", () => {
  expect(poolLineups(3, 2, 4, 1)).toEqual([[0, 1], [0, 2], [1, 2], [0, 1]]);
  expect(new Set(poolLineups(8, 4, 10, 5).map(l => l.join())).size).toBeGreaterThan(1);
  expect(halvingPlan(4, 400, 0.5, 1)).toEqual([{ round: 1, candidates: 4, survivors: 2, games: 200 }, { round: 2, candidates: 2, survivors: 1, games: 200 }]);
  const t: any = { entrants: [{ choice_id: "miser", display_name: "Miser" }, { choice_id: "investor", display_name: "Investor" }, { choice_id: "random", display_name: "Random" }],
    spec: { config: { players: 2 }, rotations: [0, 1], lineups: [[0, 2], [1, 2]], references: [] }, matches: [
      { ordinal: 0, status: "completed", result: { placements: [1, 2], scores: [9, 1] } },
      { ordinal: 1, status: "completed", result: { placements: [1, 2], scores: [8, 2] } },
      { ordinal: 2, status: "completed", result: { placements: [2, 1], scores: [1, 9] } },
    ] };
  const rows = lineupResults(t, c => c);
  expect(rows.map(r => [r.name, r.games, r.wins])).toEqual([["Miser", 2, 1], ["Investor", 1, 0], ["Random", 3, 2]]);
});

it("tournaments: a pool tournament sends its line-ups and a knockout goes to the knockout API with its sparring partners", async () => {
  vi.stubGlobal("localStorage", { getItem: () => null, setItem: () => {} });
  const { calls, request } = server();
  const knockout = { knockout_id: "k".repeat(32), status: "running", revision: 1, error: null, created: 1,
    spec: { name: "pick", config: { game_id: "rentcraft", players: 2, variant: "base" }, candidates: ["miser", "investor"], sparring: ["miser"], games: 400, keep: 0.5, min_candidates: 1, metric: "wins", seed: 7, random_seed: false, max_inflight: 4 },
    labels: { candidates: ["Miser", "Investor"], sparring: ["Miser"] }, plan: [{ round: 1, candidates: 2, survivors: 1, games: 400 }],
    rounds: [{ round: 1, candidates: 2, games: 10, planned_games: 400, finished: true, survivors: [1], tournaments: {},
      table: [{ candidate: 1, label: "Investor", games: 5, wins: 4, win_rate: 0.8, avg_place: 1.2, rank: 1 }, { candidate: 0, label: "Miser", games: 5, wins: 1, win_rate: 0.2, avg_place: 1.8, rank: 2 }] }],
    winner: 1, top: [1], games_done: 10, games_total: 400 };
  const wrapped = vi.fn(async (path: string, body?: any, credential?: string) => {
    if (path === "/api/knockouts" && body) { calls.push({ path, body, credential }); return { knockout_id: knockout.knockout_id, creator_credential: "ko-secret", revision: 0 }; }
    if (path === "/api/knockouts") return [knockout];
    if (path.startsWith("/api/knockouts/")) { calls.push({ path, body, credential }); return knockout; }
    return request(path, body, credential);
  });
  const root = document.createElement("div"); document.body.append(root);
  const page = mountTournamentsPage(root, { request: wrapped as any, games: async () => games, game: "", selected: null, setAddress: () => {} });
  page.open({ game_id: "rentcraft", players: 2, entrants: ["miser", "investor", "miser"], games: 100, mode: "pool" });
  await vi.waitFor(() => expect(root.querySelectorAll(".chips .chip")).toHaveLength(3));
  [...root.querySelectorAll("button")].find(b => b.textContent === "▶ Start the tournament")!.click();
  await vi.waitFor(() => expect(calls.some(c => c.path === "/api/tournaments" && c.body?.start === true)).toBe(true));
  const pool = calls.find(c => c.path === "/api/tournaments" && c.body)!.body;
  expect(pool.seats).toEqual(["miser", "investor", "miser"]);
  expect(pool.lineups).toHaveLength(50);
  expect(pool.lineups.slice(0, 3)).toEqual([[0, 1], [0, 2], [1, 2]]);
  await vi.waitFor(() => expect(root.querySelector(".kpis")).not.toBeNull()); // the submit has finished: its details are shown
  page.open({ game_id: "rentcraft", players: 2, entrants: ["miser", "investor", "random"], games: 400, mode: "knockout", sparring: ["random"] });
  await vi.waitFor(() => expect(root.querySelectorAll(".chips .chip.sparring")).toHaveLength(1));
  expect(root.querySelector(".bc-knockout-opts")!.textContent).toContain("1 round: 2 → 1 candidates");
  [...root.querySelectorAll("button")].find(b => b.textContent === "▶ Start the tournament")!.click();
  await vi.waitFor(() => expect(calls.some(c => c.path === "/api/knockouts" && c.body?.start === true)).toBe(true));
  const ko = calls.find(c => c.path === "/api/knockouts" && c.body)!.body;
  expect(ko).toMatchObject({ candidates: ["miser", "investor"], sparring: ["random"], games: 400, keep: 0.5, min_candidates: 1, metric: "wins", start: true });
  expect(calls.some(c => c.path.endsWith("/start"))).toBe(false);
  await vi.waitFor(() => expect(root.querySelector(".halving .winner")?.textContent).toContain("Winner: Investor"));
  expect(root.textContent).toContain("Round 1 of 1 · 2 candidates");
  expect(root.querySelector(".bc-tlist")!.textContent).toContain("pick");
  page.dispose();
});

it("tournaments: the browser never fits ratings; a completed tournament is ranked by the server", async () => {
  const done = { tournament_id: "d".repeat(32), status: "completed", revision: 3, spec_sha: "s",
    entrants: [{ choice_id: "miser", display_name: "Miser" }, { choice_id: "investor", display_name: "Investor" }],
    spec: { name: "done", seeds: [1], rotations: [0, 1], match_count: 2, max_inflight: 1, config: { game_id: "rentcraft", players: 2, variant: "base" } },
    matches: [{ match_id: "m", ordinal: 0, status: "completed", result: { placements: [1, 2], scores: [1, 0], identities: [] } }] };
  const store = new Map([["boardcraft.tournament.credentials", JSON.stringify({ [done.tournament_id]: "owner" })]]);
  vi.stubGlobal("localStorage", { getItem: (k: string) => store.get(k) ?? null, setItem: (k: string, v: string) => store.set(k, v) });
  const calls: string[] = [];
  const request = vi.fn(async (path: string, body?: unknown) => {
    if (body !== undefined) calls.push(path);
    if (path === "/api/tournaments") return [done];
    if (path.startsWith("/api/tournaments/")) return done;
    if (path.includes("/bots")) return bots.rentcraft;
    return [];
  });
  const root = document.createElement("div"); document.body.append(root);
  const page = mountTournamentsPage(root, { request: request as any, games: async () => games, game: "", selected: done.tournament_id, setAddress: () => {} });
  await vi.waitFor(() => expect(root.textContent).toContain("Miser"));
  await new Promise(resolve => setTimeout(resolve, 50));
  expect(calls).toEqual([]);
  page.dispose();
});

function pendingServer(t: any) {
  const calls: { path: string; body?: any; credential?: string }[] = [];
  const request = vi.fn(async (path: string, body?: any, credential?: string) => {
    calls.push({ path, body, credential });
    if (/\/api\/games\/(\w+)\/bots/.test(path)) return bots.dice_circuit;
    if (path === "/api/tournaments") return [t];
    if (path === "/api/knockouts") return [];
    if (path.split("?")[0] === `/api/tournaments/${t.tournament_id}`) return t;
    return {};
  });
  return { calls, request };
}
const pendingT = (extra: any = {}) => ({ tournament_id: "a".repeat(32), status: "pending", revision: 0, spec_sha: "f".repeat(64), matches: [],
  entrants: [{ choice_id: "greedy", display_name: "Greedy bot" }, { choice_id: "random", display_name: "Random bot" }],
  spec: { name: "stuck", seeds: [1], rotations: [0, 1], match_count: 2, max_inflight: 1, config: { game_id: "dice_circuit", players: 2, variant: "base" } }, ...extra });

it("tournaments: a pending tournament offers Start and Delete to its creator (credential in this browser)", async () => {
  const t = pendingT();
  vi.stubGlobal("localStorage", { getItem: (k: string) => (k === "boardcraft.tournament.credentials" ? JSON.stringify({ [t.tournament_id]: "creator" }) : null), setItem: () => {} });
  const { calls, request } = pendingServer(t);
  const root = document.createElement("div"); document.body.append(root);
  const page = mountTournamentsPage(root, { request: request as any, games: async () => games, game: "", selected: t.tournament_id, setAddress: () => {} });
  await vi.waitFor(() => expect([...root.querySelectorAll("button")].some(b => b.textContent === "▶ Start")).toBe(true));
  expect([...root.querySelectorAll("button")].some(b => b.textContent === "🗑 Delete")).toBe(true);
  [...root.querySelectorAll("button")].find(b => b.textContent === "▶ Start")!.click();
  await vi.waitFor(() => expect(calls.some(c => c.path === `/api/tournaments/${t.tournament_id}/start`)).toBe(true));
  const start = calls.find(c => c.path.endsWith("/start"))!;
  expect(start.body).toEqual({ spec_sha: t.spec_sha, revision: 0 });
  expect(start.credential).toBe("creator");
  page.dispose();
});

it("tournaments: the server's can_manage shows the management buttons without any credential (open management)", async () => {
  vi.stubGlobal("localStorage", { getItem: () => null, setItem: () => {} });
  const managed = pendingT({ can_manage: true, management: "open" });
  const first = pendingServer(managed);
  const root = document.createElement("div"); document.body.append(root);
  const page = mountTournamentsPage(root, { request: first.request as any, games: async () => games, game: "", selected: managed.tournament_id, setAddress: () => {} });
  await vi.waitFor(() => expect([...root.querySelectorAll("button")].some(b => b.textContent === "▶ Start")).toBe(true));
  [...root.querySelectorAll("button")].find(b => b.textContent === "▶ Start")!.click();
  await vi.waitFor(() => expect(first.calls.some(c => c.path.endsWith("/start"))).toBe(true));
  expect(first.calls.find(c => c.path.endsWith("/start"))!.credential).toBeUndefined();
  page.dispose();
  // The public server (no credential here, can_manage false): no management buttons.
  const other = pendingServer(pendingT({ can_manage: false, management: "credential" }));
  const root2 = document.createElement("div"); document.body.append(root2);
  const page2 = mountTournamentsPage(root2, { request: other.request as any, games: async () => games, game: "", selected: "a".repeat(32), setAddress: () => {} });
  await vi.waitFor(() => expect(root2.textContent).toContain("stuck"));
  expect([...root2.querySelectorAll("button")].some(b => ["▶ Start", "🗑 Delete", "⏹ Stop"].includes(b.textContent!))).toBe(false);
  page2.dispose();
});

it("tournaments: the list uses the server's compact rows (progress and per-entrant results) and never reads every match", async () => {
  vi.stubGlobal("localStorage", { getItem: () => null, setItem: () => {} });
  const lineup = { tournament_id: "b".repeat(32), status: "completed", revision: 2, spec_sha: "s", done: 4,
    entrants: [{ choice_id: "miser", display_name: "Miser" }, { choice_id: "investor", display_name: "Investor" }],
    results: [{ games: 4, wins: 1, place: 7, score: 900 }, { games: 4, wins: 3, place: 5, score: 1500 }],
    spec: { name: "duel", seed_count: 2, first_seed: 5, pool: false, rotations: [0, 1], match_count: 4, max_inflight: 1, config: { game_id: "rentcraft", players: 2, variant: "base" } } };
  const solo = (id: string, name: string, games: number, score: number) => ({ tournament_id: id.repeat(32), status: "completed", revision: 1, spec_sha: "s", done: games,
    entrants: [{ choice_id: "greedy", display_name: name }], results: [{ games, wins: games, place: games, score }],
    spec: { name: `Solo: check · ${name}`, seed_count: 3, first_seed: 9, pool: false, rotations: [0], match_count: 3, max_inflight: 1, config: { game_id: "dice_circuit", players: 1, variant: "base" } } });
  const soloA = solo("c", "Greedy bot", 3, 300), soloB = solo("e", "Random bot", 2, 100);
  const scores: Record<string, (number | null)[]> = { [soloA.tournament_id]: [90, 100, 110], [soloB.tournament_id]: [40, 60, null] };
  const paths: string[] = [];
  const request = vi.fn(async (path: string) => {
    paths.push(path);
    if (path === "/api/tournaments") return [lineup, soloA, soloB];
    if (path === "/api/knockouts") return [];
    if (path.includes("/bots")) return path.includes("rentcraft") ? bots.rentcraft : bots.dice_circuit;
    const page = /\/api\/tournaments\/(\w+)\/matches\?offset=(\d+)&limit=1/.exec(path);
    if (page) return { items: [{ match_id: `match-${page[2]}`, ordinal: Number(page[2]), status: "completed" }], total: 3, offset: Number(page[2]), limit: 1 };
    const one = /\/api\/tournaments\/(\w+)\?matches=false/.exec(path);
    if (one) { const t = [lineup, soloA, soloB].find(x => x.tournament_id === one[1])!; return { ...t, scores: scores[t.tournament_id] ?? null }; }
    if (path.startsWith("/api/games/")) return new Promise(() => {}); // the replay viewer itself is not part of this test
    return {};
  });
  const root = document.createElement("div"); document.body.append(root);
  const page = mountTournamentsPage(root, { request: request as any, games: async () => games, game: "", selected: lineup.tournament_id, setAddress: () => {} });
  await vi.waitFor(() => expect(root.querySelector(".bc-tlist")!.textContent).toContain("4 / 4 games · 🏆 Investor"));
  expect(root.querySelector(".bc-tlist")!.textContent).toContain("5 / 6 games · 🏆 Greedy bot · mean 100.0");
  // The open line-up tournament: its results table from the stored per-entrant results.
  await vi.waitFor(() => expect(root.querySelector(".tres")?.textContent).toContain("Investor"));
  expect(root.querySelector(".tres tbody tr")!.textContent).toContain("75.0%");
  expect(root.querySelector(".kpis")!.textContent).toContain("2 seeds");
  // The solo group: each member's stored scores, read on demand; a replay looks up its one match.
  [...root.querySelectorAll<HTMLAnchorElement>(".bc-tlist a")].find(a => a.textContent!.includes("(solo)"))!.click();
  await vi.waitFor(() => expect(root.querySelector(".tres")?.textContent).toContain("Random bot"));
  expect(paths).toContain(`/api/tournaments/${soloA.tournament_id}?matches=false`);
  expect(root.querySelector(".tres tbody tr")!.textContent).toContain("100.0");
  const best = [...root.querySelectorAll<HTMLButtonElement>(".tres button")].find(b => b.textContent === "110 ▶")!;
  best.click();
  await vi.waitFor(() => expect(paths).toContain(`/api/tournaments/${soloA.tournament_id}/matches?offset=2&limit=1`));
  expect(paths.filter(p => p === "/api/tournaments" || p.startsWith("/api/tournaments/")).every(p => !/\/api\/tournaments\/\w+$/.test(p))).toBe(true);
  page.dispose();
});
