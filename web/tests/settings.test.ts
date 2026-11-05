async function pickFirstGame(root: HTMLElement) {
  await vi.waitFor(() => expect(root.querySelector('[aria-label="Game"]')).toBeTruthy());
  const game = root.querySelector<HTMLSelectElement>('[aria-label="Game"]')!;
  game.value = game.options[1].value; game.dispatchEvent(new Event("change"));
}
import { expect, it, vi, afterEach } from "vitest";
import { mountPlayApp } from "../src/app";
const table = { properties: { rounds: { title: "Round limit", type: ["integer", "null"], minimum: 1, default: null } } };
const options = { properties: { depth: { title: "Depth", type: "integer", minimum: 1, default: 4 } } };
const live = { properties: { delay: { title: "Wait time", type: "number", minimum: 0, default: 0 } } };
// The Play page keeps the open form in the address (#/play?new=...), so every test starts from a clean address.
afterEach(() => { vi.unstubAllGlobals(); history.replaceState(null, "", "/"); });
it("submits discovered settings, configured seats and a live override (a bot seat plays with its own settings)", async () => {
  vi.stubGlobal("localStorage", { getItem: () => null, setItem: () => { } });
  const calls: any[] = [];
  vi.stubGlobal("fetch", async (path: string, init: RequestInit) => {
    // Only the table request: arming the server's automatic start is a separate call of the same click.
    if (init.body && !String(path).endsWith("/autostart"))
      calls.push([path, JSON.parse(String(init.body))]);
    const value = path.endsWith("/settings") ? { table, live, bots: { reader: options }, simulation_defaults: { rounds: 137 } } :
      path.endsWith("/bots") ? [{ kind: "builtin", choice_id: "reader", display_name: "Reader" }] :
        path === "/api/games" ? [{ game_id: "fixture", name: "Fixture", variants: ["base"], min_players: 2, max_players: 2, capabilities: { seat_operations: true } }] : {};
    return new Response(JSON.stringify(value), { headers: { "Content-Type": "application/json" } });
  });
  const root = document.createElement("div"), app = mountPlayApp(root);
  try {
    await pickFirstGame(root);
    await vi.waitFor(() => expect(root.querySelector('[aria-label="Round limit"]')).toBeTruthy());
    // The shared design has no per-seat bot settings: the seat shows no "Depth" field.
    expect(root.querySelector('[aria-label="Depth"]')).toBeNull();
    const set = (label: string, value: string) => {
      const input = root.querySelector<HTMLInputElement>('[aria-label="' + label + '"]')!;
      input.value = value;
      input.dispatchEvent(new Event("input"));
    };
    expect(root.querySelector<HTMLInputElement>('[aria-label="Round limit"]')!.value).toBe("");
    set("Round limit", "71");
    set("Wait time", "2");
    const seat = root.querySelector<HTMLSelectElement>('[aria-label="Player 1 type"]')!;
    seat.value = "agent";
    seat.dispatchEvent(new Event("change"));
    root.querySelector(".bc-create")!.dispatchEvent(new Event("submit", { cancelable: true }));
    await vi.waitFor(() => expect(calls).toHaveLength(1));
    expect(calls[0]).toEqual(["/api/tables", { config: { game_id: "fixture", variant: "base", players: 2, settings: { rounds: 71 } }, live: { delay: 2 }, seats: ["agent", "reader"], visibility: "listed" }]);
  }
  finally {
    app.dispose();
  }
});
it("keeps a surviving seat on a count change and resets the rules when choosing another game", async () => {
  vi.stubGlobal("localStorage", { getItem: () => null, setItem: () => { } });
  const games = ["first", "second"].map(game_id => ({ game_id, name: game_id, variants: ["base"], min_players: 2, max_players: 3 }));
  const list = [{ kind: "builtin", choice_id: "a", display_name: "Alpha" }, { kind: "builtin", choice_id: "b", display_name: "Bravo" }];
  vi.stubGlobal("fetch", async (path: string) => new Response(JSON.stringify(path === "/api/games" ? games : path.endsWith("/bots") ? list : path.endsWith("/settings") ? { table, live, bots: { a: options, b: options }, simulation_defaults: {} } : {}), { headers: { "Content-Type": "application/json" } }));
  const root = document.createElement("div"), app = mountPlayApp(root);
  const field = (label: string) => root.querySelector<HTMLInputElement>('[aria-label="' + label + '"]')!;
  const choose = (label: string, v: string) => {
    field(label).value = v;
    field(label).dispatchEvent(new Event("change"));
  };
  try {
    await pickFirstGame(root);
    await vi.waitFor(() => expect(field("Round limit")).toBeTruthy());
    choose("Player 2 type", "b");
    choose("Round limit", "12");
    choose("Number of players", "3");
    expect(field("Player 2 type").value).toBe("b");
    expect(field("Depth")).toBeNull();
    choose("Game", "second");
    await vi.waitFor(() => expect(field("Round limit")?.value).toBe(""));
  }
  finally {
    app.dispose();
  }
});

it.each(["automatic", "explicit", "cleared"])("submits the %s trade wait after changing games and seats", async mode => {
  vi.stubGlobal("localStorage", { getItem: () => null, setItem: () => {} });
  const games = ["dice_circuit", "rentcraft"].map(game_id => ({ game_id, name: game_id, variants: ["base"], min_players: 2, max_players: 2 }));
  const wait = { title: "Trade wait", type: "number", minimum: 0, default: 3, automatic: true,
    description: "The default is 3 seconds with people or agents and 0 with bots only." };
  const requests: any[] = [];
  vi.stubGlobal("fetch", async (path: string, init: RequestInit) => {
    if (init.body && !/\/(presentation|autostart)$/.test(String(path))) requests.push(JSON.parse(String(init.body)));
    const value = path === "/api/games" ? games : path.endsWith("/bots") ? [{ kind: "builtin", choice_id: "reader", display_name: "Reader" }] :
      path.endsWith("/settings") ? { table: {}, live: { properties: path.includes("/rentcraft/") ? { trade_window_wait_s: wait } : {} }, bots: {} } : {};
    return new Response(JSON.stringify(value), { headers: { "Content-Type": "application/json" } });
  });
  const root = document.createElement("main"), app = mountPlayApp(root);
  const field = (title: string) => root.querySelector<HTMLInputElement>('[aria-label="' + title + '"]');
  const change = (title: string, value: string) => {
    const el = field(title)!;
    el.value = value;
    el.dispatchEvent(new Event("change"));
  };
  try {
    await vi.waitFor(() => expect(field("Game")).toBeTruthy());
    expect(field("Trade wait")).toBeNull();
    change("Game", "rentcraft");
    await vi.waitFor(() => expect(field("Trade wait")).toBeTruthy());
    // The compact options row shows the roster default; it is only sent when the field is edited.
    expect(field("Trade wait")!.placeholder).toBe("Auto");
    expect(field("Trade wait")!.value).toBe("3");
    if (mode !== "automatic") change("Trade wait", "7");
    change("Player 1 type", "reader");
    expect(field("Trade wait")!.value).toBe(mode === "automatic" ? "3" : "7");
    if (mode === "cleared") change("Trade wait", "");
    expect(field("Trade wait")!.checkValidity()).toBe(true);
    expect(field("Trade wait")!.closest("label")!.title).toBe(wait.description);
    expect(field("Trade wait")!.closest("label")!.textContent).not.toContain("Default");
    root.querySelector(".bc-create")!.dispatchEvent(new Event("submit", { cancelable: true }));
    await vi.waitFor(() => expect(requests).toHaveLength(1));
    expect(requests[0]).toEqual({ config: { game_id: "rentcraft", variant: "base", players: 2 },
      seats: ["reader", "reader"], visibility: "listed", ...(mode === "explicit" ? { live: { trade_window_wait_s: 7 } } : {}) });
  } finally {
    app.dispose();
  }
});

import { settingsFields } from "../src/settings";
it("marks a malformed array invalid until the current field is corrected", () => {
  const root = document.createElement("div");
  const fields = settingsFields(root, "Bot options", { properties: { bonuses: { type: "array", title: "Station bonuses", default: [] } } });
  const field = root.querySelector("textarea")!;
  field.value = "[1,";
  field.dispatchEvent(new Event("input"));
  expect(field.checkValidity()).toBe(false);
  expect(() => fields.read()).toThrow("Station bonuses");
  field.value = "[7]";
  field.dispatchEvent(new Event("input"));
  expect(field.checkValidity()).toBe(true);
  expect(fields.read()).toEqual({ bonuses: [7] });
});

it("passes imported options to validation exactly as supplied until their field is edited", () => {
  const root = document.createElement("div");
  const schema = { properties: {
    min_reserve: { type: "integer", title: "Minimum reserve" },
    mode: { type: "string", enum: ["standard", "cautious"] },
  } };
  const saved = { min_resevre: 421, min_reserve: 90, mode: "unrecognized" };
  const fields = settingsFields(root, "Bot options", schema, {}, saved);
  expect(fields.read()).toEqual(saved);
  const field = root.querySelector<HTMLInputElement>('[aria-label="Minimum reserve"]')!;
  field.value = "95";
  field.dispatchEvent(new Event("input"));
  expect(fields.read()).toEqual({ ...saved, min_reserve: 95 });
});
