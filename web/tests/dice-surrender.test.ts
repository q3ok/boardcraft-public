import { beforeEach, expect, it, vi } from "vitest";
vi.mock("../src/scene/table", () => ({
  SharedTable3D: class { setCamera() {} setInsets() {} resize() {} dispose() {} },
}));
vi.mock("../src/scene/dice-circuit", () => ({
  DiceCircuitScene: class { mount() {} update() {} setCamera() {} setOpponent() {} dispose() {} },
}));
import { mountDiceCircuitView } from "../src/games/dice-circuit/view";
import { describeDiceEvent } from "../src/games/dice-circuit/describe";
import sheet from "../../src/boardcraft/games/dice_circuit/data/base-sheet.json";

beforeEach(() => {
  globalThis.ResizeObserver = class { observe() {} unobserve() {} disconnect() {} } as any;
  document.body.replaceChildren();
});

const blank = { yellow: sheet.yellow.premarked, blue: [], green: 0, orange: [], purple: [], earned_rewards: [],
  rerolls: 0, rerolls_earned: 0, rerolls_used: 0, extras: 0, extras_earned: 0, extras_used: 0, emblem_count: 0 };
function view(viewer: number | null, extra: Record<string, unknown> = {}, status = "playing") {
  const dice = Object.fromEntries(["yellow", "blue", "green", "orange", "purple", "white"].map((d, i) => [d, { value: i + 1, zone: "available" }]));
  return {
    table_id: "t".repeat(32), status, config: { game_id: "dice_circuit", variant: "base", players: 2 },
    seats: [{ seat: 0, kind: "human", occupied: true, display_name: "Ala" }, { seat: 1, kind: "human", occupied: true, display_name: "Ola" }],
    observation: { game_id: "dice_circuit", viewer, revision: 3, decision: null, data: {
      round: 1, rounds_total: 6, active_player: 1, phase: "regular", turn_phase: "regular", regular_opportunity: 1, event_cursor: 3,
      dice, sheets: [blank, blank], score_summary: [0, 1].map(() => ({ areas: { yellow: 0, blue: 0, green: 0, orange: 0, purple: 0 }, emblem_points: 0, total: 0 })),
      ...extra,
    } },
  } as any;
}
const resign = { kind: "resign", scope: "seat", label: "Surrender", params_schema: { type: "object", properties: {}, additionalProperties: false } };

it("a seated person surrenders from the game bar after a confirmation, with the server's revision", async () => {
  const root = document.createElement("main"); document.body.append(root);
  const sent: unknown[] = [];
  const mounted = mountDiceCircuitView(root, { gameId: "dice_circuit", boardSpec: sheet as any, rulesText: "",
    onCommand: async input => { sent.push(input); } });
  const button = root.querySelector<HTMLButtonElement>(".tc-resign")!;
  mounted.update(view(0), [], { decision_id: null, actions: [] });
  expect(button.hidden).toBe(true);
  mounted.update(view(0), [], { decision_id: null, actions: [], commands: [resign], expected_revision: 12 } as any);
  expect(button.hidden).toBe(false);
  button.click();
  const dialog = root.querySelector(".modal.confirm")!;
  expect(dialog.querySelector("h3")!.textContent).toBe("Surrender the game?");
  expect(dialog.textContent).toContain("You leave the game; the table ends and the final sheets are shown.");
  [...dialog.querySelectorAll("button")].find(b => b.textContent === "🏳 Yes, I surrender")!.click();
  await Promise.resolve();
  expect(sent).toEqual([{ kind: "resign", params: {}, expectedRevision: 12 }]);
  mounted.dispose();
});

it("watchers, replays and a finished game have no Surrender; the log names who surrendered", () => {
  const root = document.createElement("main"); document.body.append(root);
  const onCommand = vi.fn(async () => {});
  const mounted = mountDiceCircuitView(root, { gameId: "dice_circuit", boardSpec: sheet as any, rulesText: "", onCommand });
  const button = root.querySelector<HTMLButtonElement>(".tc-resign")!;
  const offered = { decision_id: null, actions: [], commands: [resign], expected_revision: 4 } as any;
  mounted.update(view(null), [], offered);
  expect(button.hidden).toBe(true);
  mounted.update(view(0, { result: { scores: [3, 5], placements: [2, 1], reason: "resigned" }, resigned: 0 }), [], offered);
  expect(button.hidden).toBe(true);
  mounted.dispose();
  const replayRoot = document.createElement("main"); document.body.append(replayRoot);
  const replay = mountDiceCircuitView(replayRoot, { gameId: "dice_circuit", boardSpec: sheet as any, rulesText: "", onCommand, readOnly: true });
  replay.update(view(0, {}, "replay"), [], offered);
  expect(replayRoot.querySelector<HTMLButtonElement>(".tc-resign")!.hidden).toBe(true);
  replay.dispose();
  expect(describeDiceEvent({ kind: "resigned", payload: { player: 1 } }, ["Ala", "Ola"])).toBe("Ola surrenders; the game ends");
});
