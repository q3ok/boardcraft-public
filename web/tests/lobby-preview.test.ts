import { afterEach, beforeEach, expect, it, vi } from "vitest";
import * as THREE from "three";
import { mountDiceCircuitView } from "../src/games/dice-circuit/view";
import { mountRentcraftView } from "../src/games/rentcraft/view";
import { DiceCircuitScene } from "../src/scene/dice-circuit";
import { RentcraftScene } from "../src/games/rentcraft/scene";
import { LegalSelection } from "../src/model";
import sheet from "../../src/boardcraft/games/dice_circuit/data/base-sheet.json";
import spaces from "../../src/boardcraft/games/rentcraft/data/board.en.json";

beforeEach(() => {
  globalThis.ResizeObserver = class { observe() {} unobserve() {} disconnect() {} } as any;
  HTMLDialogElement.prototype.close = function () {};
  document.body.replaceChildren();
});
afterEach(() => vi.restoreAllMocks());

const seat = (n: number, kind: string, occupied: boolean, name = kind === "agent" ? "Agent" : "Human") => ({ seat: n, kind, occupied, display_name: name });
const lobby = (game: string, seats: ReturnType<typeof seat>[]) => ({
  table_id: "t".repeat(32), status: "lobby", config: { game_id: game, variant: "base", players: seats.length },
  observation: null, seats,
});

/** A recording 2D context so that the real sheet and board painters can run without a browser canvas. */
function recordCanvas() {
  const texts: string[] = [];
  const context: any = new Proxy({}, {
    get(target: any, key) {
      if (key in target) return target[key];
      return (...args: unknown[]) => {
        if (key === "fillText") texts.push(String(args[0]));
        if (key === "createImageData") return { data: new Uint8ClampedArray(Number(args[0]) * Number(args[1]) * 4) };
        if (key === "getImageData") return { data: new Uint8ClampedArray(Number(args[2]) * Number(args[3]) * 4) };
        return key === "measureText" ? { width: 10 } : { addColorStop() {} };
      };
    },
    set(target: any, key, value) { target[key] = value; return true; },
  });
  vi.spyOn(HTMLCanvasElement.prototype, "getContext").mockImplementation((() => context) as any);
  return texts;
}
function fakeTable() {
  return {
    scene: new THREE.Scene(), canvas: document.createElement("canvas"), container: document.createElement("div"),
    camera: new THREE.PerspectiveCamera(), renderer: { capabilities: { getMaxAnisotropy: () => 1 }, shadowMap: {} },
    controls: { addEventListener() {}, removeEventListener() {} },
    registerCamera() {}, setCamera() {}, invalidateShadows() {}, requestRender() {}, animate: () => () => {}, pick: () => null,
  } as any;
}

it("lobbyPreview Dice widget renders a null-observation lobby and refreshes seats on every update", () => {
  const root = document.createElement("main");
  document.body.append(root);
  const game = mountDiceCircuitView(root, { gameId: "dice_circuit", boardSpec: sheet as any, rulesText: "Rules" });
  const view = lobby("dice_circuit", [seat(0, "human", false), seat(1, "agent", false)]);
  game.update(view as any);
  const status = () => root.querySelector(".bc-status")!.textContent;
  const scores = () => [...root.querySelectorAll(".bc-scores button")].map(b => b.textContent);
  expect(status()).toBe("Waiting for players · 0 of 2 seats ready");
  expect(scores()).toEqual(["HumanOpen", "AgentOpen"]);
  expect(view.observation).toBeNull();
  game.update({ ...view, seats: [seat(0, "human", false), seat(1, "agent", true)] } as any);
  expect(status()).toBe("Waiting for players · 1 of 2 seats ready");
  expect(scores()).toEqual(["HumanOpen", "AgentReady"]);
  expect(root.textContent).not.toMatch(/Round \d|Roll \d|Your turn/);
  game.dispose();
});

it("lobbyPreview Rentcraft widget shows neutral seats from a null observation and refreshes them", () => {
  const root = document.createElement("main");
  document.body.append(root);
  const game = mountRentcraftView(root, { gameId: "rentcraft", boardSpec: { spaces, station_rents: [25], utility_multipliers: [4], cards: {} } as any, rulesText: "Rules" });
  const view = lobby("rentcraft", [seat(0, "agent", false), seat(1, "agent", false)]);
  game.update(view as any);
  const rows = () => [...root.querySelectorAll(".rc-hud .pl")].map(r => r.textContent!.replace(/\s+/g, " ").trim());
  expect(root.querySelector(".actions-panel h4")!.textContent).toBe("Waiting for players");
  expect(root.querySelector(".rc-hud .panel h4")!.textContent).toBe("Seats · 0 of 2 ready");
  expect(rows()).toEqual(["Player 1Waiting for an agent🎥", "Player 2Waiting for an agent🎥"]);
  game.update({ ...view, seats: [seat(0, "agent", true), seat(1, "agent", false)] } as any);
  expect(rows()).toEqual(["Player 1Agent joined🎥", "Player 2Waiting for an agent🎥"]);
  // A seat named by the table creator (or by the agent that joined it) shows that name; bots keep theirs in the status.
  game.update({ ...view, seats: [seat(0, "agent", true, "Robin"), { ...seat(1, "human", false, "Alice") }] } as any);
  expect(rows()).toEqual(["RobinAgent joined🎥", "AliceOpen seat🎥"]);
  expect([...root.querySelectorAll(".rc-tags .seat-tag .nm")].map(n => n.textContent)).toEqual(["Robin", "Alice"]);
  expect(root.textContent).not.toMatch(/\$\d|Round \d/);
  const data = { phase: "pre_roll", players: [0, 1].map(id => ({ id, cash: 90, position: 0, jail_cards: [], bankrupt: false })), owner: spaces.map(() => null), buildings: spaces.map(() => 0), mortgaged: spaces.map(() => false), round: 1, current: 0, debts: [], event_cursor: 0 };
  game.update({ ...view, status: "playing", observation: { viewer: null, decision: null, revision: 0, data } } as any);
  expect(rows().join()).toContain("$90");
  game.dispose();
});

it("lobbyPreview Dice scene draws blank sheets per seat, no dice and no observation, then re-seats for the real viewer", () => {
  const texts = recordCanvas();
  const scene = new DiceCircuitScene(sheet as any, new LegalSelection(), () => {}, () => {});
  scene.mount(fakeTable());
  const preview = lobby("dice_circuit", [seat(0, "human", false), seat(1, "agent", true)]);
  scene.update(preview as any, [], []);
  const papers = () => scene.group.children.filter(c => c.children.some(m => m.userData.paper));
  expect(papers()).toHaveLength(2);
  expect(texts).toEqual(expect.arrayContaining(["Human", "Agent"]));
  expect(texts).not.toContain("You");
  const dice = scene.group.children.filter(c => c.userData.dieId);
  expect(dice).toHaveLength(6);
  expect(dice.every(d => !d.visible)).toBe(true);
  expect(preview.observation).toBeNull();
  const blank = (n: number) => ({ yellow: (sheet as any).yellow.premarked, blue: [], green: 0, orange: [], purple: [], earned_rewards: [], rerolls: 0, extras: 0, emblem_count: 0, n });
  const observation = { game_id: "dice_circuit", viewer: 1, revision: 0, decision: null, data: {
    round: 1, active_player: 0, phase: "passive", regular_opportunity: 0, event_cursor: 0,
    dice: Object.fromEntries(["yellow", "blue", "green", "orange", "purple", "white"].map(id => [id, { value: 2, zone: "tray" }])),
    sheets: [blank(0), blank(1)] } };
  scene.update({ ...preview, status: "playing", observation } as any, [], []);
  expect(papers()).toHaveLength(2);
  const own = papers().find(p => p.children.find(m => m.userData.paper)!.userData.paper.seat === 1)!;
  expect([own.position.x, own.position.z]).toEqual([-3.5, 1.5]);
  expect(texts).toContain("You");
  expect(scene.group.children.filter(c => c.userData.dieId).every(d => d.visible)).toBe(true);
  scene.dispose();
});

it("lobbyPreview Rentcraft scene shows the real board but no fabricated roll before an observation", () => {
  recordCanvas();
  const scene = new RentcraftScene({ spaces, station_rents: [25], utility_multipliers: [4], cards: {} } as any, () => {}, () => {}, () => {});
  scene.mount(fakeTable());
  const dice = (scene as any).dice as THREE.Mesh[];
  expect(dice).toHaveLength(2);
  expect(dice.every(d => !d.visible)).toBe(true);
  expect(() => scene.update(lobby("rentcraft", [seat(0, "human", false), seat(1, "human", false)]) as any, [])).not.toThrow();
  expect(dice.every(d => !d.visible)).toBe(true);
  scene.dispose();
});
