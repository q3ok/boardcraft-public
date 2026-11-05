import { afterEach, expect, it, vi } from "vitest";
import { mountRoom, SessionStore, type RoomHost } from "../src/lobby";
import { renderNavigation } from "../src/navigation";
import { mountPlayApp } from "../src/app";

const games = [{ game_id: "dice_circuit", name: "Dice Circuit", variants: ["base"], min_players: 1, max_players: 4 }, { game_id: "rentcraft", name: "Rentcraft", variants: ["base"], min_players: 2, max_players: 4 }];
const descriptors = [
  { choice_id: "strategist", display_name: "Strategist", description: "Plans bonus chains.", kind: "builtin", reference: {} },
  ...["mini-evo5", "njord-gen0", "hodr"].map(name => ({ choice_id: name, display_name: name, kind: "model", reference: {} })),
];
const disposers: (() => void)[] = [];
afterEach(() => { disposers.splice(0).forEach(dispose => dispose()); document.body.replaceChildren(); vi.restoreAllMocks(); vi.unstubAllGlobals(); });
function setup(extra: Partial<RoomHost> = {}) {
  const requests: { path: string; body: any }[] = [];
  const root = document.createElement("div"); document.body.append(root);
  const opened = vi.fn();
  const request = vi.fn(async (path: string, body?: any) => {
    requests.push({ path, body });
    if (path === "/api/games") return games;
    if (path.endsWith("/bots")) return descriptors;
    if (path.endsWith("/settings")) return { table: {}, live: {}, bots: {} };
    if (path.endsWith("/rules")) return "# Real rules\nRead this game's rules.";
    if (body) return path.endsWith("/join") ? { credential: "seat" } : { table_id: "a".repeat(32), creator_credential: "owner" };
    const q = new URL(path, "http://localhost").searchParams;
    const offset = Number(q.get("offset")), limit = Number(q.get("limit"));
    return { total: 57, offset, limit, items: Array.from({ length: Math.min(limit, 57 - offset) }, (_, i) => ({ table_id: (i + offset + 1).toString(16).padStart(32, "0"), game_id: "dice_circuit", status: "lobby", players: 2, seats: [{ seat: 0, kind: "human", occupied: false, display_name: "Human" }] })) };
  });
  const room = mountRoom(root, { origin: location.origin, store: new SessionStore(location.origin), request: request as any, alive: () => true, open: opened, go: vi.fn(), fail: e => { throw Error(e); }, clearError: vi.fn(), ...extra });
  disposers.push(() => room.dispose());
  return { root, requests, opened, request };
}
const choose = (root: HTMLElement, label: string, value: string) => { const input = root.querySelector<HTMLSelectElement>(`[aria-label="${label}"]`)!; input.value = value; input.dispatchEvent(new Event("change")); };
const press = (root: HTMLElement, text: string) => { const b = [...root.querySelectorAll("button")].find(b => b.textContent === text || b.getAttribute("aria-label") === text)!; expect(b, text).toBeTruthy(); b.click(); };

it("first paint puts games and tables before an unselected new-table form", async () => {
  const { root, requests } = setup();
  await vi.waitFor(() => expect(root.querySelectorAll(".bc-table-list .bc-table-row")).toHaveLength(12));
  expect(root.querySelector(".bc-room-grid")!.firstElementChild!.querySelector(".bc-games")).toBeTruthy();
  expect(root.querySelector(".bc-create")!.textContent).toContain("Pick a game");
  expect(requests.some(r => r.path.endsWith("/bots"))).toBe(false);
  expect(root.querySelectorAll(".bc-table-head svg")).toHaveLength(12);
  expect(root.querySelector('[name="visibility"]')).toBeNull();
});

it("shared pager handles 57 real summaries and resets the server offset on filters", async () => {
  const { root, requests } = setup();
  await vi.waitFor(() => expect(root.textContent).toContain("page 1 of 5"));
  for (let n = 1; n <= 4; n++) { press(root, "Next page"); await vi.waitFor(() => expect(root.textContent).toContain(`page ${n + 1} of 5`)); }
  expect(root.querySelectorAll(".bc-table-list .bc-table-row")).toHaveLength(9);
  expect(root.querySelector<HTMLButtonElement>('[aria-label="Next page"]')!.disabled).toBe(true);
  choose(root, "Filter by game", "rentcraft");
  await vi.waitFor(() => expect(requests.at(-1)!.path).toContain("game_id=rentcraft"));
  expect(requests.at(-1)!.path).toContain("offset=0&limit=12");
});

it("chosen seats use real catalog IDs and explicit listed visibility", async () => {
  const { root, requests, opened } = setup();
  await vi.waitFor(() => expect(root.querySelector('[aria-label="Game"]')).toBeTruthy());
  choose(root, "Game", "dice_circuit");
  await vi.waitFor(() => expect(root.querySelector('[aria-label="Player 1 type"] option[value="strategist"]')).toBeTruthy());
  choose(root, "Number of players", "2");
  choose(root, "Player 2 type", "hodr");
  press(root, "Create and sit");
  await vi.waitFor(() => expect(opened).toHaveBeenCalled());
  const made = requests.find(r => r.path === "/api/tables" && r.body)!.body;
  expect(made.visibility).toBe("listed");
  expect(made.seats).toEqual(["human", "hodr"]);
  expect(requests.find(r => r.path.endsWith("/join"))!.body).toEqual({ seat: 0 });
});

it("agent setup and rules open accessible dialogs from the actual server", async () => {
  const { root } = setup();
  await vi.waitFor(() => expect(root.querySelector('[aria-label="Game"]')).toBeTruthy());
  choose(root, "Game", "dice_circuit");
  await vi.waitFor(() => expect(root.querySelector('[aria-label="Player 1 type"]')).toBeTruthy());
  choose(root, "Player 1 type", "agent");
  // As in the shared design: the agent seat explains itself in a box; there is no separate "Agent help" dialog.
  expect(root.querySelector(".bc-seat-card .mcpbox")!.textContent).toBe("The agent joins after the table is created. You get a ready sentence to paste to the agent.");
  expect([...root.querySelectorAll("button")].some(b => b.textContent === "Agent help")).toBe(false);
  press(root, "Dice Circuit rules");
  await vi.waitFor(() => expect(document.querySelector('[role="dialog"]')!.textContent).toContain("Real rules"));
});

it("shell has its pages, the colour menu and Rules, and accepts host sections", () => {
  const go = vi.fn(), rules = vi.fn(), selectSection = vi.fn();
  const nav = renderNavigation({ current: "play", go, rules, sections: [{ id: "training", label: "🧠 Trening" }], selectSection }); document.body.append(nav);
  expect([...nav.querySelectorAll("nav button")].map(b => b.textContent)).toEqual(["🎲 Play", "⚔ Tournaments", "🏆 Rankings", "▶ Replays", "🧠 Trening"]);
  expect([...nav.children].map(c => c.className.split(" ")[0] || c.tagName)).toEqual(["brand", "NAV", "palette-menu", "bc-nav-rules"]);
  press(nav, "▶ Replays"); expect(go).toHaveBeenCalledWith("replays");
  press(nav, "🧠 Trening"); expect(selectSection).toHaveBeenCalledWith("training");
  press(nav, "📖 Rules"); expect(rules).toHaveBeenCalled();
});

it("empty seats are omitted and the chosen human uses its compact API seat", async () => {
  const { root, requests, opened } = setup();
  await vi.waitFor(() => expect(root.querySelector('[aria-label="Game"]')).toBeTruthy());
  choose(root, "Game", "rentcraft");
  await vi.waitFor(() => expect(root.querySelector('[aria-label="Player 2 type"] option[value="strategist"]')).toBeTruthy());
  choose(root, "Number of players", "4");
  choose(root, "Player 1 type", "empty");
  choose(root, "Player 2 type", "human");
  choose(root, "Player 3 type", "empty");
  choose(root, "Player 4 type", "strategist");
  press(root, "Create and sit");
  await vi.waitFor(() => expect(opened).toHaveBeenCalled());
  expect(requests.find(r => r.path === "/api/tables" && r.body)!.body.seats).toEqual(["human", "strategist"]);
  expect(requests.find(r => r.path.endsWith("/join"))!.body.seat).toBe(0);
});

it("host models are discovered by game and prepared before the table request", async () => {
  const extraModels = vi.fn(async (_game: string, _signal: AbortSignal) => [{ choice_id: "host-choice", display_name: "Host model", kind: "model" as const, reference: {} }]);
  const prepareSeat = vi.fn(async (_game: string, id: string) => id === "host-choice" ? "registered-choice" : id);
  const { root, requests, opened } = setup({ extraModels, prepareSeat });
  await vi.waitFor(() => expect(root.querySelector('[aria-label="Game"]')).toBeTruthy());
  choose(root, "Game", "dice_circuit");
  await vi.waitFor(() => expect(root.querySelector('[aria-label="Player 1 type"] option[value="host-choice"]')).toBeTruthy());
  choose(root, "Number of players", "1");
  choose(root, "Player 1 type", "host-choice"); press(root, "Create and watch");
  await vi.waitFor(() => expect(opened).toHaveBeenCalled());
  expect(extraModels.mock.calls[0][0]).toBe("dice_circuit");
  expect(requests.find(r => r.path === "/api/tables" && r.body)!.body.seats).toEqual(["registered-choice"]);
});

it("too few occupied places are explained when creating, and nothing is sent", async () => {
  const fail = vi.fn();
  const { root, requests } = setup({ fail });
  await vi.waitFor(() => expect(root.querySelector('[aria-label="Game"]')).toBeTruthy());
  choose(root, "Game", "rentcraft");
  await vi.waitFor(() => expect(root.querySelector('[aria-label="Player 2 type"] option[value="strategist"]')).toBeTruthy());
  choose(root, "Number of players", "2");
  choose(root, "Player 2 type", "empty");
  press(root, "Create and sit");
  await vi.waitFor(() => expect(root.querySelector(".bc-create")!.textContent).toContain("At least 2 players must sit at the table."));
  expect(fail).toHaveBeenCalledWith("At least 2 players must sit at the table.");
  expect(requests.some(r => r.path === "/api/tables" && r.body)).toBe(false);
});

it("Dice starts with four seat cards, two players and two empty places", async () => {
  const { root } = setup();
  await vi.waitFor(() => expect(root.querySelector('[aria-label="Game"]')).toBeTruthy()); choose(root, "Game", "dice_circuit");
  await vi.waitFor(() => expect(root.querySelector('[aria-label="Player 1 type"] option[value="strategist"]')).toBeTruthy());
  expect(root.querySelectorAll(".bc-seat-card")).toHaveLength(4);
  expect([...root.querySelectorAll<HTMLSelectElement>('.bc-seat-card select[aria-label$=" type"]')].map(s => s.value)).toEqual(["human", "strategist", "empty", "empty"]);
});

it("an outer shell can mount Settings and navigate to Watch without a second header", async () => {
  const fetcher = vi.fn(async (path: string) => new Response(JSON.stringify(path === "/api/games" ? games : { items: [], total: 0, offset: 0, limit: 12 }), { headers: { "Content-Type": "application/json" } }));
  vi.stubGlobal("fetch", fetcher);
  const root = document.createElement("div"); document.body.append(root);
  const app = mountPlayApp(root, { showNavigation: false, initialPage: "settings" }); disposers.push(() => app.dispose());
  expect(root.querySelector(".bc-platform-settings")).toBeTruthy();
  expect(root.querySelector("header")).toBeNull(); expect(fetcher).not.toHaveBeenCalled();
  app.navigate("replays");
  await vi.waitFor(() => expect(root.querySelector(".bc-replays")).toBeTruthy());
  expect(root.querySelector("header")).toBeNull();
  app.navigate("watch");
  await vi.waitFor(() => expect(root.querySelector(".bc-create")).toBeTruthy());
});

it("leaving during a seat claim retains the created table and late seat capability", async () => {
  let finishJoin!: (value: any) => void;
  const store = new SessionStore(location.origin);
  const { root, request, opened } = setup({ store });
  const normal = request.getMockImplementation()!;
  request.mockImplementation(async (path, body) => path.endsWith("/join") ? new Promise<any>(resolve => { finishJoin = resolve; }) : normal(path, body));
  await vi.waitFor(() => expect(root.querySelector('[aria-label="Game"]')).toBeTruthy()); choose(root, "Game", "dice_circuit");
  await vi.waitFor(() => expect(root.querySelector('[aria-label="Player 1 type"] option[value="strategist"]')).toBeTruthy());
  press(root, "Create and sit");
  await vi.waitFor(() => expect(finishJoin).toBeTypeOf("function"));
  expect(store.get("a".repeat(32))?.creator).toBe("owner");
  disposers.splice(0).forEach(dispose => dispose());
  finishJoin({ credential: "late-seat" });
  await vi.waitFor(() => expect(store.get("a".repeat(32))?.credential).toBe("late-seat"));
  expect(opened).not.toHaveBeenCalled();
});

it("clicking an active model tab preserves the selected model", async () => {
  const { root } = setup();
  await vi.waitFor(() => expect(root.querySelector('[aria-label="Game"]')).toBeTruthy()); choose(root, "Game", "dice_circuit");
  await vi.waitFor(() => expect(root.querySelector('[aria-label="Player 1 type"] option[value="strategist"]')).toBeTruthy());
  choose(root, "Player 1 type", "hodr");
  root.querySelector<HTMLButtonElement>('.bc-seat-card [data-kind="model"]')!.click();
  expect(root.querySelector<HTMLSelectElement>('[aria-label="Player 1 type"]')!.value).toBe("hodr");
});

it("descriptions and validation errors retain readable contrast in Modern dark", async () => {
  const fsModule = "node:fs", fs = await import(/* @vite-ignore */ fsModule);
  const read = (name: string): string => fs.readFileSync(`${(globalThis as any).process.cwd()}/src/${name}`, "utf8");
  const tokens = read("ui/tokens.css"), dark = tokens.match(/:root\[data-palette="ciemny"\]\s*\{([^}]+)\}/)![1];
  const values = new Map<string, string>();
  for (const source of [tokens.split(':root[data-palette=')[0], dark]) for (const match of source.matchAll(/(--[\w-]+)\s*:\s*([^;]+);/g)) values.set(match[1], match[2].trim());
  let css = read("style.css") + read("ui/shell.css");
  css = css.replace(/@import[^;]+;/g, "");
  for (let n = 0; n < 4; n++) css = css.replace(/var\((--[\w-]+)\)/g, (all, key) => values.get(key) ?? all);
  const style = document.createElement("style"); style.textContent = css; document.head.append(style);
  const root = document.createElement("div"); root.className = "bc-app";
  root.innerHTML = '<div class="bc-room"><div class="bc-panel"><p class="bc-setting-description">Description</p><p class="bc-setting-error">Invalid value</p></div></div>'; document.body.append(root);
  const rgb = (s: string) => s.startsWith("#") ? s.slice(1).match(/../g)!.map(v => parseInt(v, 16)) : s.match(/\d+/g)!.slice(0, 3).map(Number);
  const light = (s: string) => rgb(s).map(v => { const c = v / 255; return c <= .04045 ? c / 12.92 : ((c + .055) / 1.055) ** 2.4; }).reduce((a, v, i) => a + v * [.2126, .7152, .0722][i], 0);
  try { for (const kind of ["description", "error"]) {
    const fg = light(getComputedStyle(root.querySelector(`.bc-setting-${kind}`)!).color), bg = light(values.get("--panel2")!);
    expect((Math.max(fg, bg) + .05) / (Math.min(fg, bg) + .05), kind).toBeGreaterThanOrEqual(4.5);
  } } finally { style.remove(); }
});

it("host model entries appear under the host's group heading and a preselected seat is prefilled", async () => {
  const extraModels = vi.fn(async () => [{ choice_id: "run-a-ckpt-7", display_name: "mokosz-gen0 · step 7", kind: "model", reference: {}, group: "Training: mokosz" }]);
  const { root, requests, opened } = setup({ extraModels: extraModels as any, preselect: { gameId: "dice_circuit", kind: "model", choiceId: "run-a-ckpt-7" } });
  await vi.waitFor(() => expect(root.querySelector<HTMLSelectElement>('[aria-label="Player 2 type"]')?.value).toBe("run-a-ckpt-7"));
  const groups = [...root.querySelectorAll<HTMLOptGroupElement>('[aria-label="Player 2 type"] optgroup')].map(g => g.label);
  expect(groups).toEqual(["People", "Agents", "Built-in bots", "Trained models", "Training: mokosz", "Unused"]);
  const picker = root.querySelector<HTMLSelectElement>('[aria-label="Player 2 bot or model"]')!;
  expect([...picker.querySelectorAll("optgroup")].map(g => g.label)).toEqual(["Trained models", "Training: mokosz"]);
  expect(root.querySelector<HTMLSelectElement>('[aria-label="Player 1 type"]')!.value).toBe("human");
  expect([...root.querySelectorAll(".bc-seat-card")].map(c => c.getAttribute("aria-label"))).toEqual(["Seat 1", "Seat 2", "Seat 3", "Seat 4"]);
  expect([...root.querySelectorAll(".bc-seat-card .bc-seat-top .bc-seat-num")].map(l => l.textContent)).toEqual(["SEAT 1", "SEAT 2", "SEAT 3", "SEAT 4"]);
  expect(root.querySelector('.bc-seat-card [role="group"]')!.getAttribute("aria-label")).toBe("Player 1 seat type");
  press(root, "Create and sit");
  await vi.waitFor(() => expect(opened).toHaveBeenCalled());
  expect(requests.find(r => r.path === "/api/tables" && r.body)!.body.seats).toEqual(["human", "run-a-ckpt-7"]);
});

it("an unknown preselected choice is reported and nothing is guessed", async () => {
  const { root } = setup({ preselect: { gameId: "dice_circuit", kind: "model", choiceId: "missing" } });
  await vi.waitFor(() => expect(root.querySelector(".bc-create")!.textContent).toContain("The chosen bot or model is not available for this game."));
  expect(root.querySelector<HTMLSelectElement>('[aria-label="Player 2 type"]')!.value).not.toBe("missing");
});

it("an outer shell opens Play with a model already chosen through playWith", async () => {
  const fetcher = vi.fn(async (path: string) => new Response(JSON.stringify(path === "/api/games" ? games : path.endsWith("/bots") ? descriptors
    : path.endsWith("/settings") ? { table: {}, live: {}, bots: {} } : { items: [], total: 0, offset: 0, limit: 12 }), { headers: { "Content-Type": "application/json" } }));
  vi.stubGlobal("fetch", fetcher);
  const root = document.createElement("div"); document.body.append(root);
  const app = mountPlayApp(root, { showNavigation: false, initialPage: "settings" }); disposers.push(() => app.dispose());
  app.playWith({ gameId: "rentcraft", kind: "model", choiceId: "njord-gen0", seat: 2 });
  await vi.waitFor(() => expect(root.querySelector<HTMLSelectElement>('[aria-label="Player 3 type"]')?.value).toBe("njord-gen0"));
  expect(root.querySelector<HTMLSelectElement>('[aria-label="Game"]')!.value).toBe("rentcraft");
});

it("the host can set the picker heading of entries the server lists itself", async () => {
  const { root } = setup({ groupFor: d => (d.choice_id === "hodr" ? "Own trainings (private build only)" : undefined) });
  await vi.waitFor(() => expect(root.querySelector('[aria-label="Game"]')).toBeTruthy());
  choose(root, "Game", "dice_circuit");
  await vi.waitFor(() => expect(root.querySelector('[aria-label="Player 1 type"] option[value="hodr"]')).toBeTruthy());
  const groups = [...root.querySelectorAll<HTMLOptGroupElement>('[aria-label="Player 1 type"] optgroup')].map(g => [g.label, [...g.querySelectorAll("option")].map(o => o.value)]);
  expect(groups).toContainEqual(["Own trainings (private build only)", ["hodr"]]);
  expect(groups).toContainEqual(["Trained models", ["mini-evo5", "njord-gen0"]]);
});

it("Rentcraft seats get a person's name, distinct pieces and a combined player, sent before anybody sits", async () => {
  const { root, requests, opened } = setup();
  await vi.waitFor(() => expect(root.querySelector('[aria-label="Game"]')).toBeTruthy());
  choose(root, "Game", "rentcraft");
  await vi.waitFor(() => expect(root.querySelector('[aria-label="Player 2 type"] option[value="strategist"]')).toBeTruthy());
  const tabs = [...root.querySelectorAll('.bc-seat-card [role="group"][aria-label="Player 1 seat type"] button')].map(b => b.getAttribute("aria-label"));
  expect(tabs).toEqual(["Human", "Bot", "MCP/API agent", "RL model", "Combined player", "Empty"]);
  expect(root.querySelector(".bc-seat-card label:not([hidden]) span")?.textContent).toBe("Personality");
  const name = root.querySelector<HTMLInputElement>('[aria-label="Player 1 name"]')!;
  name.value = "Alice"; name.dispatchEvent(new Event("input"));
  root.querySelector<HTMLButtonElement>('[aria-label="Player 1 piece"] [aria-label="zebra"]')!.click();
  expect(root.querySelector<HTMLButtonElement>('[aria-label="Player 2 piece"] [aria-label="zebra"]')!.disabled).toBe(true);
  press(root.querySelector('[aria-label="Player 3 seat type"]')!, "Combined player");
  const offers = root.querySelector<HTMLSelectElement>('[aria-label="Player 3 combined player: offers"]')!;
  offers.value = "hodr"; offers.dispatchEvent(new Event("change"));
  press(root, "Create and sit");
  await vi.waitFor(() => expect(opened).toHaveBeenCalled());
  const made = requests.find(r => r.path === "/api/tables" && r.body)!.body;
  expect(made.seats[2]).toBe("hybrid:offers=hodr;rest=strategist");
  // The creator's names go with the table itself; the pieces follow before anybody sits.
  expect(made.seat_names).toEqual(["Alice", null, null]);
  const shown = requests.find(r => r.path.endsWith("/presentation"))!;
  expect(shown.body.seats[0]).toEqual({ seat: 0, token: "zebra" });
  expect(new Set(shown.body.seats.map((s: any) => s.token)).size).toBe(shown.body.seats.length);
  expect(requests.findIndex(r => r.path.endsWith("/presentation"))).toBeLessThan(requests.findIndex(r => r.path.endsWith("/join")));
});

it("the creator deletes one table or every finished table they created; the replay stays on the server", async () => {
  const store = new SessionStore(location.origin);
  const id = (n: number) => n.toString(16).padStart(32, "0");
  store.remember({ table: id(1), creator: "own-1" } as any);
  store.remember({ table: id(2), creator: "own-2" } as any);
  const deleted = new Set<string>();
  const calls: { path: string; credential?: string }[] = [];
  const request = vi.fn(async (path: string, body?: any, credential?: string) => {
    calls.push({ path, credential });
    if (path === "/api/games") return games;
    if (path.endsWith("/delete")) { deleted.add(path.split("/")[3]); return { deleted: true }; }
    const items = [[1, "completed"], [2, "completed"], [3, "completed"], [4, "playing"]].filter(([n]) => !deleted.has(id(n as number)))
      .map(([n, status]) => ({ table_id: id(n as number), game_id: "dice_circuit", status, players: 1, seats: [{ seat: 0, kind: "builtin", occupied: true, display_name: "Greedy" }] }));
    return { total: items.length, offset: 0, limit: 10, items };
  });
  vi.stubGlobal("confirm", () => true);
  const { root } = setup({ store, request: request as any });
  await vi.waitFor(() => expect(root.querySelectorAll(".bc-table-actions button.del")).toHaveLength(2));
  const bulk = root.querySelector<HTMLButtonElement>(".bc-bulk-delete")!;
  expect(bulk.hidden).toBe(false);
  expect(bulk.textContent).toBe("🗑 Delete finished (2)");
  root.querySelector<HTMLButtonElement>(".bc-table-actions button.del")!.click();
  await vi.waitFor(() => expect(root.querySelectorAll(".bc-table-actions button.del")).toHaveLength(1));
  bulk.click();
  await vi.waitFor(() => expect(bulk.hidden).toBe(true));
  expect(calls.filter(c => c.path.endsWith("/delete")).map(c => [c.path, c.credential])).toEqual([[`/api/tables/${id(1)}/delete`, "own-1"], [`/api/tables/${id(2)}/delete`, "own-2"]]);
});

it("a table row shows the server's table number and the round in play", async () => {
  const request = vi.fn(async (path: string) => {
    if (path === "/api/games") return games;
    return { total: 1, offset: 0, limit: 10, items: [{ table_id: "c".repeat(32), number: 130, round: 14, rounds: null, game_id: "dice_circuit", status: "playing", players: 1,
      seats: [{ seat: 0, kind: "builtin", occupied: true, display_name: "Greedy" }] }] };
  });
  const { root } = setup({ request: request as any });
  await vi.waitFor(() => expect(root.querySelector(".bc-table-head strong")?.textContent).toBe("Table #130"));
  expect(root.querySelector(".bc-table-head .tag")!.textContent).toBe("in play · round 14");
});

it("seat names: the creator names an agent seat with the same Name field; bots keep their own name; names go with the table", async () => {
  const { root, requests, opened } = setup();
  await vi.waitFor(() => expect(root.querySelector('[aria-label="Game"]')).toBeTruthy());
  choose(root, "Game", "dice_circuit");
  await vi.waitFor(() => expect(root.querySelector('[aria-label="Player 2 type"] option[value="strategist"]')).toBeTruthy());
  choose(root, "Number of players", "3");
  press(root.querySelector('[aria-label="Player 2 seat type"]')!, "MCP/API agent");
  press(root.querySelector('[aria-label="Player 3 seat type"]')!, "MCP/API agent");
  const field = (n: number) => root.querySelector<HTMLInputElement>(`[aria-label="Player ${n} name"]`)!;
  expect(field(2).closest("label")!.hidden).toBe(false);
  expect(field(2).value).toBe("");
  expect(field(2).placeholder).toMatch(/agent/i);
  field(2).value = " Robin "; field(2).dispatchEvent(new Event("input"));
  press(root.querySelector('[aria-label="Player 2 seat type"]')!, "Bot");
  expect(field(2).closest("label")!.hidden).toBe(true);
  press(root.querySelector('[aria-label="Player 2 seat type"]')!, "MCP/API agent");
  expect(field(2).value).toBe(" Robin ");
  press(root, "Create and sit");
  await vi.waitFor(() => expect(opened).toHaveBeenCalled());
  const made = requests.find(r => r.path === "/api/tables" && r.body)!.body;
  expect(made.seats).toEqual(["human", "agent", "agent"]);
  // An agent seat left empty lets the agent name itself when it joins.
  expect(made.seat_names).toEqual(["Player", "Robin", null]);
  expect(requests.some(r => r.path.endsWith("/presentation"))).toBe(false);
});
