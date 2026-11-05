import { it, expect, vi, beforeEach } from "vitest";
vi.mock("../src/scene/table", () => ({
  SharedTable3D: class {
    setCamera() {}
    setInsets() {}
    resize() {}
    dispose() {}
  },
}));
vi.mock("../src/scene/dice-circuit", () => ({
  DiceCircuitScene: class {
    mount() {}
    update() {}
    setCamera() {}
    setOpponent() {}
    dispose() {}
  },
}));
import { mountDiceCircuitView as mountGameView } from "../src/games/dice-circuit/view";
import type { View, ActionList } from "../src/games/dice-circuit/types";
import sheet from "../../src/boardcraft/games/dice_circuit/data/base-sheet.json";
beforeEach(() => {
  globalThis.ResizeObserver = class {
    observe() {}
    unobserve() {}
    disconnect() {}
  } as any;
  document.body.replaceChildren();
  HTMLDialogElement.prototype.close = function () {};
});
function fixture() {
  return {
    table_id: "table",
    status: "playing",
    config: { game_id: "dice_circuit", variant: "base", players: 2 },
    observation: {
      game_id: "dice_circuit",
      viewer: 1,
      revision: 4,
      decision: { id: "own", player: 1, kind: "passive" },
      data: {
        round: 4,
        active_player: 0,
        phase: "passive",
        regular_opportunity: 3,
        event_cursor: 1,
        dice: Object.fromEntries(
          ["yellow", "blue", "green", "orange", "purple", "white"].map((id) => [
            id,
            { value: 3, zone: "tray" },
          ]),
        ),
        sheets: [0, 1].map(() => ({
          yellow: sheet.yellow.premarked,
          blue: [],
          green: 0,
          orange: [],
          purple: [],
          earned_rewards: [],
          rerolls: 0,
          extras: 0,
          emblem_count: 0,
        })),
        window: {
          stage: "bonus",
          bonus_stack: {
            frames: [
              {
                pending: [
                  { source_id: "yellow:row:0", kind: "blueX" },
                  { source_id: "yellow:diagonal", kind: "extra" },
                ],
                ancestry: [],
              },
            ],
            current: null,
          },
        },
      },
    },
  } as unknown as View;
}
it("renders pending bonus choices as English controls without source IDs and emits exact owning context", () => {
  const root = document.createElement("main");
  document.body.append(root);
  const onAction = vi.fn();
  const app = mountGameView(root, {
    gameId: "dice_circuit",
    boardSpec: sheet,
    rulesText: "Rules",
    onAction,
  });
  const actions: ActionList = {
    decision_id: "own",
    actions: [
      {
        id: "opaque",
        kind: "choose_bonus",
        params: { bonus_id: "yellow:row:0" },
        label: "Resolve bonus yellow:row:0.",
      },
    ],
  };
  app.update(fixture(), [], actions);
  const b = [...root.querySelectorAll("button")].find(
    (b) => b.textContent === "Resolve Blue cross",
  )!;
  expect(b).toBeTruthy();
  expect(root.textContent).not.toContain("yellow:row:0");
  b.click();
  b.click();
  expect(onAction).toHaveBeenCalledExactlyOnceWith({
    decisionId: "own",
    actionId: "opaque",
  });
  app.dispose();
  expect(root.children).toHaveLength(0);
});
it("clears stale targets and keyboard handlers when unmounted", () => {
  const root = document.createElement("main");
  document.body.append(root);
  const action = {
    id: "field",
    kind: "select_die",
    params: { die_id: "white", area: "yellow", target: [0, 0] },
    label: "Use white die.",
  };
  const app = mountGameView(root, {
    gameId: "dice_circuit",
    boardSpec: sheet,
    rulesText: "Rules",
  });
  app.update(fixture(), [], { decision_id: "own", actions: [action] });
  window.dispatchEvent(new KeyboardEvent("keydown", { key: "6" }));
  expect(root.querySelector(".bc-target")?.textContent).toBe(
    "Yellow · row 1, col 1",
  );
  const next = fixture();
  next.observation!.revision++;
  next.observation!.decision!.id = "new";
  app.update(next, [], { decision_id: "own", actions: [action] });
  expect(root.querySelectorAll(".bc-target")).toHaveLength(0);
  app.dispose();
  window.dispatchEvent(new KeyboardEvent("keydown", { key: "6" }));
  expect(root.children).toHaveLength(0);
});
it("keeps keyboard focus during an unchanged poll", () => {
  const root = document.createElement("main");
  document.body.append(root);
  const actions = {
    decision_id: "own",
    actions: [
      {
        id: "field",
        kind: "select_die",
        params: { die_id: "white", area: "yellow", target: [0, 0] },
        label: "Use white die.",
      },
    ],
  };
  const app = mountGameView(root, {
    gameId: "dice_circuit",
    boardSpec: sheet,
    rulesText: "Rules",
  });
  app.update(fixture(), [], actions);
  const b = root.querySelector<HTMLButtonElement>(".bc-die-button:last-child")!;
  b.focus();
  app.update(fixture(), [], actions);
  expect(document.activeElement).toBe(b);
  app.dispose();
});
it("updates historical replay frames without remounting the table or enabling choices", () => {
  const root = document.createElement("main");
  document.body.append(root);
  const onAction = vi.fn();
  const app = mountGameView(root, {
    gameId: "dice_circuit",
    boardSpec: sheet,
    rulesText: "Rules",
    onAction,
  });
  const live = fixture();
  app.update(live, [], { decision_id: "own", actions: [] });
  const canvas = root.querySelector("canvas");
  const historical = fixture();
  historical.status = "replay";
  historical.observation!.revision = 1;
  historical.observation!.data.round = 1;
  app.update(historical, [], { decision_id: null, actions: [] });
  expect(root.querySelector("canvas")).toBe(canvas);
  expect(root.querySelector(".bc-status")?.textContent).toContain("Round 1");
  expect(root.querySelector(".bc-decision-title")?.textContent).toBe(
    "Reviewing the game",
  );
  app.dispose();
});
it("returns the same final observation from replay to result presentation", () => {
  const root = document.createElement("main");
  document.body.append(root);
  const app = mountGameView(root, {
    gameId: "dice_circuit",
    boardSpec: sheet,
    rulesText: "Rules",
  });
  const final = fixture();
  final.status = "replay";
  final.observation!.data.phase = "over";
  final.observation!.decision = null;
  const actions = { decision_id: null, actions: [] };
  app.update(final, [], actions);
  const canvas = root.querySelector("canvas");
  expect(root.querySelector(".bc-decision-title")?.textContent).toBe(
    "Reviewing the game",
  );
  app.update({ ...final, status: "completed" }, [], actions);
  expect(root.querySelector(".bc-decision-title")?.textContent).toBe(
    "Game finished",
  );
  expect(root.querySelector("canvas")).toBe(canvas);
  app.dispose();
});

it("shows the bonus phase and distinct emblem count and points", () => {
  const root = document.createElement("main");
  document.body.append(root);
  const app = mountGameView(root, {
    gameId: "dice_circuit", boardSpec: sheet, rulesText: "Rules",
  });
  const view = fixture();
  Object.assign(view.observation!.data, {
    phase: "bonus", turn_phase: "passive",
    score_summary: [
      { areas: { yellow: 0, blue: 0, green: 0, orange: 0, purple: 0 }, emblem_points: 0, total: 0 },
      { areas: { purple: 33, orange: 25, blue: 29, yellow: 26, green: 28 }, emblem_points: 50, total: 191 },
    ],
  });
  Object.assign(view.observation!.data.sheets[1], { emblem_count: 2 });
  app.update(view, [], { decision_id: "own", actions: [] });
  expect(root.querySelector(".bc-status")?.textContent).toContain("bonus chain");
  expect(root.querySelectorAll<HTMLButtonElement>(".bc-score")[1].title).toContain("Emblem points: 50");
  expect(root.querySelectorAll<HTMLButtonElement>(".bc-score")[1].title).toContain("yellow: 26 · blue: 29 · green: 28 · orange: 25 · purple: 33");
  expect(root.querySelector(".bc-resources .bc-resource-row:last-child")?.textContent).toMatch(/2$/);
  app.dispose();
});

const rentPick = vi.hoisted(() => ({ select: undefined as undefined | ((index: number, x: number, y: number) => void) }));
vi.mock("../src/games/rentcraft/scene", () => ({
  appearance: {}, RentcraftScene: class {
    constructor(_board: unknown, _hover: unknown, pick: typeof rentPick.select) { rentPick.select = pick; }
    mount() {} update() {} dispose() {}
  },
}));
import { mountRentcraftView } from "../src/games/rentcraft/view";
import rentSpaces from "../../src/boardcraft/games/rentcraft/data/board.en.json";
it("refreshes the pinned deed after a transfer without requiring a second pick", () => {
  const root = document.createElement("div"), boardSpec = { spaces: rentSpaces, station_rents: [], utility_multipliers: [], cards: {} };
  const game = mountRentcraftView(root, { gameId: "rentcraft", boardSpec, rulesText: "", readOnly: true });
  const display = (owner: number) => game.update({ status: "playing", event_cursor: owner, observation: { viewer: null, revision: owner, data: {
    phase: "post_roll", players: [], owner: rentSpaces.map(s => s.index === 1 ? owner : null), mortgaged: rentSpaces.map(() => false), buildings: rentSpaces.map(() => 0) } } });
  try {
    display(0); rentPick.select!(1, 10, 20);
    const tip = root.querySelector<HTMLElement>(".rc-tip")!;
    expect(tip.textContent).toContain("Player 1");
    display(1);
    expect(tip.style.display).toBe("block");
    expect(tip.textContent).toContain("Owner: Player 2");
    expect(tip.textContent).not.toContain("Player 1");
    expect(tip.querySelector("button")?.getAttribute("aria-label")).toBe("Close");
  } finally { game.dispose(); }
});
it.each(["dice_circuit"])("requests the embedding replay stage for %s fullscreen", gameId => {
  const stage = document.createElement("section"), root = document.createElement("div");
  stage.append(document.createElement("nav"), root);
  stage.requestFullscreen = vi.fn(async () => {});
  const options = { gameId, rulesText: "", fullscreenRoot: stage };
  const game = gameId === "rentcraft" ? mountRentcraftView(root, { ...options, boardSpec: { spaces: rentSpaces } }) : mountGameView(root, { ...options, boardSpec: sheet });
  try {
    [...root.querySelectorAll("button")].find(b => b.textContent?.endsWith("Full screen"))!.click();
    expect(stage.requestFullscreen).toHaveBeenCalledOnce();
  } finally { game.dispose(); }
});
