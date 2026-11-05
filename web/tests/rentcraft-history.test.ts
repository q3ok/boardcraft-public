import { afterEach, expect, it, vi } from "vitest";
import { mountPlayApp } from "../src/app";
import { mountRentcraftView } from "../src/games/rentcraft/view";

const table = "a".repeat(32);
const board = { spaces: [], cards: {} };
const config = { game_id: "rentcraft", variant: "base", players: 2 };
function observation(revision: number, event_cursor: number, amount: number, viewer: number | null = null) {
  return { revision, viewer, game_id: "rentcraft", decision: null, data: {
    round: 1, current: 0, phase: "pre_roll", players: [], event_cursor,
    memory: { log: [{ kind: "pay", who: 0, amount, round: 1 }] },
  } };
}
function historyLines(root: HTMLElement) {
  return [...root.querySelectorAll(".rc-hud .log-lines div:not(.empty)")].map(el => el.textContent);
}
function moveLines(root: HTMLElement) {
  return [...root.querySelectorAll(".rc-hud .mind .moves div")].map(el => el.textContent);
}
afterEach(() => {
  vi.unstubAllGlobals();
  history.replaceState(null, "", "/");
  sessionStorage.clear();
  document.body.replaceChildren();
});

it.each([3, 10])("restores completed history after reviewing a frame with cursor %i", async cursor => {
  // The replay opens from its own address (the Replays page); the game bar has no replay button.
  history.replaceState(null, "", "/#/replay/" + table);
  vi.stubGlobal("localStorage", { setItem() {}, getItem: () => null });
  const result = observation(8, 10, 87);
  vi.stubGlobal("fetch", async (url: string) => {
    let body: unknown;
    if (url.endsWith("/view")) body = { table_id: table, status: "completed", config, event_cursor: 10,
      observation: { ...result, data: { ...result.data, result: { placements: [1, 2], scores: [101, 99] } } } };
    else if (url.endsWith("/actions")) body = { decision_id: null, actions: [] };
    else if (url.endsWith("/board")) body = board;
    else if (url.endsWith("/rules")) body = "Rules";
    else if (url.endsWith("/settings")) body = { table: {}, live: {}, bots: {}, simulation_defaults: {} };
    else if (url.endsWith("/replay?position=0")) body = { position: 0, total_positions: 3,
      observation: observation(0, cursor, 24), events: [{ sequence: cursor, kind: "start_roll", payload: { player: 0, dice: [3, 5] } }] };
    else if (url.endsWith("/replay?position=1")) body = { position: 1, total_positions: 3,
      observation: observation(1, cursor, 24), events: [] };
    else throw Error(url);
    return new Response(JSON.stringify(body), { headers: { "Content-Type": "application/json" } });
  });
  const root = document.createElement("main");
  document.body.append(root);
  const find = (label: string) => [...root.querySelectorAll("button")].find(b => b.textContent === label);
  const app = mountPlayApp(root);
  try {
    await vi.waitFor(() => expect(moveLines(root)).toEqual(["Player 1 rolls 3 + 5 for the starting order"]));
    expect(historyLines(root)).toEqual(["Player 1 pays $24 to the bank"]);
    root.querySelector<HTMLButtonElement>('[aria-label="Next position (→)"]')!.click();
    await vi.waitFor(() => expect(root.querySelector(".tl-pos")!.textContent).toBe("decision 2 / 3"));
    await vi.waitFor(() => expect(moveLines(root)).toEqual([]));
    expect(root.querySelector(".rc-hud .mind")!.textContent).toContain("No visible change at this position.");
    root.querySelector<HTMLButtonElement>('[aria-label="Previous position (←)"]')!.click();
    await vi.waitFor(() => expect(moveLines(root)).toEqual(["Player 1 rolls 3 + 5 for the starting order"]));
    find("✕ Close replay")!.click();
    await vi.waitFor(() => expect(root.textContent).toContain("Final standings"));
    expect(historyLines(root)).toEqual(["Player 1 pays $87 to the bank"]);
    expect(root.querySelector<HTMLElement>(".rc-hud .mind")!.hidden).toBe(true);
  } finally {
    app.dispose();
  }
});

it("reconciles silent revisions and perspective changes without repeating a live receipt", () => {
  const root = document.createElement("main");
  const widget = mountRentcraftView(root, { gameId: "rentcraft", boardSpec: board, rulesText: "Rules" });
  const view = (revision: number, cursor: number, amount: number, viewer: number) => ({
    table_id: table, status: "playing", config, event_cursor: cursor, observation: observation(revision, cursor, amount, viewer),
  });
  try {
    widget.update(view(7, 12, 28, 0));
    const event = { sequence: 13, kind: "pay", payload: { player: 0, amount: 17 } };
    widget.update(view(8, 13, 17, 0), [event]);
    widget.update(view(8, 13, 17, 0), [event]);
    expect(historyLines(root)).toEqual(["Player 1 pays $17 to the bank", "Player 1 pays $28 to the bank"]);
    widget.update(view(9, 13, 17, 0));
    expect(historyLines(root)).toEqual(["Player 1 pays $17 to the bank", "Player 1 pays $28 to the bank"]);
    widget.update(view(9, 13, 31, 1), [event]);
    expect(historyLines(root)).toEqual(["Player 1 pays $31 to the bank"]);
  } finally {
    widget.dispose();
  }
});

it("keeps the roll and move visible after a silent end turn", () => {
  // Spectator HUD fields and receipts captured with two players, seed 50 and trade windows off.
  const root = document.createElement("main");
  const spaces = ["Start", "Mill Street", "Community Fund", "Blacksmith Street", "Income Tax", "West Station",
    "Linden Street", "Opportunity", "Birch Street", "Maple Street", "Jail"].map(name => ({ name }));
  const widget = mountRentcraftView(root, { gameId: "rentcraft", boardSpec: { spaces, cards: {} }, rulesText: "Rules", readOnly: true });
  const view = (revision: number, event_cursor: number, current: number, position: number, phase: string) => ({
    table_id: table, status: "playing", event_cursor, config: { ...config, settings: { trade_windows: "off" } },
    observation: { revision, game_id: "rentcraft", viewer: null, decision: null, data: {
      round: 1, current, acting_seat: current, phase, event_cursor, memory: { log: [] },
      players: [0, 1].map(id => ({ id, cash: 1500, position: id === 1 ? position : 0, bankrupt: false, in_jail: false })),
    } },
  });
  try {
    widget.update(view(0, 3, 1, 0, "pre_roll"));
    widget.update(view(1, 5, 1, 10, "post_roll"), [
      { sequence: 4, kind: "roll", payload: { player: 1, dice: [4, 6] } },
      { sequence: 5, kind: "move", payload: { player: 1, to: 10 } },
    ]);
    const expected = ["Player 2 moves to Jail", "Player 2 rolls 4 + 6"];
    expect(historyLines(root)).toEqual(expected);
    widget.update(view(2, 5, 0, 10, "pre_roll"), []);
    expect(historyLines(root)).toEqual(expected);
  } finally {
    widget.dispose();
  }
});

it("shows the recorded decision with the model's probabilities in the replay's In the player's head panel", () => {
  const root = document.createElement("main");
  const widget = mountRentcraftView(root, { gameId: "rentcraft", boardSpec: board, rulesText: "Rules" });
  const positions = [{ decision: null, metrics: null }, { metrics: null, decision: { player: 1, action: "Buy Oak Lane", meta: { probabilities: [{ action: "Buy Oak Lane", probability: 0.82 }, { action: "Decline", probability: 0.18 }] } } },
    { metrics: null, decision: { player: 0, action: "End turn", explanation: "Nothing worth building." } }];
  const view = (position: number) => ({ table_id: table, status: "replay", config, event_cursor: position, observation: observation(position + 1, position, 5),
    replay_position: position, replay_timeline: { positions } });
  try {
    widget.update(view(1) as any);
    const mind = root.querySelector<HTMLElement>(".rc-hud .mind")!;
    expect(mind.querySelector("h4")!.textContent).toBe("In the player's head");
    expect(mind.querySelector(".chosen")!.textContent).toContain("Buy Oak Lane");
    expect([...mind.querySelectorAll(".prob")].map(p => [p.querySelector("span")!.textContent, p.querySelector("b")!.textContent, p.classList.contains("on")]))
      .toEqual([["Buy Oak Lane", "82%", true], ["Decline", "18%", false]]);
    widget.update(view(2) as any);
    expect(mind.querySelector(".expl")!.textContent).toContain("Nothing worth building.");
    widget.update(view(0) as any);
    expect(mind.textContent).toContain("Start of the game.");
  } finally {
    widget.dispose();
  }
});
