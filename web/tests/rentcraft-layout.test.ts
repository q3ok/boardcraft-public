import { afterEach, expect, it, vi } from "vitest";
import * as THREE from "three";
import { mountRentcraftView } from "../src/games/rentcraft/view";
import { SharedTable3D } from "../src/scene/table";
import spaces from "../../src/boardcraft/games/rentcraft/data/board.en.json";

vi.mock("three", async importOriginal => ({
  ...await importOriginal<typeof import("three")>(),
  WebGLRenderer: class {
    shadowMap = {};
    capabilities = { getMaxAnisotropy: () => 1 };
    renderLists = { dispose() {} };
    setPixelRatio() {} setSize() {} render() {} dispose() {} forceContextLoss() {}
  },
}));
vi.mock("../src/games/rentcraft/textures", async importOriginal => {
  const original = await importOriginal<typeof import("../src/games/rentcraft/textures")>();
  const texture = () => new THREE.Texture();
  return { ...original, boardTexture: texture, deckTexture: texture, pipTexture: texture, woodTexture: texture };
});

afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals(); document.body.replaceChildren(); });
const rect = (x: number, y: number, width: number, height: number) =>
  ({ x, y, left: x, top: y, width, height, right: x + width, bottom: y + height } as DOMRect);

it.each([
  { width: 800, height: 360, headerBottom: 109, guideTop: 302.5, hudStart: 462, narrow: false },
  { width: 360, height: 800, headerBottom: 96, guideTop: 800, hudStart: 432, narrow: true },
  { width: 1280, height: 720, headerBottom: 54, guideTop: 669, hudStart: 942, narrow: false },
])("keeps the preset inside the measured table overlays at $width × $height and leaves an idle orbit alone", ({ width, height, headerBottom, guideTop, hudStart, narrow }) => {
  const observers = new Map<Element, Set<() => void>>();
  vi.stubGlobal("ResizeObserver", class {
    constructor(readonly measure: () => void) {}
    observe(el: Element) {
      if (!observers.has(el)) observers.set(el, new Set());
      observers.get(el)!.add(this.measure);
    }
    disconnect() { for (const callbacks of observers.values()) callbacks.delete(this.measure); }
  });
  vi.stubGlobal("matchMedia", () => Object.assign(new EventTarget(), { matches: false }));
  const frames = new Map<number, FrameRequestCallback>();
  let frameId = 0;
  vi.stubGlobal("requestAnimationFrame", (cb: FrameRequestCallback) => { frames.set(++frameId, cb); return frameId; });
  vi.stubGlobal("cancelAnimationFrame", (id: number) => frames.delete(id));
  const drain = () => { const pending = [...frames.values()]; frames.clear(); pending.forEach(cb => cb(0)); expect(frames.size).toBe(0); };
  const resized = (el: Element) => observers.get(el)?.forEach(cb => cb());
  const insets = vi.spyOn(SharedTable3D.prototype, "setInsets");
  const root = document.createElement("main"); document.body.append(root);
  const game = mountRentcraftView(root, { gameId: "rentcraft", boardSpec: { spaces, cards: {}, station_rents: [], utility_multipliers: [] }, rulesText: "" });
  try {
    const host = root.querySelector<HTMLElement>(".rc-table")!, top = root.querySelector(".rc-top")!, hint = root.querySelector(".rc-hint")!, hud = root.querySelector(".rc-hud")!;
    const x = 32, y = 51; // Embedded table origin differs from the page origin.
    Object.defineProperties(host, { clientWidth: { value: width }, clientHeight: { value: height } });
    Object.defineProperties(hud, { offsetWidth: { value: narrow ? width : 330 }, offsetHeight: { value: narrow ? height - hudStart : height - 16 } });
    vi.spyOn(host, "getBoundingClientRect").mockReturnValue(rect(x, y, width, height));
    const topBounds = vi.spyOn(top, "getBoundingClientRect").mockReturnValue(rect(x + 3, y + 3, narrow ? width - 6 : hudStart - 20, headerBottom - 3));
    vi.spyOn(hint, "getBoundingClientRect").mockReturnValue(narrow ? rect(0, 0, 0, 0) : rect(x + 12, y + guideTop, hudStart - 20, height - guideTop - 8));
    vi.spyOn(hud, "getBoundingClientRect").mockReturnValue(narrow ? rect(x, y + hudStart, width, height - hudStart) : rect(x + hudStart, y + 8, 330, height - 16));
    resized(host);
    const observation = { status: "playing", event_cursor: 1, observation: { revision: 1, viewer: null, data: {
      phase: "post_roll", current: 0, round: 1, players: [], owner: spaces.map(() => null), buildings: spaces.map(() => 0), mortgaged: spaces.map(() => false) } } };
    game.update(observation);
    const table = insets.mock.contexts.at(-1) as SharedTable3D;
    function checkFit(t: number) {
      table.camera.updateMatrixWorld();
      for (const tileX of [-5, 5]) for (const tileZ of [-5, 5]) {
        const corner = new THREE.Vector3(tileX, .12, tileZ).project(table.camera);
        const px = (corner.x + 1) * width / 2, py = (1 - corner.y) * height / 2;
        expect(px).toBeGreaterThanOrEqual(0); expect(px).toBeLessThanOrEqual(narrow ? width : hudStart);
        expect(py).toBeGreaterThanOrEqual(t); expect(py).toBeLessThanOrEqual(narrow ? hudStart : guideTop);
      }
    }
    checkFit(headerBottom);
    expect(insets.mock.calls.at(-1)![0].top).toBeGreaterThanOrEqual(headerBottom);
    expect(insets.mock.calls.at(-1)![0].bottom).toBeGreaterThanOrEqual(height - (narrow ? hudStart : guideTop));
    expect(observers.get(hint)?.size).toBe(1);
    expect(observers.get(hud)?.size).toBe(1);
    const beforeWrap = insets.mock.calls.length;
    topBounds.mockReturnValue(rect(x + 3, y + 3, width / 2, headerBottom + 17));
    resized(top);
    expect(insets).toHaveBeenCalledTimes(beforeWrap + 1);
    checkFit(headerBottom + 20);
    table.controls.dispatchEvent({ type: "start" });
    table.camera.position.x += 2; table.controls.update();
    const userPosition = table.camera.position.clone();
    drain();
    const settled = insets.mock.calls.length, scheduled = frameId;
    game.update(observation); resized(top); resized(hint); resized(hud);
    expect(insets).toHaveBeenCalledTimes(settled);
    expect(table.camera.position.equals(userPosition)).toBe(true);
    expect(frameId).toBe(scheduled);
  } finally { game.dispose(); }
  expect([...observers.values()].every(callbacks => callbacks.size === 0)).toBe(true);
});

it("starts the 3D table when the host already has a size at mount (no early inset use)", () => {
  vi.stubGlobal("ResizeObserver", class { observe() {} disconnect() {} });
  vi.stubGlobal("matchMedia", () => Object.assign(new EventTarget(), { matches: false }));
  vi.stubGlobal("requestAnimationFrame", () => 1);
  vi.stubGlobal("cancelAnimationFrame", () => {});
  vi.spyOn(Element.prototype, "getBoundingClientRect").mockReturnValue(rect(0, 0, 1024, 768));
  const warn = vi.spyOn(console, "warn").mockImplementation(() => {});
  const insets = vi.spyOn(SharedTable3D.prototype, "setInsets");
  const root = document.createElement("main"); document.body.append(root);
  const game = mountRentcraftView(root, { gameId: "rentcraft", boardSpec: { spaces, cards: {}, station_rents: [], utility_multipliers: [] }, rulesText: "" });
  try {
    expect(warn).not.toHaveBeenCalled();
    expect(insets).toHaveBeenCalled();
    expect(root.querySelector<HTMLElement>(".rc-notice")!.hidden).toBe(true);
  } finally { game.dispose(); }
});

it("table screens fill the play stage (not the whole viewport) so a page header above them never adds page scrollbars", async () => {
  const fsModule = "node:fs", fs = await import(/* @vite-ignore */ fsModule);
  const read = (name: string): string => fs.readFileSync(`${(globalThis as any).process.cwd()}/src/${name}`, "utf8");
  const css = [read("style.css"), read("ui/table.css"), read("games/rentcraft/style.css")].join("\n").replace(/@import[^;]+;/g, "");
  const style = document.createElement("style"); style.textContent = css; document.head.append(style);
  const app = document.createElement("main"); app.className = "bc-app";
  app.innerHTML = '<div class="bc-stage tc-screen"><section class="rc-table tc-screen"></section><div class="bc-game dice-stage"></div></div>'
    + '<section class="bc-embedded-stage bc-stage tc-screen"><section class="rc-table tc-screen"></section></section>';
  document.body.append(app);
  try {
    const [table, embedded] = [...app.querySelectorAll<HTMLElement>(".rc-table")];
    expect(getComputedStyle(app.querySelector(".bc-stage")!).height).toBe("100%");
    expect(getComputedStyle(table).height).toBe("100%");
    expect(getComputedStyle(app.querySelector(".dice-stage")!).height).toBe("100%");
    // An embedded board (solo, tournament replay) sits in a scrolling page and keeps its viewport height.
    expect(getComputedStyle(embedded).height).toBe("100dvh");
  } finally { style.remove(); }
});
