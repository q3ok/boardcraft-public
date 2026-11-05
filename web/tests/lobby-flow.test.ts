import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { mountPlayApp } from "../src/app";
import { SessionStore } from "../src/lobby";
import sheet from "../../src/boardcraft/games/dice_circuit/data/base-sheet.json";
import spaces from "../../src/boardcraft/games/rentcraft/data/board.en.json";

type Seat = { seat: number; kind: string; occupied: boolean; display_name: string };
interface Table { id: string; game_id: string; players: number; status: string; seats: Seat[]; visibility: string; creator: string; autostart?: boolean }
const hex = (n: number) => n.toString(16).padStart(32, "0");
const games = [
  { game_id: "dice_circuit", name: "Dice Circuit", description: "Roll six dice and fill your sheet.", variants: ["base"], min_players: 1, max_players: 4, bot_kinds: ["greedy"], capabilities: { seat_operations: false } },
  { game_id: "rentcraft", name: "Rentcraft", description: "Buy streets, trade and collect rent.", variants: ["base"], min_players: 2, max_players: 4, bot_kinds: ["greedy"], capabilities: { seat_operations: true } },
];
const bots = [
  { choice_id: "greedy", display_name: "Greedy bot", kind: "builtin", description: "Takes the best immediate gain.", reference: {} },
  { choice_id: "mlp-1", display_name: "Trained MLP", kind: "model", description: "A trained policy.", reference: {} },
];
const rentData = (players: number) => ({ phase: "pre_roll", players: Array.from({ length: players }, (_, id) => ({ id, cash: 90, position: 0, jail_cards: [], bankrupt: false })),
  owner: spaces.map(() => null), buildings: spaces.map(() => 0), mortgaged: spaces.map(() => false), round: 1, current: 0, debts: [], event_cursor: 0 });
const diceData = (players: number) => ({ round: 1, active_player: 0, phase: "passive", regular_opportunity: 0, event_cursor: 0,
  dice: Object.fromEntries(["yellow", "blue", "green", "orange", "purple", "white"].map(id => [id, { value: 2, zone: "tray" }])),
  sheets: Array.from({ length: players }, () => ({ yellow: sheet.yellow.premarked, blue: [], green: 0, orange: [], purple: [], earned_rewards: [], rerolls: 0, extras: 0, emblem_count: 0 })) });

function fakeServer() {
  const tables = new Map<string, Table>();
  const log: { method: string; path: string; body?: any; auth: string | null }[] = [];
  let counter = 0, listFailures = 0, startStatus = 0, serverStarts = 0;
  let gate: Promise<void> | null = null;
  const json = (value: unknown, status = 200) => new Response(JSON.stringify(value), { status, headers: { "Content-Type": "application/json" } });
  const add = (game_id: string, kinds: string[], extra: Partial<Table> = {}) => {
    const id = hex(++counter + 4096);
    tables.set(id, { id, game_id, players: kinds.length, status: "lobby", visibility: "invite", creator: "creator-" + id.slice(-4),
      seats: kinds.map((kind, seat) => ({ seat, kind, occupied: !["human", "agent"].includes(kind), display_name: kind === "human" ? "Human" : kind === "agent" ? "Agent" : "Greedy bot" })), ...extra });
    return tables.get(id)!;
  };
  const view = (t: Table, auth: string | null) => {
    const base = { table_id: t.id, status: t.status, config: { game_id: t.game_id, variant: "base", players: t.players }, seats: t.seats.map(s => ({ ...s })) };
    if (t.status === "lobby") return { ...base, observation: null };
    const viewer = auth?.startsWith("Bearer seat-") ? Number(auth.slice(12)) : null;
    const data = t.game_id === "rentcraft" ? rentData(t.players) : diceData(t.players);
    return { ...base, revision: 1, event_cursor: 0, observation: { game_id: t.game_id, viewer, revision: 1, decision: null, data } };
  };
  const handler = async (path: string, init: RequestInit = {}) => {
    const url = new URL(path, "http://server.test"), method = init.method ?? "GET", auth = new Headers(init.headers).get("Authorization");
    const body = init.body ? JSON.parse(String(init.body)) : undefined;
    log.push({ method, path, body, auth });
    const p = url.pathname;
    let m: RegExpMatchArray | null;
    if (p === "/api/games") return json(games);
    if ((m = p.match(/^\/api\/games\/(\w+)\/bots$/))) return json(bots);
    if ((m = p.match(/^\/api\/games\/(\w+)\/settings$/))) return json({ table: {}, live: {}, bots: {}, simulation_defaults: {} });
    if ((m = p.match(/^\/api\/games\/(\w+)\/board$/))) return json(m[1] === "rentcraft" ? { spaces, station_rents: [25], utility_multipliers: [4], cards: {} } : sheet);
    if (p.endsWith("/rules")) return new Response("Rules", { headers: { "Content-Type": "text/plain" } });
    if (p === "/api/tables" && method === "GET") {
      if (gate) await gate;
      if (listFailures-- > 0) return new Response("", { status: 503 });
      const game = url.searchParams.get("game_id"), status = url.searchParams.get("status");
      const offset = Number(url.searchParams.get("offset")), limit = Number(url.searchParams.get("limit"));
      const listed = [...tables.values()].filter(t => t.visibility === "listed" && (!game || t.game_id === game) && (!status || t.status === status));
      return json({ items: listed.slice(offset, offset + limit).map(t => ({ table_id: t.id, game_id: t.game_id, variant: "base", players: t.players, status: t.status, seats: t.seats })), total: listed.length, offset, limit });
    }
    if (p === "/api/tables" && method === "POST") {
      const kinds = body.seats.map((s: any) => typeof s === "string" ? s : s.choice_id);
      const t = add(body.config.game_id, kinds, { visibility: body.visibility ?? "invite" });
      return json({ table_id: t.id, creator_credential: t.creator });
    }
    if ((m = p.match(/^\/api\/tables\/(\w+)\/(\w+)$/))) {
      const t = tables.get(m[1]);
      if (!t) return new Response("", { status: 404 });
      // Like the real server: an armed table is started by the server once every seat is taken.
      const armedStart = () => { if (t.autostart && t.status === "lobby" && t.seats.every(s => s.occupied)) { t.status = "playing"; serverStarts++; } };
      if (m[2] === "join") { t.seats[body.seat].occupied = true; armedStart(); return json({ credential: "seat-" + body.seat, seat: body.seat }); }
      if (m[2] === "autostart") {
        if (auth !== "Bearer " + t.creator) return new Response("", { status: 403 });
        t.autostart = body.on; armedStart();
        return json({ table_id: t.id, autostart: body.on, status: t.status });
      }
      if (m[2] === "start") {
        if (auth !== "Bearer " + t.creator) return new Response("", { status: 403 });
        if (startStatus) { t.status = "playing"; return new Response("", { status: startStatus }); }
        if (t.status !== "lobby" || !t.seats.every(s => s.occupied)) return new Response("", { status: 409 });
        t.status = "playing";
        return json({});
      }
      if (m[2] === "view") return json(view(t, auth));
      if (m[2] === "actions") return json({ decision_id: null, actions: [] });
    }
    return json({});
  };
  const calls = (suffix: string) => log.filter(r => r.method === "POST" && r.path.endsWith(suffix));
  return { tables, add, log, handler, calls,
    join: (t: Table, seat: number) => { t.seats[seat].occupied = true; if (t.autostart && t.status === "lobby" && t.seats.every(s => s.occupied)) { t.status = "playing"; serverStarts++; } },
    serverStarts: () => serverStarts,
    failList: (n: number) => { listFailures = n; },
    holdList: () => { let release!: () => void; gate = new Promise<void>(resolve => { release = resolve; }); return () => { gate = null; release(); }; }, conflictOnStart: (status: number) => { startStatus = status; } };
}

let apps: { dispose(): void }[] = [], server: ReturnType<typeof fakeServer>;
/** A Map-backed localStorage (the environment's own may be missing), so tests can also make it fail. */
function memoryStorage() {
  const data = new Map<string, string>();
  return { getItem: (k: string) => data.get(k) ?? null, setItem: (k: string, v: string) => void data.set(k, String(v)), removeItem: (k: string) => void data.delete(k), clear: () => data.clear() };
}
beforeEach(() => {
  server = fakeServer();
  vi.stubGlobal("localStorage", memoryStorage());
  vi.stubGlobal("fetch", (path: string, init?: RequestInit) => server.handler(path, init));
  globalThis.ResizeObserver = class { observe() {} unobserve() {} disconnect() {} } as any;
  HTMLDialogElement.prototype.close = function () {};
});
afterEach(() => {
  apps.forEach(a => a.dispose());
  apps = [];
  vi.restoreAllMocks();
  localStorage.clear();
  vi.unstubAllGlobals();
  sessionStorage.clear();
  history.replaceState(null, "", "/");
  document.body.replaceChildren();
});
function open(options: Parameters<typeof mountPlayApp>[1] = {}) {
  const root = document.createElement("main");
  document.body.append(root);
  const app = mountPlayApp(root, options);
  apps.push(app);
  return { root, app };
}
const until = (check: () => void, timeout = 4000) => vi.waitFor(() => { document.dispatchEvent(new Event("visibilitychange")); check(); }, { timeout, interval: 40 });
const ready = async (root: HTMLElement) => {
  await vi.waitFor(() => expect(root.querySelector('[aria-label="Game"]')).toBeTruthy());
  const game = root.querySelector<HTMLSelectElement>('[aria-label="Game"]')!;
  if (!game.value) { game.value = "dice_circuit"; game.dispatchEvent(new Event("change")); }
  await vi.waitFor(() => expect(root.querySelector<HTMLButtonElement>('.bc-create button[type="submit"]')!.disabled).toBe(false));
  if (game.dataset.testSelected !== game.value) {
    const count = root.querySelector<HTMLSelectElement>('[aria-label="Number of players"]')!;
    count.value = count.options[0].value; count.dispatchEvent(new Event("change")); game.dataset.testSelected = game.value;
  }
};
const field = (root: HTMLElement, label: string) => root.querySelector<HTMLSelectElement & HTMLInputElement>(`[aria-label="${label}"]`)!;
const choose = (root: HTMLElement, label: string, value: string) => { const el = field(root, label); el.value = value; el.dispatchEvent(new Event("change", { bubbles: true })); };
const press = (root: HTMLElement, label: string) => { const b = [...root.querySelectorAll("button")].find(x => x.textContent === label || x.getAttribute("aria-label") === label); expect(b, label).toBeTruthy(); b!.click(); };
const sessions = () => JSON.parse(localStorage.getItem("boardcraft.sessions.v1") ?? "{}")[location.origin] ?? [];
const lastTable = () => [...server.tables.values()].at(-1)!;
/** The lobby panel over the board (the room also contains the words "Waiting for players" in its status filter). */
const overlay = (root: HTMLElement) => root.querySelector<HTMLElement>(".bc-lobby-overlay");
const inLobby = (root: HTMLElement) => vi.waitFor(() => expect(overlay(root)).not.toBeNull());

it("lobbyFlow room lists only returned tables with status, participants and the right actions", async () => {
  server.add("rentcraft", ["human", "agent"], { visibility: "listed" });
  server.add("dice_circuit", ["human"], { visibility: "listed", status: "playing" });
  server.add("dice_circuit", ["human"], { visibility: "listed", status: "completed" });
  server.add("dice_circuit", ["human"]);
  const { root } = open();
  await vi.waitFor(() => expect(root.querySelectorAll(".bc-table-row")).toHaveLength(3));
  const rows = [...root.querySelectorAll<HTMLElement>(".bc-table-row")];
  expect(rows[0].textContent).toMatch(/Rentcraft.*waiting for players.*Human · human \(free\).*Agent · agent \(free\)/s);
  expect(rows[1].textContent).toContain("in play");
  expect(rows[2].textContent).toContain("finished");
  const labels = (row: HTMLElement) => [...row.querySelectorAll("button")].map(b => b.textContent);
  expect(labels(rows[0])).toEqual(["Sit down", "Watch"]);
  expect(labels(rows[1])).toEqual(["Watch"]);
  expect(labels(rows[2])).toEqual(["▶ Replays", "Open"]);
  expect(root.textContent).toContain("Roll six dice and fill your sheet.");
  expect(root.textContent).toMatch(/1-4 players/);
  server.log.length = 0;
  rows[1].querySelector("button")!.click();
  const id = [...server.tables.values()][1].id;
  await vi.waitFor(() => expect(server.log.some(r => r.path === `/api/tables/${id}/view`)).toBe(true));
  expect(location.hash).toBe("#/table/" + id);
  expect(server.calls("/join")).toHaveLength(0);
  expect(server.log.filter(r => r.path.includes(id)).every(r => r.auth === null)).toBe(true);
});

it("lobbyFlow filters by game and status and paginates with the exact query", async () => {
  for (let i = 0; i < 56; i++) server.add("rentcraft", ["human", "agent"], { visibility: "listed" });
  server.add("dice_circuit", ["human"], { visibility: "listed", status: "completed" });
  const { root } = open();
  await vi.waitFor(() => expect(root.textContent).toContain("page 1 of 5 · 57 tables"));
  const query = () => new URL(server.log.filter(r => r.path.startsWith("/api/tables?")).at(-1)!.path, "http://x").searchParams;
  expect(Object.fromEntries(query())).toEqual({ offset: "0", limit: "12" });
  press(root, "Next page");
  await vi.waitFor(() => expect(root.textContent).toContain("page 2 of 5 · 57 tables"));
  expect(query().get("offset")).toBe("12");
  expect(root.querySelector<HTMLButtonElement>('[aria-label="Next page"]')!.disabled).toBe(false);
  choose(root, "Filter by game", "rentcraft");
  choose(root, "Filter by status", "lobby");
  await vi.waitFor(() => expect(root.textContent).toContain("page 1 of 5 · 56 tables"));
  expect(Object.fromEntries(query())).toEqual({ game_id: "rentcraft", status: "lobby", offset: "0", limit: "12" });
  choose(root, "Filter by game", "dice_circuit");
  await vi.waitFor(() => expect(root.textContent).toMatch(/No tables match these filters/));
});

it("lobbyFlow explains loading, empty and failed lists and retries", async () => {
  server.failList(1);
  const release = server.holdList();
  const { root } = open();
  expect(root.textContent).toMatch(/Loading games/);
  await vi.waitFor(() => expect(root.textContent).toMatch(/Loading open tables/));
  release();
  await vi.waitFor(() => expect(root.textContent).toContain("Open tables could not be loaded"));
  press(root, "Try again");
  await vi.waitFor(() => expect(root.textContent).toMatch(/There are no tables yet/));
  expect(root.textContent).not.toContain("could not be loaded");
  expect(root.textContent).toMatch(/Create the first one/);
});

it("lobbyFlow describes games and participants in groups, with no raw JSON or ID entry for ordinary choices", async () => {
  const { root } = open();
  await ready(root);
  expect(root.querySelector(".bc-games")!.textContent).toContain("Buy streets, trade and collect rent.");
  const groups = [...root.querySelectorAll('[aria-label="Player 1 type"] optgroup')].map(g => [g.getAttribute("label"), [...g.querySelectorAll("option")].map(o => o.textContent)]);
  expect(groups).toEqual([["People", ["Human"]], ["Agents", ["Agent"]], ["Built-in bots", ["Greedy bot"]], ["Trained models", ["Trained MLP"]], ["Unused", ["Empty"]]]);
  choose(root, "Player 1 type", "agent");
  expect(root.querySelector(".bc-create")!.textContent).toMatch(/The agent joins after the table is created/);
  // As in the shared design a bot seat shows only its list; a model seat shows the model's description.
  choose(root, "Player 1 type", "greedy");
  expect(root.querySelector(".bc-create")!.textContent).not.toContain("Takes the best immediate gain.");
  choose(root, "Player 1 type", "mlp-1");
  expect(root.querySelector(".bc-create")!.textContent).toContain("A trained policy.");
  expect(root.querySelector(".bc-create textarea")).toBeNull();
  expect(root.querySelector('[name="visibility"]')).toBeNull();
  expect(root.querySelector(".bc-create")!.textContent).not.toMatch(/Tournament/);
});

it("lobbyFlow separates Play and Tournaments navigation without tournament forms under table creation", async () => {
  const { root } = open();
  await ready(root);
  const nav = root.querySelector("nav")!;
  expect([...nav.querySelectorAll("button")].map(b => b.textContent)).toEqual(["🎲 Play", "⚔ Tournaments", "🏆 Rankings", "▶ Replays"]);
  expect(nav.querySelector('[aria-current="page"]')!.textContent).toBe("🎲 Play");
  expect(root.querySelector(".bc-create")!.textContent).not.toMatch(/Tournament/);
  expect(root.querySelector(".bc-tournaments")).toBeNull();
  press(root, "⚔ Tournaments");
  await vi.waitFor(() => expect(root.querySelector(".bc-tournaments")).not.toBeNull());
  expect(root.querySelector('nav [aria-current="page"]')!.textContent).toBe("⚔ Tournaments");
  expect(location.hash).toBe("#/tournaments");
  expect(root.querySelector(".bc-hub .bc-create")).toBeNull();
  press(root, "🎲 Play");
  await ready(root);
});

it("lobbyFlow Create & sit joins only the first human seat, arms start and remembers the session", async () => {
  const { root } = open();
  await ready(root);
  choose(root, "Game", "rentcraft");
  await vi.waitFor(() => expect(field(root, "Player 2 type")).toBeTruthy());
  await ready(root);
  choose(root, "Player 1 type", "human");
  choose(root, "Player 2 type", "human");
  // No "Your seat" and no auto-start checkbox (as in the shared design): the first human seat is the creator's.
  expect(root.querySelector('[aria-label="Your seat"]')).toBeNull();
  expect(root.querySelector('[aria-label="Start automatically when every seat is ready"]')).toBeNull();
  press(root, "Create and sit");
  await vi.waitFor(() => expect(server.calls("/join")).toHaveLength(1));
  expect(server.calls("/join")[0].body).toEqual({ seat: 0 });
  expect(server.calls("/start")).toHaveLength(0);
  const t = lastTable();
  expect(server.calls("")[0].body).toEqual({ config: { game_id: "rentcraft", variant: "base", players: 2 }, seats: ["human", "human"], seat_names: ["Player", null], visibility: "listed" });
  await inLobby(root);
  expect(sessions()).toEqual([expect.objectContaining({ table: t.id, creator: t.creator, credential: "seat-0", seat: 0, gameId: "rentcraft", autostart: true })]);
  expect(location.hash).toBe(`#/table/${t.id}?seat=0`);
  expect(JSON.stringify(location.href)).not.toContain(t.creator);
  expect(document.body.innerHTML).not.toContain(t.creator);
});

it("lobbyFlow Create & watch never claims a seat and new tables are always listed", async () => {
  const { root } = open();
  await ready(root);
  choose(root, "Game", "rentcraft");
  await ready(root);
  choose(root, "Player 2 type", "agent");
  press(root, "Create and watch");
  await vi.waitFor(() => expect(server.calls("/api/tables")).toHaveLength(1));
  expect(server.calls("/api/tables")[0].body.visibility).toBe("listed");
  await inLobby(root);
  expect(server.calls("/join")).toHaveLength(0);
  expect(sessions()[0].credential).toBeUndefined();
  await vi.waitFor(() => expect(overlay(root)!.textContent).toContain("Free seats: 1 (human), 2 (agent)"));
  await new Promise(resolve => setTimeout(resolve, 100));
  expect(server.calls("/join")).toHaveLength(0);
});

it.each(["dice_circuit", "rentcraft"])("lobbyFlow %s agent creation opens the full board with a prompt for each open agent seat", async game => {
  const writeText = vi.fn(async (_text: string) => undefined);
  Object.defineProperty(navigator, "clipboard", { value: { writeText }, configurable: true });
  const { root } = open();
  await ready(root);
  choose(root, "Game", game);
  await ready(root);
  choose(root, "Number of players", "2");
  choose(root, "Player 1 type", "agent");
  choose(root, "Player 2 type", "agent");
  press(root, "Create and watch");
  await vi.waitFor(() => expect(root.querySelectorAll("textarea[data-agent-prompt]")).toHaveLength(2));
  const t = lastTable();
  await vi.waitFor(() => expect(root.querySelector(game === "rentcraft" ? ".rc-table" : ".bc-game")).not.toBeNull());
  const prompts = [...root.querySelectorAll<HTMLTextAreaElement>("textarea[data-agent-prompt]")].map(a => a.value);
  expect(prompts[0]).toContain("Table ID: " + t.id);
  expect(prompts[0]).toContain("Boardcraft server: " + location.origin);
  expect(prompts[0]).toContain(`Game: ${game === "rentcraft" ? "Rentcraft" : "Dice Circuit"} (${game})`);
  expect(prompts[0]).toContain("API seat 0");
  expect(prompts[1]).toContain("API seat 1");
  for (const text of prompts) expect(text).not.toContain(t.creator);
  expect(overlay(root)!.textContent).toContain("Free seats: 1 (agent), 2 (agent)");
  // As in the shared design the sentences for the agents are already in the clipboard after creating.
  expect(writeText).toHaveBeenCalledTimes(1);
  expect(writeText.mock.calls[0][0]).toContain("API seat 0");
  expect(writeText.mock.calls[0][0]).toContain("API seat 1");
  expect(writeText.mock.calls[0][0]).not.toContain(t.creator);
  delete (navigator as any).clipboard;
});

it("lobbyFlow an armed table is started by the server when agents join; the browser never sends start and the same widget plays", async () => {
  const { root } = open();
  await ready(root);
  choose(root, "Number of players", "2");
  choose(root, "Player 1 type", "agent");
  choose(root, "Player 2 type", "agent");
  press(root, "Create and watch");
  await vi.waitFor(() => expect(root.querySelector(".bc-game")).not.toBeNull());
  const board = root.querySelector(".bc-game")!, canvas = root.querySelector("canvas");
  const t = lastTable();
  server.join(t, 0);
  await until(() => expect(overlay(root)!.textContent).toContain("Free seats: 2 (agent)"));
  expect(server.calls("/start")).toHaveLength(0);
  server.join(t, 1);
  await until(() => expect(t.status).toBe("playing"));
  for (let i = 0; i < 6; i++) document.dispatchEvent(new Event("visibilitychange"));
  await until(() => expect(root.querySelector(".bc-status")!.textContent).toMatch(/^Round [0-9]+/));
  // The creator's click armed the server's start once; the start itself happened on the server.
  expect(server.calls("/autostart")).toHaveLength(1);
  expect(server.calls("/autostart")[0].auth).toBe("Bearer " + t.creator);
  expect(server.calls("/autostart")[0].body).toEqual({ on: true });
  expect(server.calls("/start")).toHaveLength(0);
  expect(server.serverStarts()).toBe(1);
  expect(root.querySelector(".bc-game")).toBe(board);
  expect(root.querySelector("canvas")).toBe(canvas);
  expect(root.querySelector(".bc-lobby-overlay")).toBeNull();
  expect(root.querySelector(".bc-interaction button:not(:disabled)")).toBeNull();
});

it("lobbyFlow a creator's table without the automatic start offers Start now, sending one creator-authorized request", async () => {
  const t = server.add("dice_circuit", ["agent", "agent"], { visibility: "listed" });
  new SessionStore(location.origin).save({ table: t.id, creator: t.creator, gameId: t.game_id });
  history.replaceState(null, "", "/#/table/" + t.id);
  const { root } = open();
  await inLobby(root);
  expect(overlay(root)!.querySelector('[aria-label="Start automatically when every seat is ready"]')).toBeNull();
  const now = () => [...overlay(root)!.querySelectorAll("button")].find(b => b.textContent === "Start now")!;
  await vi.waitFor(() => expect(now().disabled).toBe(true));
  server.join(t, 0);
  server.join(t, 1);
  await until(() => expect(now().disabled).toBe(false));
  expect(server.calls("/start")).toHaveLength(0);
  press(root, "Start now");
  await until(() => expect(t.status).toBe("playing"));
  expect(server.calls("/start")).toHaveLength(1);
  expect(server.calls("/start")[0].auth).toBe("Bearer " + t.creator);
});

it.each([409, 503])("lobbyFlow a stale or failed Start now (%s) is re-read without a retry storm", async status => {
  const t = server.add("dice_circuit", ["agent", "agent"], { visibility: "listed" });
  new SessionStore(location.origin).save({ table: t.id, creator: t.creator, gameId: t.game_id });
  server.conflictOnStart(status);
  history.replaceState(null, "", "/#/table/" + t.id);
  const { root } = open();
  await inLobby(root);
  server.join(t, 0);
  server.join(t, 1);
  await until(() => expect([...overlay(root)!.querySelectorAll("button")].find(b => b.textContent === "Start now")?.disabled).toBe(false));
  press(root, "Start now");
  await until(() => expect(server.calls("/start")).toHaveLength(1));
  for (let i = 0; i < 5; i++) { document.dispatchEvent(new Event("visibilitychange")); await new Promise(resolve => setTimeout(resolve, 30)); }
  expect(server.calls("/start")).toHaveLength(1);
  if (status === 409) await until(() => expect(root.querySelector(".bc-status")!.textContent).toMatch(/^Round [0-9]+/));
  if (status === 409) expect(root.querySelector(".bc-app-error")).toBeNull();
});

it("lobbyFlow solo human Create & sit keeps the old play flow and reaches the game", async () => {
  const { root } = open();
  await ready(root);
  choose(root, "Number of players", "1");
  press(root, "Create and sit");
  await until(() => expect(root.querySelector(".bc-status")!.textContent).toMatch(/Round 1/));
  // Create with the seat's name ("Player" as in the shared design), arm the server's start, sit down.
  expect(server.log.filter(r => r.method === "POST").map(r => r.path.split("/").at(-1))).toEqual(["tables", "autostart", "join"]);
  expect(server.calls("/api/tables")[0].body.seat_names).toEqual(["Player"]);
  expect(server.calls("/join")[0].body).toEqual({ seat: 0 });
});

it("lobbyFlow recovers owner capability for a reloaded table and scopes sessions to the API origin", async () => {
  const { root, app } = open();
  await ready(root);
  choose(root, "Number of players", "2");
  choose(root, "Player 1 type", "agent");
  choose(root, "Player 2 type", "agent");
  press(root, "Create and watch");
  await inLobby(root);
  const t = lastTable();
  expect(location.hash).toBe("#/table/" + t.id);
  localStorage.setItem("boardcraft.sessions.v1", JSON.stringify({ ...JSON.parse(localStorage.getItem("boardcraft.sessions.v1")!), "http://other.example:9": [{ table: hex(7), creator: "other-origin-creator" }] }));
  app.dispose();
  sessionStorage.clear();
  const again = open();
  await inLobby(again.root);
  expect(again.root.textContent).not.toContain("other-origin-creator");
  history.replaceState(null, "", "/");
  apps.pop()!.dispose();
  // The room knows the table as this browser's own: its row offers Resume (there is no separate "Your tables" panel).
  const room = open();
  await vi.waitFor(() => expect(room.root.querySelector(`[aria-label="Resume Dice Circuit table ${t.id.slice(0, 8)}"]`)).not.toBeNull());
  expect(room.root.querySelector(".bc-my-tables")).toBeNull();
  expect(room.root.innerHTML).not.toContain(t.creator);
  expect(room.root.innerHTML).not.toContain("other-origin-creator");
});

it("lobbyFlow migrates the legacy last table without exposing credentials", async () => {
  const table = hex(99);
  localStorage.setItem("boardcraft.recent", JSON.stringify({ table, gameId: "dice_circuit", credential: "legacy-seat-secret", seat: 0 }));
  const { root } = open();
  await vi.waitFor(() => expect(root.querySelector(".bc-tables")).not.toBeNull());
  expect(root.querySelector(".bc-my-tables")).toBeNull();
  expect(root.innerHTML).not.toContain("legacy-seat-secret");
  expect(sessions()).toEqual([expect.objectContaining({ table, credential: "legacy-seat-secret" })]);
  new SessionStore(location.origin).forget(table);
  expect(sessions()).toEqual([]);
});

it("lobbyFlow storage failure leaves the in-memory table usable and explains how to keep the invitation", async () => {
  const full = { ...memoryStorage(), setItem: () => { throw new DOMException("full", "QuotaExceededError"); } };
  vi.stubGlobal("localStorage", full);
  vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => { throw new DOMException("full", "QuotaExceededError"); });
  const { root } = open();
  await ready(root);
  choose(root, "Number of players", "2");
  choose(root, "Player 1 type", "agent");
  choose(root, "Player 2 type", "human");
  press(root, "Create and sit");
  await inLobby(root);
  expect(root.textContent).toMatch(/could not save this table in this browser/i);
  expect(root.textContent).not.toMatch(/Copy invitation link/);
  expect(location.hash).toBe(`#/table/${lastTable().id}?seat=1`);
  const t = lastTable();
  server.join(t, 0);
  await until(() => expect(t.status).toBe("playing"));
  expect(server.calls("/autostart")).toHaveLength(1);
  expect(server.calls("/start")).toHaveLength(0);
});

it("lobbyFlow an observer or ownerless visitor has no start control and is told who starts", async () => {
  const t = server.add("rentcraft", ["human", "agent"], { visibility: "listed" });
  history.replaceState(null, "", "/#table=" + t.id);
  const { root } = open();
  await inLobby(root);
  await vi.waitFor(() => expect(overlay(root)!.textContent).toContain("Free seats: 1 (human), 2 (agent)"));
  expect([...root.querySelectorAll("button")].map(b => b.textContent)).not.toContain("Start now");
  expect(root.querySelector('[aria-label="Start automatically when every seat is ready"]')).toBeNull();
  expect(server.calls("/start")).toHaveLength(0);
});

it("lobbyFlow disposing before the lazy game view loads is safe", async () => {
  const t = server.add("dice_circuit", ["human"], { visibility: "listed" });
  history.replaceState(null, "", "/#table=" + t.id);
  const { root, app } = open();
  await vi.waitFor(() => expect(server.log.some(r => r.path.endsWith("/view"))).toBe(true));
  app.dispose();
  await new Promise(resolve => setTimeout(resolve, 100));
  expect(root.childElementCount).toBe(0);
});

it("lobbyFlow the waiting window is centred over the board inside the full-screen stage and leads back to Play", async () => {
  const t = server.add("dice_circuit", ["human"], { visibility: "listed" });
  history.replaceState(null, "", "/#table=" + t.id);
  const { root } = open();
  await vi.waitFor(() => expect(root.querySelector(".bc-lobby-overlay")).not.toBeNull());
  const stage = root.querySelector(".bc-stage")!;
  expect(stage.contains(root.querySelector(".bc-lobby-overlay"))).toBe(true);
  expect(stage.contains(root.querySelector(".bc-game"))).toBe(true);
  expect(root.querySelector(".bc-lobby-overlay")!.parentElement!.matches(".modal.soft")).toBe(true);
  expect(root.querySelector(".bc-lobby-overlay h3")!.textContent).toBe("Waiting for players");
  expect([...root.querySelectorAll("button")].some(b => /table panel/.test(b.getAttribute("aria-label") ?? b.textContent ?? ""))).toBe(false);
  press(root, "Back to Play");
  await vi.waitFor(() => expect(root.querySelector(".bc-tables")).not.toBeNull());
  expect(location.hash).toBe("#/play");
});
