import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { mountPlayApp } from "../src/app";
import { SessionStore } from "../src/lobby";
import { mountAgentHelp } from "../src/agent-help";
import sheet from "../../src/boardcraft/games/dice_circuit/data/base-sheet.json";

// Boundary regressions for the room, seat claims, start requests and styling. Every value is synthetic.
type Seat = { seat: number; kind: string; occupied: boolean; display_name: string };
interface Table { id: string; game_id: string; players: number; status: string; seats: Seat[]; visibility: string; creator: string }
const hex = (n: number) => n.toString(16).padStart(32, "0");
const games = [
  { game_id: "dice_circuit", name: "Dice Circuit", description: "Roll six dice.", variants: ["base"], min_players: 1, max_players: 4 },
  { game_id: "rentcraft", name: "Rentcraft", description: "Buy streets.", variants: ["base"], min_players: 2, max_players: 4 },
];
const bots = [{ choice_id: "greedy", display_name: "Greedy bot", kind: "builtin", reference: {} }];
const diceData = (players: number) => ({ round: 1, active_player: 0, phase: "passive", regular_opportunity: 0, event_cursor: 0,
  dice: Object.fromEntries(["yellow", "blue", "green", "orange", "purple", "white"].map(id => [id, { value: 2, zone: "tray" }])),
  sheets: Array.from({ length: players }, () => ({ yellow: sheet.yellow.premarked, blue: [], green: 0, orange: [], purple: [], earned_rewards: [], rerolls: 0, extras: 0, emblem_count: 0 })) });

function fakeServer() {
  const tables = new Map<string, Table>();
  const log: { method: string; path: string; body?: any; auth: string | null }[] = [];
  let counter = 0, startMode: "normal" | "ack-only" = "normal";
  const json = (value: unknown, status = 200) => new Response(JSON.stringify(value), { status, headers: { "Content-Type": "application/json" } });
  const add = (game_id: string, kinds: string[], extra: Partial<Table> = {}) => {
    const id = hex(++counter + 4096);
    tables.set(id, { id, game_id, players: kinds.length, status: "lobby", visibility: "invite", creator: "creator-" + id.slice(-4),
      seats: kinds.map((kind, seat) => ({ seat, kind, occupied: !["human", "agent"].includes(kind), display_name: kind })), ...extra });
    return tables.get(id)!;
  };
  const handler = async (path: string, init: RequestInit = {}) => {
    const url = new URL(path, "http://server.test"), method = init.method ?? "GET", auth = new Headers(init.headers).get("Authorization");
    const body = init.body ? JSON.parse(String(init.body)) : undefined;
    log.push({ method, path, body, auth });
    const p = url.pathname;
    let m: RegExpMatchArray | null;
    if (p === "/api/games") return json(games);
    if (/\/bots$/.test(p)) return json(bots);
    if (/\/settings$/.test(p)) return json({ table: {}, live: {}, bots: {}, simulation_defaults: {} });
    if (/\/board$/.test(p)) return json(sheet);
    if (p.endsWith("/rules")) return new Response("Rules", { headers: { "Content-Type": "text/plain" } });
    if (p === "/api/tables" && method === "GET") {
      const listed = [...tables.values()].filter(t => t.visibility === "listed");
      return json({ items: listed.map(t => ({ table_id: t.id, game_id: t.game_id, variant: "base", players: t.players, status: t.status, seats: t.seats })), total: listed.length, offset: 0, limit: 10 });
    }
    if (p === "/api/tables" && method === "POST") {
      const t = add(body.config.game_id, body.seats.map((s: any) => typeof s === "string" ? s : s.choice_id));
      return json({ table_id: t.id, creator_credential: t.creator });
    }
    if ((m = p.match(/^\/api\/tables\/(\w+)\/(\w+)$/))) {
      const t = tables.get(m[1]);
      if (!t) return new Response("", { status: 404 });
      if (m[2] === "join") { t.seats[body.seat].occupied = true; return json({ credential: "seat-" + body.seat, seat: body.seat }); }
      if (m[2] === "start") {
        if (auth !== "Bearer " + t.creator) return new Response("", { status: 403 });
        if (startMode === "normal") t.status = "playing";
        return json({});
      }
      if (m[2] === "view") {
        const base = { table_id: t.id, status: t.status, config: { game_id: t.game_id, variant: "base", players: t.players }, seats: t.seats.map(s => ({ ...s })) };
        if (t.status === "lobby") return json({ ...base, observation: null });
        const viewer = auth?.startsWith("Bearer seat-") ? Number(auth.slice(12)) : null;
        return json({ ...base, revision: 1, event_cursor: 0, observation: { game_id: t.game_id, viewer, revision: 1, decision: null, data: diceData(t.players) } });
      }
      if (m[2] === "actions") return json({ decision_id: null, actions: [] });
      if (m[2] === "replay") return json({ observation: { game_id: t.game_id, viewer: null, revision: 0, decision: null, data: diceData(t.players) }, events: [], position: 0, total_positions: 3 });
    }
    return json({});
  };
  const calls = (suffix: string) => log.filter(r => r.method === "POST" && r.path.endsWith(suffix));
  return { tables, add, log, handler, calls, ackOnlyStart: () => { startMode = "ack-only"; } };
}

let apps: { dispose(): void }[] = [], server: ReturnType<typeof fakeServer>;
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
  localStorage.clear();
  vi.unstubAllGlobals();
  sessionStorage.clear();
  history.replaceState(null, "", "/");
  document.head.querySelectorAll("style[data-test-css]").forEach(s => s.remove());
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
const overlay = (root: HTMLElement) => root.querySelector<HTMLElement>(".bc-lobby-overlay");
const inLobby = (root: HTMLElement) => vi.waitFor(() => expect(overlay(root)).not.toBeNull());
const sessions = (origin = location.origin) => JSON.parse(localStorage.getItem("boardcraft.sessions.v1") ?? "{}")[origin] ?? [];
const tabTable = () => JSON.parse(sessionStorage.getItem("boardcraft.session") || "null")?.table;
const bearers = (table: string) => server.log.filter(r => r.path.includes(table) && r.auth);

it("lobby-boundaries: a seat credential saved for one API origin is never sent to another", async () => {
  const t = server.add("dice_circuit", ["human"]);
  new SessionStore("http://a.invalid").save({ table: t.id, credential: "fixture-seat-A", seat: 0, creator: "fixture-owner-A" });
  history.replaceState(null, "", "/#table=" + t.id);
  open({ apiBaseUrl: "http://b.invalid" });
  await vi.waitFor(() => expect(server.log.some(r => r.path.endsWith("/view"))).toBe(true));
  expect(server.log.filter(r => r.auth)).toEqual([]);
});

it("lobby-boundaries: the same API origin still recovers its own owner and seat", async () => {
  const t = server.add("dice_circuit", ["human"]);
  new SessionStore(location.origin).save({ table: t.id, credential: "fixture-seat-A", seat: 0 });
  history.replaceState(null, "", "/#table=" + t.id);
  open();
  await vi.waitFor(() => expect(bearers(t.id).some(r => r.auth === "Bearer fixture-seat-A")).toBe(true));
});

it("lobby-boundaries: an unscoped legacy record is not given to a foreign API and is claimed only explicitly", async () => {
  const table = hex(7);
  localStorage.setItem("boardcraft.recent", JSON.stringify({ table, creator: "fixture-owner-A", gameId: "dice_circuit" }));
  expect(new SessionStore("http://b.invalid").get(table)?.creator).toBeUndefined();
  const { root } = open({ apiBaseUrl: "http://b.invalid" });
  await vi.waitFor(() => expect(root.querySelector(".bc-tables")).not.toBeNull());
  // The room has only Games, Tables and New table (as in the shared design): no "Your tables" or invitation-link panel.
  expect(root.querySelector(".bc-my-tables, .bc-join")).toBeNull();
  expect(root.innerHTML).not.toContain("fixture-owner-A");
  expect(sessions("http://b.invalid")).toEqual([]);
  new SessionStore("http://b.invalid").claimLegacy();
  expect(sessions("http://b.invalid")).toEqual([expect.objectContaining({ table, creator: "fixture-owner-A" })]);
  // A page that talks to its own origin keeps the old one-step recovery.
  localStorage.setItem("boardcraft.recent", JSON.stringify({ table: hex(8), creator: "fixture-owner-B" }));
  expect(new SessionStore(location.origin).get(hex(8))?.creator).toBe("fixture-owner-B");
});

it("lobby-boundaries: forgetting clears the tab copy and a stale Resume row cannot resurrect it", async () => {
  const t = server.add("dice_circuit", ["human"], { visibility: "listed" });
  const store = new SessionStore(location.origin);
  store.save({ table: t.id, credential: "seat-0", seat: 0, gameId: "dice_circuit" });
  store.forget(t.id);
  expect(tabTable()).not.toBe(t.id);
  // Another tab forgets the table after this row was drawn; its Resume button must not use the old capability.
  new SessionStore(location.origin).save({ table: t.id, credential: "seat-0", seat: 0, gameId: "dice_circuit" });
  const second = open();
  const resume = `Resume Dice Circuit table ${t.id.slice(0, 8)}`;
  await vi.waitFor(() => expect(second.root.querySelector(`[aria-label="${resume}"]`)).not.toBeNull());
  new SessionStore(location.origin).forget(t.id);
  server.log.length = 0;
  press(second.root, resume);
  await new Promise(resolve => setTimeout(resolve, 120));
  expect(bearers(t.id)).toEqual([]);
  // The table opens as a watcher: no capability is restored for this tab.
  expect(JSON.parse(sessionStorage.getItem("boardcraft.session") || "null")?.credential).toBeUndefined();
});

it("lobby-boundaries: Sit down claims only the first free human seat once and keeps its credential", async () => {
  const t = server.add("dice_circuit", ["human", "human"], { visibility: "listed" });
  const { root } = open();
  const sit = `Sit down Dice Circuit table ${t.id.slice(0, 8)}`;
  await vi.waitFor(() => expect(root.querySelector(`[aria-label="${sit}"]`)).not.toBeNull());
  press(root, sit);
  await inLobby(root);
  await vi.waitFor(() => expect(sessions()[0]?.credential).toBe("seat-0"));
  // Later polls of the waiting window never claim a second seat.
  await until(() => expect(overlay(root)!.textContent).toContain("Free seats: 2 (human)"));
  expect(server.calls("/join").map(r => r.body.seat)).toEqual([0]);
  expect(location.hash).toBe(`#/table/${t.id}?seat=0`);
});

it("lobby-boundaries: create & sit keeps the seat that was submitted and always arms the automatic start", async () => {
  const { root } = open();
  await ready(root);
  choose(root, "Game", "rentcraft");
  await ready(root);
  choose(root, "Player 1 type", "empty");
  choose(root, "Player 2 type", "human");
  choose(root, "Player 3 type", "greedy");
  expect(field(root, "Your seat")).toBeNull();
  expect(field(root, "Start automatically when every seat is ready")).toBeNull();
  let release!: () => void;
  const gate = new Promise<void>(resolve => { release = resolve; });
  vi.stubGlobal("fetch", async (path: string, init?: RequestInit) => {
    if (init?.method === "POST" && path === "/api/tables") await gate;
    return server.handler(path, init);
  });
  press(root, "Create and sit");
  // A change made while the request is in flight does not move the seat that was submitted.
  choose(root, "Player 1 type", "human");
  release();
  await vi.waitFor(() => expect(server.calls("/join")).toHaveLength(1));
  expect(server.calls("/join")[0].body.seat).toBe(0);
  expect(server.calls("/autostart")).toHaveLength(1);
  await vi.waitFor(() => expect(sessions()[0]?.autostart).toBe(true));
});

it("lobby-boundaries: a reload while a start is unconfirmed never sends a second start by itself", async () => {
  const t = server.add("dice_circuit", ["greedy"]);
  new SessionStore(location.origin).save({ table: t.id, creator: t.creator, gameId: t.game_id });
  history.replaceState(null, "", "/#table=" + t.id);
  let attempts = 0, release!: () => void;
  const gate = new Promise<void>(resolve => { release = resolve; });
  vi.stubGlobal("fetch", async (path: string, init?: RequestInit) => {
    if (init?.method === "POST" && path.endsWith("/start")) { attempts++; await gate; }
    return server.handler(path, init);
  });
  try {
    const first = open();
    // The creator presses Start now (the browser never starts a table by itself).
    await inLobby(first.root);
    await vi.waitFor(() => expect([...first.root.querySelectorAll("button")].find(b => b.textContent === "Start now")?.disabled).toBe(false));
    press(first.root, "Start now");
    await vi.waitFor(() => expect(attempts).toBe(1));
    first.app.dispose();
    const second = open();
    await inLobby(second.root);
    await new Promise(resolve => setTimeout(resolve, 150));
    expect(attempts).toBe(1);
    expect(second.root.textContent).toMatch(/start was requested but not confirmed/i);
    const now = [...second.root.querySelectorAll("button")].find(b => b.textContent === "Start now")!;
    expect(now.disabled).toBe(false);
  } finally { release(); }
});

it("lobby-boundaries: an old visit finishing its start cannot reopen the controls of a new visit", async () => {
  const a = server.add("dice_circuit", ["greedy"], { visibility: "listed" }), b = server.add("dice_circuit", ["greedy"], { visibility: "listed" });
  const store = new SessionStore(location.origin);
  store.save({ table: a.id, creator: a.creator });
  store.save({ table: b.id, creator: b.creator });
  history.replaceState(null, "", "/#table=" + a.id);
  let releaseA!: () => void, releaseB!: () => void;
  const gateA = new Promise<void>(resolve => { releaseA = resolve; }), gateB = new Promise<void>(resolve => { releaseB = resolve; });
  let heldA = 0, heldB = 0;
  vi.stubGlobal("fetch", async (path: string, init?: RequestInit) => {
    if (init?.method === "POST" && path.endsWith(`/${a.id}/start`)) { heldA++; await gateA; }
    if (init?.method === "POST" && path.endsWith(`/${b.id}/start`)) { heldB++; await gateB; }
    return server.handler(path, init);
  });
  const { root } = open();
  await inLobby(root);
  await vi.waitFor(() => expect([...root.querySelectorAll("button")].find(b => b.textContent === "Start now")?.disabled).toBe(false));
  press(root, "Start now");
  await vi.waitFor(() => expect(heldA).toBe(1));
  // Same application, new route: leave table A through the board's back button and resume table B.
  await vi.waitFor(() => expect([...root.querySelectorAll("button")].some(x => x.textContent === "← Play")).toBe(true));
  press(root, "← Play");
  // Both synthetic tables share an ID prefix, so table B is the last Resume row of the table list (A is listed first).
  await vi.waitFor(() => expect(root.querySelectorAll('.bc-table-list [aria-label^="Resume"]')).toHaveLength(2));
  [...root.querySelectorAll<HTMLButtonElement>('.bc-table-list [aria-label^="Resume"]')].at(-1)!.click();
  await inLobby(root);
  const now = () => [...root.querySelectorAll("button")].find(x => x.textContent === "Start now")!;
  await vi.waitFor(() => expect(now().disabled).toBe(false));
  now().click();
  await vi.waitFor(() => expect(now().disabled).toBe(true));
  releaseA();
  await new Promise(resolve => setTimeout(resolve, 100));
  expect(now().disabled).toBe(true);
  expect(heldB).toBe(1);
  releaseB();
});

it("lobby-boundaries: an acknowledged start disables the manual button until the table changes", async () => {
  server.ackOnlyStart();
  const t = server.add("dice_circuit", ["greedy"]);
  new SessionStore(location.origin).save({ table: t.id, creator: t.creator });
  history.replaceState(null, "", "/#table=" + t.id);
  const { root } = open();
  await inLobby(root);
  const now = () => [...root.querySelectorAll("button")].find(x => x.textContent === "Start now")!;
  await vi.waitFor(() => expect(now().disabled).toBe(false));
  now().click();
  await vi.waitFor(() => expect(server.calls("/start")).toHaveLength(1));
  await until(() => expect(now().disabled).toBe(true));
  expect(server.calls("/start")).toHaveLength(1);
});

it("lobby-boundaries: polling an unchanged roster keeps focus on the prompt and its Copy button", () => {
  const parent = document.createElement("section");
  document.body.append(parent);
  const help = mountAgentHelp(parent, { origin: location.origin, gameId: "dice_circuit", gameName: "Dice Circuit", table: hex(6) });
  const seats = [{ seat: 0, kind: "agent", occupied: false }, { seat: 1, kind: "agent", occupied: false }];
  help.update(seats);
  const area = parent.querySelector("textarea")!, copy = parent.querySelector<HTMLButtonElement>("button")!;
  area.focus();
  help.update(seats);
  expect(document.activeElement).toBe(area);
  copy.focus();
  help.update(seats);
  expect(document.activeElement).toBe(copy);
  help.update([seats[1]]);
  expect(parent.querySelectorAll("textarea[data-agent-prompt]")).toHaveLength(1);
});

it("lobby-boundaries: Join, Watch and Review lead to different screens", async () => {
  const lobby = server.add("dice_circuit", ["human", "human"], { visibility: "listed" });
  const done = server.add("dice_circuit", ["human"], { visibility: "listed", status: "completed" });
  const rowButton = (root: HTMLElement, label: string, id: string) => root.querySelector<HTMLButtonElement>(`[aria-label="${label} Dice Circuit table ${id.slice(0, 8)}"]`)!;
  const join = open();
  await vi.waitFor(() => expect(rowButton(join.root, "Sit down", lobby.id)).toBeTruthy());
  rowButton(join.root, "Sit down", lobby.id).click();
  await inLobby(join.root);
  // Sit down takes the first free human seat (as in the shared design); the window lists the seats still free.
  await vi.waitFor(() => expect(server.calls("/join").map(r => r.body.seat)).toEqual([0]));
  await until(() => expect(overlay(join.root)!.textContent).toContain("Free seats: 2 (human)"));
  apps.pop()!.dispose();
  history.replaceState(null, "", "/");
  // Another browser (nothing remembered) watches the same table.
  localStorage.clear();
  sessionStorage.clear();
  const watch = open();
  await vi.waitFor(() => expect(rowButton(watch.root, "Watch", lobby.id)).toBeTruthy());
  rowButton(watch.root, "Watch", lobby.id).click();
  await inLobby(watch.root);
  await until(() => expect(overlay(watch.root)!.textContent).toContain("Free seats: 2 (human)"));
  await new Promise(resolve => setTimeout(resolve, 100));
  expect(server.calls("/join")).toHaveLength(1);
  apps.pop()!.dispose();
  history.replaceState(null, "", "/");
  const review = open();
  await vi.waitFor(() => expect(rowButton(review.root, "Open", done.id)).toBeTruthy());
  rowButton(review.root, "Open", done.id).click();
  await vi.waitFor(() => expect(review.root.querySelector(".tc-replay")?.textContent).toContain("decision 1 / 3"));
});

async function withStyles() {
  // The stylesheet is read from disk: the test runner replaces CSS imports with empty text.
  const module = "node:fs";
  const fs: any = await import(/* @vite-ignore */ module);
  const css: string = fs.readFileSync(`${(globalThis as any).process.cwd()}/src/style.css`, "utf8");
  const style = document.createElement("style");
  style.dataset.testCss = "";
  style.textContent = css;
  document.head.append(style);
}
const channels = (color: string) => color.match(/\d+/g)!.slice(0, 3).map(Number);
const luminance = (color: string) => channels(color).map(c => { const s = c / 255; return s <= .03928 ? s / 12.92 : ((s + .055) / 1.055) ** 2.4; }).reduce((sum, c, i) => sum + c * [.2126, .7152, .0722][i], 0);
const contrast = (a: string, b: string) => { const [hi, lo] = [luminance(a), luminance(b)].sort((x, y) => y - x); return (hi + .05) / (lo + .05); };

it("lobby-boundaries: fields marked hidden stay hidden under the real stylesheet", async () => {
  await withStyles();
  // jsdom does not apply the author cascade like a browser does, so also pin the rule that wins in a browser:
  // an important `[hidden]` rule must cover every author `display` that targets form labels.
  const css = document.head.querySelector<HTMLStyleElement>("style[data-test-css]")!.textContent!;
  expect(css).toMatch(/\.bc-app\s+\[hidden\]\s*\{[^}]*display:\s*none\s*!important/);
  const { root } = open();
  await ready(root);
  const label = (text: string) => [...root.querySelectorAll<HTMLElement>(".bc-create label")].find(l => l.textContent!.startsWith(text))!;
  for (const text of ["Variant", "Players"]) {
    expect(label(text).hidden, text).toBe(true);
    expect(getComputedStyle(label(text)).display, text).toBe("none");
  }
  expect(label("Your seat")).toBeUndefined();

});

it("lobby-boundaries: selected game cards expose their selected state independently of text colour", async () => {
  await withStyles();
  const { root } = open(); await ready(root);
  const selected = root.querySelector<HTMLElement>('.bc-game-card[aria-pressed="true"]')!;
  expect(selected.textContent).toBe("Dice Circuit");
  expect(root.querySelectorAll('.bc-game-card[aria-pressed="true"]')).toHaveLength(1);
  expect(root.querySelector('.bc-game-card[aria-pressed="false"]')!.textContent).toBe("Rentcraft");
  const module = "node:fs";
  const css = { default: (await import(/* @vite-ignore */ module)).readFileSync(`${(globalThis as any).process.cwd()}/src/ui/shell.css`, "utf8") };
  expect(css.default).toMatch(/bc-game-card\[aria-pressed="true"\][^{]*\{[^}]*box-shadow:[^}]*2px/);
});
