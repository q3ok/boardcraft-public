import { afterEach, beforeEach, expect, it, vi } from "vitest";
vi.mock("../src/scene/table", () => ({
  SharedTable3D: class { setCamera() {} setInsets() {} resize() {} dispose() {} },
}));
vi.mock("../src/scene/dice-circuit", () => ({
  DiceCircuitScene: class { mount() {} update() {} setCamera() {} setOpponent() {} dispose() {} },
}));
import { mountDiceCircuitView } from "../src/games/dice-circuit/view";
import { mountNavigation } from "../src/ui/nav";
import { mountReplayBar } from "../src/ui/replaybar";
import { timelineMarks } from "../src/app";
import sheet from "../../src/boardcraft/games/dice_circuit/data/base-sheet.json";

beforeEach(() => {
  globalThis.ResizeObserver = class { observe() {} unobserve() {} disconnect() {} } as any;
  document.body.replaceChildren();
});
afterEach(() => { vi.unstubAllGlobals(); document.documentElement.removeAttribute("data-palette"); });

const blank = (over: Record<string, unknown> = {}) => ({ yellow: sheet.yellow.premarked, blue: [], green: 0, orange: [], purple: [], earned_rewards: [],
  rerolls: 1, rerolls_earned: 2, rerolls_used: 1, extras: 1, extras_earned: 2, extras_used: 1, emblem_count: 0, ...over });
function soloView(over: Record<string, unknown> = {}, dice: Record<string, [number, string]> = {}) {
  const zones: Record<string, [number, string]> = { yellow: [1, "tray"], blue: [2, "tray"], green: [2, "tray"], orange: [4, "kept"], purple: [5, "kept"], white: [6, "kept"], ...dice };
  return {
    table_id: "t".repeat(32), status: "playing", config: { game_id: "dice_circuit", variant: "base", players: 1 },
    seats: [{ seat: 0, kind: "human", occupied: true, display_name: "Alice" }],
    observation: { game_id: "dice_circuit", viewer: 0, revision: 9, decision: { id: "p", player: 0, kind: "passive" }, data: {
      round: 2, rounds_total: 6, active_player: 0, phase: "passive", turn_phase: "passive", regular_opportunity: 0, event_cursor: 9,
      dice: Object.fromEntries(Object.entries(zones).map(([id, [value, zone]]) => [id, { value, zone }])),
      sheets: [blank()], score_summary: [{ areas: { yellow: 0, blue: 0, green: 0, orange: 0, purple: 0 }, emblem_points: 0, total: 12 }],
      window: { stage: "passive", extra_used: [], bonus_stack: { frames: [], current: null } }, ...over,
    } },
  } as any;
}
const select = (die: string, area: string, target: unknown, kind = "select_die") => ({ id: `${kind}:${die}:${area}`, kind, label: "x", params: { die_id: die, area, target } });

it("a solo passive turn says so plainly, marks the tray and names the round and seat", () => {
  const root = document.createElement("main"); document.body.append(root);
  const view = mountDiceCircuitView(root, { gameId: "dice_circuit", boardSpec: sheet as any, rulesText: "" });
  view.update(soloView(), [], { decision_id: "p", actions: [select("yellow", "yellow", [1, 0])] } as any);
  expect(root.querySelector(".bc-status")!.textContent).toBe("Round 2 of 6: simulated opponent's turn");
  expect(root.querySelector(".bc-decision-title")!.textContent).toBe("Simulated opponent's turn: pick 1 of the 3 dice on the tray, then a field");
  const strip = [...root.querySelectorAll<HTMLButtonElement>(".bc-die-button")];
  expect(strip).toHaveLength(6);
  expect(strip.filter(b => b.classList.contains("bc-zone-tray")).map(b => b.textContent)).toEqual(["Yellow 1tray", "Blue 2tray", "Green 2tray"]);
  expect(strip[0].disabled).toBe(false);
  expect(strip[3].disabled).toBe(true);
  expect(root.querySelector(".bc-exception")).toBeNull();
  expect(root.querySelector(".bc-score span")!.textContent).toBe("You");
  view.update(soloView({ revision: 10 }), [], { decision_id: "p", actions: [select("purple", "purple", 0)] } as any);
  expect(root.querySelector(".bc-exception")!.textContent).toContain("None of the 3 tray dice fits your sheet");
  view.dispose();
});

it("the +1 window is signalled and each +1 field says what it spends", () => {
  const root = document.createElement("main"); document.body.append(root);
  const view = mountDiceCircuitView(root, { gameId: "dice_circuit", boardSpec: sheet as any, rulesText: "" });
  const data = { window: { stage: "extra", extra_used: [], bonus_stack: { frames: [], current: null } } };
  const actions = [select("green", "green", 0, "extra_die"), { id: "finish_extra", kind: "finish_extra", label: "Finish extra-die choices.", params: {} }];
  view.update(soloView(data, { green: [6, "tray"] }), [], { decision_id: "p", actions } as any);
  expect(root.querySelector(".bc-decision-title")!.textContent).toBe("Now you may use +1: pick any die or finish (1 +1 left)");
  expect([...root.querySelectorAll("button")].some(b => b.textContent === "Finish - keep 1 +1 for later")).toBe(true);
  [...root.querySelectorAll<HTMLButtonElement>(".bc-die-button")].find(b => b.textContent!.startsWith("Green"))!.click();
  expect([...root.querySelectorAll(".bc-target")].map(b => b.textContent)).toEqual(["Use +1: green 6 -> green field 1 (1 left -> 0)"]);
  const resources = root.querySelector(".bc-resources")!.textContent!;
  expect(resources).toContain("Rerolls1 left2 earned · 1 used");
  expect(resources).toContain("Extra dice1 left2 earned · 1 used");
  view.dispose();
});

it("recent moves use seat names and show what a window revealed, never before it", () => {
  const root = document.createElement("main"); document.body.append(root);
  const view = mountDiceCircuitView(root, { gameId: "dice_circuit", boardSpec: sheet as any, rulesText: "", readOnly: true });
  const watching = (revision: number) => ({ ...soloView({ event_cursor: revision }), seats: [{ seat: 0, kind: "model", occupied: true, display_name: "mokosz-gen0" }],
    observation: { ...soloView().observation, viewer: null, revision, decision: null } });
  view.update(watching(1), [{ sequence: 1, kind: "passive_started", payload: { active_player: 0 } }] as any);
  const moves = () => [...root.querySelectorAll(".bc-moves-list li")].map(li => li.textContent);
  expect(moves()).toEqual(["The others choose from the shared tray"]);
  expect(root.querySelector(".bc-resources h4")!.textContent).toBe("mokosz-gen0: resources");
  const before = soloView().observation.data.sheets[0];
  const after = { ...before, yellow: [...before.yellow, [1, 0]], orange: [5] };
  view.update(watching(2), [{ sequence: 2, kind: "window_published", payload: { phase: "passive", sheets: [after] } }] as any);
  expect(moves().slice(0, 2)).toEqual(["mokosz-gen0 entered orange field 1 (5) (simulated turn)", "mokosz-gen0 entered yellow row 2, column 1 (simulated turn)"]);
  expect(root.querySelector(".bc-score span")!.textContent).toBe("mokosz-gen0");
  view.dispose();
});

it("the shared top bar has the colour menu and remembers the choice", () => {
  const data = new Map<string, string>();
  vi.stubGlobal("localStorage", { getItem: (k: string) => data.get(k) ?? null, setItem: (k: string, v: string) => data.set(k, v) });
  const bar = document.createElement("header"); document.body.append(bar);
  mountNavigation(bar, { brand: "Boardcraft", emblem: "", active: "play", items: [{ id: "play", label: "Play" }], select() {} });
  const open = bar.querySelector<HTMLButtonElement>(".palette-btn")!;
  expect(bar.querySelector<HTMLElement>(".palette-pop")!.hidden).toBe(true);
  open.click();
  expect(open.getAttribute("aria-expanded")).toBe("true");
  bar.querySelector<HTMLButtonElement>('[data-palette="ciemny"]')!.click();
  expect(document.documentElement.dataset.palette).toBe("ciemny");
  expect(data.get("boardcraft-palette")).toBe("ciemny");
  expect(bar.querySelector('[data-palette="ciemny"]')!.getAttribute("aria-pressed")).toBe("true");
  document.body.dispatchEvent(new Event("pointerdown", { bubbles: true }));
  expect(bar.querySelector<HTMLElement>(".palette-pop")!.hidden).toBe(true);
  const plain = document.createElement("header");
  mountNavigation(plain, { brand: "Boardcraft", emblem: "", active: "play", items: [], select() {}, palette: false });
  expect(plain.querySelector(".palette-btn")).toBeNull();
});

it("replay marks come from the timeline and jump to their position", async () => {
  const positions = [
    { position: 0, events: [], metrics: { sets: [0, 0], hotels: [0, 0], bankrupt: [false, false], net_worth: [1500, 1500] } },
    { position: 1, events: [{ kind: "trade", player: 1 }], metrics: { sets: [1, 0], hotels: [0, 0], bankrupt: [false, false], net_worth: [1400, 1600] } },
    { position: 2, events: [], metrics: { sets: [1, 0], hotels: [0, 1], bankrupt: [false, true], net_worth: [1700, 0] } },
  ];
  const { marks, legend } = timelineMarks("rentcraft", { total_positions: 3, positions }, ["Ann", "Bob"]);
  expect(marks).toEqual([
    { position: 1, tone: "green", label: "Ann: colour set" }, { position: 1, tone: "amber", label: "Trade: Bob" },
    { position: 2, tone: "red", label: "Bob: hotel" }, { position: 2, tone: "silver", label: "Bob: bankruptcy" },
  ]);
  expect(timelineMarks("dice_circuit", { total_positions: 3, positions }, []).marks).toEqual([]);
  let bar!: ReturnType<typeof mountReplayBar>;
  const load = vi.fn(async (n: number) => bar.shown(n, 3, ""));
  bar = mountReplayBar({ load, close() {} });
  document.body.append(bar.element);
  bar.shown(0, 3, "");
  bar.setMarks(marks, legend);
  expect(bar.element.querySelectorAll(".mk")).toHaveLength(4);
  expect(bar.element.querySelector(".tl-legend")!.textContent).toBe("colour sethoteltradebankruptcy");
  bar.element.querySelector<HTMLButtonElement>('.mk[data-n="2"]')!.click();
  await vi.waitFor(() => expect(load).toHaveBeenCalledWith(2));
  bar.dispose();
});

import { describe as describeScores, histogram, mountSoloTournaments, soloStats } from "../src/solo";
it("solo tournament: leaderboard by mean with 95% interval, paired difference to the best and a shared histogram", () => {
  expect(describeScores([10, 12, 14])).toEqual({ n: 3, mean: 12, std: 2, ci: expect.closeTo(2.263, 3) });
  const rows = soloStats([
    { name: "Greedy", tournamentId: "a", status: "completed", total: 4, scores: [80, 90, 100, 110] },
    { name: "Random", tournamentId: "b", status: "running", total: 4, scores: [40, 50, null, 70] },
  ]);
  expect(rows.map(r => [r.name, r.mean, r.games])).toEqual([["Greedy", 95, 4], ["Random", expect.closeTo(53.33, 2), 3]]);
  expect(rows[1].diff).toBeCloseTo(-40);
  expect([rows[0].best, rows[0].worst]).toEqual([3, 0]);
  expect(histogram([1, 2, 2, 9], 4).counts.reduce((a, b) => a + b, 0)).toBe(4);
});
it("solo tournament: one form creates and starts a single-seat tournament per contestant on the same seeds", async () => {
  const calls: { path: string; body: any; credential?: string }[] = [];
  const request = vi.fn(async (path: string, body?: any, credential?: string) => {
    calls.push({ path, body, credential });
    if (path.endsWith("/bots")) return [{ choice_id: "greedy", display_name: "Greedy bot", kind: "builtin" }, { choice_id: "random", display_name: "Random bot", kind: "builtin" }];
    if (path === "/api/tournaments" && body) return { tournament_id: (calls.length).toString(16).padStart(32, "0"), creator_credential: "c" + calls.length, spec_sha: "s", revision: 0 };
    if (path === "/api/tournaments") return [];
    return {};
  });
  const root = document.createElement("div"); document.body.append(root);
  const solo = mountSoloTournaments(root, { request: request as any, games: async () => [{ game_id: "dice_circuit", name: "Dice Circuit", min_players: 1, variants: ["base"] }] });
  await vi.waitFor(() => expect(root.querySelectorAll(".bc-solo-players input")).toHaveLength(2));
  root.querySelector<HTMLInputElement>('[aria-label="Games per contestant"]')!.value = "1000";
  root.querySelector<HTMLFormElement>(".bc-solo-form")!.requestSubmit();
  await vi.waitFor(() => expect(calls.filter(c => c.path === "/api/tournaments" && c.body?.start === true)).toHaveLength(2));
  const specs = calls.filter(c => c.path === "/api/tournaments" && c.body).map(c => c.body);
  expect(specs.map(s => s.seats)).toEqual([["greedy"], ["random"]]);
  expect(specs.map(s => s.name)).toEqual(["Solo: Solo check · Greedy bot", "Solo: Solo check · Random bot"]);
  expect(specs[0].seeds).toHaveLength(1000);
  expect(new Set(specs[0].seeds).size).toBe(1000);
  expect(specs[1].seeds).toEqual(specs[0].seeds);
  expect(specs[0]).toMatchObject({ config: { players: 1 }, match_count: 1000, rotations: [0] });
  // One request per contestant: the server saves and starts it; no separate start request.
  expect(calls.filter(c => c.path.endsWith("/start"))).toHaveLength(0);
  solo.dispose();
});

it("a Dice replay shows the model's recorded probabilities in In the player's head", () => {
  const root = document.createElement("main"); document.body.append(root);
  const view = mountDiceCircuitView(root, { gameId: "dice_circuit", boardSpec: sheet as any, rulesText: "", readOnly: true });
  const positions = [{ decision: null }, { decision: { player: 0, action: "Pick the green 6", meta: { probabilities: [{ action: "Pick the green 6", probability: 0.7 }, { action: "Reroll", probability: 0.3 }] } } }];
  const replay = (at: number) => ({ ...soloView({ event_cursor: at }), status: "replay", seats: [{ seat: 0, kind: "model", occupied: true, display_name: "mokosz-gen0" }],
    observation: { ...soloView().observation, viewer: null, revision: at + 1, decision: null }, replay_position: at, replay_timeline: { positions } });
  view.update(replay(1) as any, []);
  const mind = root.querySelector<HTMLElement>(".bc-mind")!;
  expect(mind.hidden).toBe(false);
  expect(mind.querySelector(".who")!.textContent).toBe("mokosz-gen0");
  expect([...mind.querySelectorAll(".prob")].map(p => [p.querySelector("span")!.textContent, p.querySelector("b")!.textContent, p.classList.contains("on")]))
    .toEqual([["Pick the green 6", "70%", true], ["Reroll", "30%", false]]);
  view.update(replay(0) as any, []);
  expect(mind.textContent).toContain("Start of the game.");
  view.dispose();
});
