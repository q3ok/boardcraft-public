import { expect, it, vi } from "vitest";
import { tileWorld, uvToIndex, seatRotation } from "../src/games/rentcraft/geometry";
import { animals, createPieces } from "../src/games/rentcraft/pieces";
import { presentationEvents } from "../src/games/rentcraft/events";
it("picks every space from its world centre on all four edges", () => {
  for (let i = 0; i < 40; i++) {
    const { x, z } = tileWorld(i);
    expect(uvToIndex(x / 10 + .5, .5 - z / 10)).toBe(i);
    expect(seatRotation(Math.floor(i / 10))).toBe(-Math.floor(i / 10) * Math.PI / 2);
  }
  expect(uvToIndex(.5, .5)).toBe(-1);
});
it("mounts independent sculpted animal models and both building shapes", () => {
  const one = createPieces(), two = createPieces();
  expect(animals).toEqual(["elephant", "giraffe", "lion", "rhinoceros", "zebra", "hippopotamus", "crocodile", "gorilla"]);
  for (const animal of animals)
    expect(one.animalPawn(animal, "red").children[1].children.length).toBeGreaterThan(3);
  expect(one.building(false).children.length).toBe(2);
  expect(one.building(true).children[0]).not.toBe(two.building(true).children[0]);
  one.dispose();
  two.dispose();
});
it("animates a contiguous receipt once and settles skipped, reversed and replay snapshots", () => {
  const roll = { sequence: 12, kind: "roll", payload: { player: 0, dice: [3, 4] } };
  expect(presentationEvents(11, 12, [roll], false)).toEqual([roll]);
  expect(presentationEvents(12, 12, [roll], false)).toEqual([]);
  expect(presentationEvents(9, 12, [roll], false)).toEqual([]);
  expect(presentationEvents(12, 11, [roll], false)).toEqual([]);
  expect(presentationEvents(11, 12, [roll], true)).toEqual([]);
});
import { mountRentcraftView } from "../src/games/rentcraft/view";
import spaces from "../../src/boardcraft/games/rentcraft/data/board.en.json";
const press = (inside: ParentNode, text: string) => [...inside.querySelectorAll("button")].find(b => b.textContent?.trim() === text)!.click();
it("uses the protected table revision, keeps a rejected offer draft only on its decision and prevents spectator commands", async () => {
  const boardSpec = { spaces, station_rents: [25], utility_multipliers: [4], cards: {} }, root = document.createElement("main");
  document.body.append(root);
  const command = vi.fn(async () => { throw Error("Rejected offer"); });
  const options = { gameId: "rentcraft", boardSpec, rulesText: "Rules", onCommand: command };
  const data = { phase: "pre_roll", players: [0, 1].map(id => ({ id, cash: 90, position: 0, jail_cards: [], bankrupt: false })), owner: spaces.map((s, i) => i === 1 ? 0 : null), buildings: spaces.map(() => 0), mortgaged: spaces.map(() => false), round: 1, current: 0, debts: [], event_cursor: 3 };
  const view = { config: { players: 2 }, status: "playing", event_cursor: 3, observation: { viewer: 0, decision: { id: "d1" }, revision: 2, data } };
  const actions = { expected_revision: 19, decision_id: "d1", commands: [{ kind: "propose_trade", scope: "decision", label: "Offer trade", params_schema: {} }], actions: [] };
  const game = mountRentcraftView(root, options);
  game.update(view, [], actions);
  root.querySelector<HTMLButtonElement>(".trade-btn")!.click();
  await vi.waitFor(() => expect(root.querySelector('[aria-label="Give cash"]')).not.toBeNull());
  root.querySelector<HTMLInputElement>('[aria-label="Give cash"]')!.value = "12";
  press(root, "Send offer");
  await vi.waitFor(() => expect(root.querySelector(".tw-error")?.textContent).toContain("Rejected offer"));
  expect(command).toHaveBeenCalledWith({ expectedRevision: 19, decisionId: "d1", kind: "propose_trade", params: expect.objectContaining({ target: 1, give_cash: 12 }) });
  expect(root.querySelector<HTMLInputElement>('[aria-label="Give cash"]')!.value).toBe("12");
  game.update(view, [], actions);
  expect(root.querySelector<HTMLInputElement>('[aria-label="Give cash"]')!.value).toBe("12");
  game.update({ ...view, observation: { ...view.observation, decision: { id: "d2" } } }, [], { ...actions, decision_id: "d2" });
  expect(root.querySelector('[aria-label="Give cash"]')).toBeNull();
  game.dispose();
  const watch = mountRentcraftView(root, { ...options, readOnly: true });
  watch.update(view, [], actions);
  expect(root.querySelector(".trade-btn")).toBeNull();
  expect(root.querySelector<HTMLElement>(".tc-resign")!.hidden).toBe(true);
  watch.dispose();
  root.remove();
});
it("restores the visible history from an observation after reconnect without showing a new draw", () => {
  const root = document.createElement("main"), boardSpec = { spaces, cards: { opportunity: [{ id: 3, text: "Synthetic revealed card" }] } };
  const game = mountRentcraftView(root, { gameId: "rentcraft", boardSpec, rulesText: "", readOnly: true });
  game.update({ status: "playing", event_cursor: 81, observation: { revision: 30, viewer: null, data: { phase: "post_roll", round: 5, current: 0, players: [], memory: { log: [{ kind: "card", who: 0, round: 5, card: ["opportunity", 3] }] } } } }, [], { actions: [], decision_id: null });
  expect(root.querySelector(".rc-hud .log-lines")!.textContent).toContain("Player 1 draws Opportunity: Synthetic revealed card");
  expect(root.querySelector(".card-show")).toBeNull();
  game.dispose();
});
it("shows a newly drawn card once and writes the move to the log in words", () => {
  const root = document.createElement("main"), boardSpec = { spaces, cards: { community_fund: [{ id: 1, text: "A bank error in your favour." }] } };
  const game = mountRentcraftView(root, { gameId: "rentcraft", boardSpec, rulesText: "", readOnly: true });
  const frame = (cursor: number) => ({ status: "playing", event_cursor: cursor, observation: { revision: cursor, viewer: null, data: { phase: "post_roll", round: 2, current: 0, players: [0, 1].map(id => ({ id, cash: 1500, position: 2, jail_cards: [], bankrupt: false })), memory: { log: [] } } } });
  const draw = { sequence: 5, kind: "card", payload: { player: 0, deck: "community_fund", card: 1 } };
  try {
    game.update(frame(4));
    game.update(frame(5), [draw]);
    expect(root.querySelectorAll(".card-show")).toHaveLength(1);
    expect(root.querySelector(".cs-text")!.textContent).toBe("A bank error in your favour.");
    expect(root.querySelector(".log-lines")!.textContent).toContain("Player 1 draws Community Fund: A bank error in your favour.");
    game.update(frame(5), [draw]);
    expect(root.querySelectorAll(".card-show")).toHaveLength(1);
  } finally { game.dispose(); }
});
it("opens an incoming offer, counter with reversed terms, and takeover with the actual repayment and interest choices", async () => {
  const root = document.createElement("main");
  document.body.append(root);
  const spec = { spaces: spaces.map(s => ({ ...s, mortgage_value: s.price / 2 })), cards: {}, station_rents: [25, 50, 100, 200], utility_multipliers: [4, 10] };
  const onAction = vi.fn(), onCommand = vi.fn(async () => { });
  const game = mountRentcraftView(root, { gameId: "rentcraft", boardSpec: spec, rulesText: "Rules", onAction, onCommand });
  const offer = { proposer: 1, target: 0, give_cash: 42, get_cash: 12, give_spaces: [3], get_spaces: [1], give_cards: 1, get_cards: 0 };
  const state = { round: 1, current: 1, phase: "trade_response", acting_seat: 0, trade: { offer, history: [offer], exchanges: 1 }, debts: [], players: [0, 1].map(id => ({ id, position: 10, cash: 120, jail_cards: [], bankrupt: false })), owner: spaces.map(s => s.index === 1 ? 0 : s.index === 3 ? 1 : null), mortgaged: spaces.map(s => s.index === 1), buildings: spaces.map(() => 0) };
  const base = { status: "playing", observation: { revision: 6, viewer: 0, data: state, decision: { id: "d6" } } };
  const manage = { id: "sell_building:1", kind: "sell_building", params: { space: 1 }, label: "Sell a building on Mill Street" };
  const menu = { expected_revision: 15, decision_id: "d6", actions: [{ id: "accept_trade", kind: "accept_trade", label: "Accept", params: {} }, manage], commands: [{ kind: "counter_trade", scope: "decision", label: "Counter" }] };
  game.update(base, [], menu);
  const dialog = () => root.querySelector<HTMLElement>(".tw-win");
  expect(dialog()!.textContent).toContain("$42");
  expect(dialog()!.textContent).toContain("Player 2 proposes a trade (offer 1 of 4");
  press(dialog()!, "Manage property…");
  expect(dialog()).toBeNull();
  game.update(base, [], menu);
  expect(dialog()).toBeNull();
  press(root, "Show the offer");
  press(dialog()!, "Counter-offer…");
  expect(dialog()!.querySelector<HTMLInputElement>('[aria-label="Give cash"]')!.value).toBe("12");
  press(dialog()!, "Send counter-offer");
  await vi.waitFor(() => expect(onCommand).toHaveBeenCalledOnce());
  expect(onCommand.mock.calls[0]).toMatchObject([{ expectedRevision: 15, decisionId: "d6", kind: "counter_trade", params: { target: 1, give_cash: 12, get_cash: 42, give_spaces: [1], get_spaces: [3] } }]);
  game.update({ ...base, observation: { ...base.observation, revision: 7, decision: { id: "d7" }, data: { ...state, phase: "mortgage_takeover", trade: null, pending_takeovers: [[0, 1]] } } }, [], { decision_id: "d7", actions: [{ id: "pass", kind: "pass", label: "Pass", params: {} }, { id: "unmortgage:1", kind: "unmortgage", params: { space: 1 }, label: "Repay the mortgage on Mill Street" }] });
  expect(dialog()!.textContent).toContain("Taken-over mortgaged space: Mill Street");
  expect(dialog()!.textContent).toContain("Pay off at once ($33)");
  press(dialog()!, "Interest only ($3)");
  expect(onAction).toHaveBeenCalledWith({ decisionId: "d7", actionId: "pass" });
  game.dispose();
  root.remove();
});
it("claims an advertised timed offer window and opens the composer on the returned decision", async () => {
  const root = document.createElement("main");
  document.body.append(root);
  const actions = { decision_id: "w1", expected_revision: 9, actions: [], commands: [{ kind: "request_trade", scope: "seat", label: "Request a trade window" }, { kind: "propose_trade", scope: "decision", label: "Propose a trade" }] };
  const data = { players: [0, 1].map(id => ({ id, cash: 100, position: 0, jail_cards: [] })), round: 1, phase: "trade_window", acting_seat: 0, window: { kind: "payment", serial: 4, payments: [[1, 0, 28, {}]] }, owner: spaces.map(() => null), mortgaged: spaces.map(() => false), event_cursor: 1 };
  const view = { status: "playing", event_cursor: 1, control: { deadline: { kind: "window", at: 1234, effect: "pass" } }, server_time: 1231, observation: { viewer: 0, decision: { id: "w1" }, revision: 1, data } };
  let claimed = false;
  const game = mountRentcraftView(root, { gameId: "rentcraft", boardSpec: { spaces, cards: {} }, rulesText: "", onCommand: async (input) => {
      expect(input).toEqual({ kind: "request_trade", expectedRevision: 9, params: {} });
      game.update({ ...view, control: { deadline: null }, observation: { ...view.observation, revision: 2, decision: { id: "w2" } } }, [], { ...actions, decision_id: "w2", expected_revision: 10, commands: actions.commands.slice(1) });
      claimed = true;
    } });
  game.update(view, [], actions);
  expect(root.querySelector(".window-info")!.textContent).toMatch(/Offer window: a payment - \d s · you decide · Player 2 will pay \$28/);
  root.querySelector<HTMLButtonElement>(".trade-btn")!.click();
  await vi.waitFor(() => expect(claimed).toBe(true));
  expect(root.querySelector('[aria-label="Give cash"]')).not.toBeNull();
  game.dispose();
  root.remove();
});
it("confirms Surrender in the game bar and sends the seat's resign command", async () => {
  const root = document.createElement("main");
  document.body.append(root);
  const onCommand = vi.fn(async () => { });
  const game = mountRentcraftView(root, { gameId: "rentcraft", boardSpec: { spaces, cards: {} }, rulesText: "", onCommand });
  const data = { phase: "post_roll", round: 3, current: 1, players: [0, 1].map(id => ({ id, cash: 300, position: 0, jail_cards: [], bankrupt: false })), event_cursor: 2 };
  game.update({ status: "playing", event_cursor: 2, observation: { viewer: 0, decision: null, revision: 4, data } }, [], { decision_id: null, expected_revision: 21, actions: [], commands: [{ kind: "resign", scope: "seat", label: "Resign" }] });
  const resign = root.querySelector<HTMLButtonElement>(".tc-resign")!;
  expect(resign.hidden).toBe(false);
  resign.click();
  press(root.querySelector(".modal.confirm")!, "🏳 Yes, I surrender");
  await vi.waitFor(() => expect(onCommand).toHaveBeenCalledWith({ kind: "resign", expectedRevision: 21, params: {} }));
  game.dispose();
  root.remove();
});
import * as THREE from "three";
import * as textures from "../src/games/rentcraft/textures";
import { RentcraftScene } from "../src/games/rentcraft/scene";
import type { SharedTable3D } from "../src/scene/table";
it("lays out shared jail, each development level, mortgages and transferred deeds in the mounted table", () => {
  for (const name of ["boardTexture", "deckTexture", "deedTexture", "jailCardTexture", "pipTexture"] as const)
    vi.spyOn(textures, name).mockImplementation(() => new THREE.Texture() as THREE.CanvasTexture);
  const world = new THREE.Scene(), canvas = document.createElement("canvas"), camera = new THREE.PerspectiveCamera();
  const animate = vi.fn((_fn: (now: number, end: boolean) => boolean) => () => { });
  const invalidateShadows = vi.fn();
  const pick = vi.fn();
  const table = { scene: world, canvas, camera, renderer: { capabilities: { getMaxAnisotropy: () => 1 } }, controls: new THREE.EventDispatcher(), registerCamera: vi.fn(), setCamera: vi.fn(), requestRender: vi.fn(), invalidateShadows, animate, pick } as unknown as SharedTable3D;
  const spec = { spaces: spaces.map(s => ({ ...s, mortgage_value: s.price / 2 })), cards: {}, station_rents: [], utility_multipliers: [], start_salary: 200 };
  const scene = new RentcraftScene(spec, () => { }, () => { }, () => { });
  scene.mount(table);
  const board = world.children[0];
  const players = [0, 1].map(id => ({ id, cash: 100, position: 10, in_jail: true, jail_cards: [], bankrupt: false }));
  const owner = spaces.map(s => s.index === 1 ? 0 : null);
  const frame = (revision: number, level: number, seat: number, mortgage = false, replay = false, rolls = 0) => {
    const data = { players, owner: owner.map((v, i) => i === 1 ? seat : v), buildings: spaces.map(s => s.index === 1 ? level : 0), mortgaged: spaces.map(s => s.index === 1 && mortgage), roll_count: rolls, last_roll: [2, 4], event_cursor: revision };
    scene.update({ status: replay ? "replay" : "playing", event_cursor: revision, observation: { revision, data } } as any, [{ sequence: revision, kind: rolls ? "roll" : "build", payload: { player: 0, dice: [2, 4] } }]);
  };
  try {
    for (let level = 0; level <= 5; level++) {
      frame(level, level, 0);
      const buildings = board.children.find(g => g.userData.level === level);
      expect(buildings?.children.length ?? 0).toBe(level === 5 ? 1 : level);
    }
    const pawns = board.children.filter(o => o.scale.x === 1.4);
    expect(pawns).toHaveLength(2);
    expect(pawns[0].position.equals(pawns[1].position)).toBe(false);
    frame(6, 0, 1, true);
    expect(textures.deedTexture).toHaveBeenCalledWith(spec, 1, true);
    let deed: THREE.Object3D | undefined;
    board.traverse(o => { if (o.userData.space === 1)
      deed = o; });
    expect(deed?.userData.owner).toBe(1);
    expect(animate).toHaveBeenCalledTimes(1);
    pick.mockReturnValue({ object: deed });
    canvas.dispatchEvent(new MouseEvent("pointermove", { clientX: 10, clientY: 12 }));
    const glow = board.children.find(o => (o as THREE.Mesh).geometry?.type === "PlaneGeometry") as THREE.Mesh<THREE.PlaneGeometry, THREE.MeshBasicMaterial>;
    expect(glow.visible).toBe(true);
    expect(glow.material.opacity).toBeGreaterThan(0);
    frame(7, 0, 1, true, false, 1);
    expect(animate).toHaveBeenCalledTimes(2);
    frame(8, 0, 1, true, false, 1);
    expect(animate).toHaveBeenCalledTimes(2);
    frame(33, 0, 0, false, false, 4);
    frame(4, 4, 0, false, true, 0);
    invalidateShadows.mockClear();
    frame(4, 4, 0, false, true, 0);
    expect(invalidateShadows).not.toHaveBeenCalled();
    expect(animate).toHaveBeenCalledTimes(2);
  }
  finally {
    scene.dispose();
    vi.restoreAllMocks();
  }
  expect(world.children).toHaveLength(0);
});
it("prints the module's actual release card text and deck name on its 3D card",()=>{
  const printed:string[]=[];
  const g={fillRect:vi.fn(),strokeRect:vi.fn(),fillText:(s:string)=>printed.push(s),measureText:(s:string)=>({width:s.length*8})};
  const ctx=vi.spyOn(HTMLCanvasElement.prototype,"getContext").mockReturnValue(g as any);
  try{
    const spec={spaces:[{kind:"opportunity",name:"Fixture deck"}],cards:{opportunity:[{effect:"jail_free",text:"A test release. Keep the revealed card."}]}};
    const texture=(textures.jailCardTexture as any)(spec,"opportunity");
    expect(printed.join(" ").toUpperCase()).toContain("FIXTURE DECK");
    expect(printed.join(" ").toUpperCase()).toContain("KEEP THE REVEALED CARD.");
    texture.dispose();
  }finally{ctx.mockRestore();}
});
import { deedHtml } from "../src/games/rentcraft/deed";
it("highlights the scheduled rent for the current owner, portfolio, mortgage and buildings", () => {
  const board = { spaces: spaces.map(s => ({ ...s, mortgage_value: s.price / 2 })), start_salary: 200, cards: {}, station_rents: [25,50,100,200], utility_multipliers: [4,10] };
  const state = { owner: spaces.map(() => null as number | null), mortgaged: spaces.map(() => false), buildings: spaces.map(() => 0) };
  const active = (i: number) => {
    const card = document.createElement("div");
    card.innerHTML = deedHtml(board as any, state as any, i, ["Ann", "Bob"]);
    return Array.from(card.querySelectorAll("tr.now td:last-child"), r => r.textContent?.replace(" ◀", ""));
  };
  expect(active(1)).toEqual([]);
  state.owner[1] = 0; expect(active(1)).toEqual(["$2"]);
  state.owner[3] = 0; state.mortgaged[3] = true; expect(active(1)).toEqual(["$4"]);
  for (let i = 1; i <= 5; ++i) { state.buildings[1] = i; expect(active(1)).toEqual(["$" + spaces[1].rents[i]]); }
  state.mortgaged[1] = true; expect(active(1)).toEqual([]);
  for (const [i, space] of spaces.filter(s => s.kind === "railroad").entries()) {
    state.owner[space.index] = 1; expect(active(5)).toEqual(["$" + board.station_rents[i]]);
  }
  state.owner[12] = 1; expect(active(12)).toEqual(["4 × dice total"]);
  state.owner[28] = 1; expect(active(12)).toEqual(["10 × dice total"]);
  state.mortgaged[12] = true; expect(active(12)).toEqual([]);
});
it("appends contiguous receipts to reconnect history once and replaces that history across a gap", () => {
  const root = document.createElement("div");
  const boardSpec = { spaces, cards: { opportunity: [{ id: 3, text: "Earlier card" }] } };
  const game = mountRentcraftView(root, { gameId: "rentcraft", rulesText: "", boardSpec });
  const frame = (cursor: number, memory: unknown[], viewer: number | null = null) => ({ status: "playing", event_cursor: cursor,
    observation: { viewer, revision: cursor, data: { phase: "post_roll", round: 5, current: 0, players: [], memory: { log: memory } } } });
  const events = [{ kind: "roll", sequence: 8, payload: { player: 0, dice: [2,3] } }];
  const history = () => root.querySelector(".log-lines")!.textContent!;
  try {
    game.update(frame(7, [{ kind: "card", who: 1, round: 4, card: ["opportunity",3] }]));
    game.update(frame(8, []), events);
    game.update(frame(8, []), events);
    expect(history()).toContain("Earlier card");
    expect(history().match(/2 \+ 3/g)).toHaveLength(1);
    game.update(frame(17, [{ kind: "pass", who: 0, round: 5 }]));
    expect(history()).toContain("pass"); expect(history()).not.toContain("Earlier card");
    game.update(frame(17, [], 1));
    expect(history()).not.toContain("pass");
  } finally { game.dispose(); }
});
it("keeps the shared HUD width on wide tables and moves the panel under the board on a phone", async () => {
  const fsModule = "node:fs/promises", urlModule = "node:url";
  const { readFile } = await import(fsModule), { URL: FileURL } = await import(urlModule);
  const css: string = await readFile(new FileURL("../src/games/rentcraft/style.css", import.meta.url), "utf8");
  const [wide, phone] = css.split("@container (max-width: 759px)");
  expect(wide).toMatch(/\.rc-table \.hud \{[^}]*width:\s*var\(--rc-hud-width\)/);
  expect(wide).toMatch(/\.rc-table \.rc-top \{[^}]*max-width:\s*calc\(100%\s*-\s*var\(--rc-hud-width\)\s*-\s*\d+px\)/);
  expect(phone).toMatch(/\.rc-table \.hud \{[^}]*top:\s*auto;[^}]*width:\s*auto/);
  expect(phone).toMatch(/\.rc-table \.rc-top \{[^}]*max-width:\s*calc\(100% - 16px\);\s*flex-wrap:\s*nowrap/);
});
