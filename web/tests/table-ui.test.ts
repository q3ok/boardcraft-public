import { afterEach, expect, it, vi } from "vitest";
import { confirmDialog, gameBadge, mountGameBar, rulesDialog } from "../src/ui/gamebar";
import { mountReplayBar } from "../src/ui/replaybar";
import { describeDiceEvents } from "../src/games/dice-circuit/describe";
import { compactLog, eventLine, logHtml, memoryEvent } from "../src/games/rentcraft/log";
import { netWorth, seatNames, tradeableSpaces } from "../src/games/rentcraft/format";
import { phaseText, playersHtml } from "../src/games/rentcraft/panel";
import { tradeHints } from "../src/games/rentcraft/tradedialog";
import spaces from "../../src/boardcraft/games/rentcraft/data/board.en.json";

afterEach(() => { vi.useRealTimers(); document.body.replaceChildren(); });
const board = { spaces: spaces.map(s => ({ ...s, mortgage_value: Math.floor(s.price / 2) })), cards: { opportunity: [{ id: 3, text: "Advance to Start.", effect: "move" }] }, start_salary: 200, station_rents: [25, 50, 100, 200], utility_multipliers: [4, 10] } as any;
const names = ["Ann", "Bob", "Cid"];

it("the game bar shows the game's mark, the table, the connection, Rules and Surrender only when given", () => {
  const back = vi.fn(), rules = vi.fn(), resign = vi.fn();
  const bar = mountGameBar({ gameId: "rentcraft", title: "Table 4f27990b", onBack: back });
  document.body.append(bar.element);
  expect(bar.element.querySelector(".logo")!.textContent).toBe("RENTCRAFT");
  expect(gameBadge("dice_circuit").textContent).toBe("Dice Circuit");
  expect(bar.element.textContent).toContain("Table 4f27990b");
  const [backButton] = bar.element.querySelectorAll("button");
  backButton.click();
  expect(back).toHaveBeenCalledOnce();
  const rulesButton = bar.element.querySelector<HTMLButtonElement>(".tc-rules")!, resignButton = bar.element.querySelector<HTMLButtonElement>(".tc-resign")!;
  expect(rulesButton.hidden && resignButton.hidden).toBe(true);
  bar.setRules(rules); bar.setSurrender(resign); bar.setConnection("offline");
  rulesButton.click(); resignButton.click();
  expect(rules).toHaveBeenCalledOnce(); expect(resign).toHaveBeenCalledOnce();
  expect(bar.element.querySelector(".conn")!.textContent).toBe("○ reconnecting…");
  bar.setSurrender(null);
  expect(resignButton.hidden).toBe(true);
});

it("confirm and rules dialogs stay inside the table screen and close with Escape", () => {
  const host = document.createElement("section"), yes = vi.fn();
  document.body.append(host);
  confirmDialog(host, "Surrender the game?", "This cannot be undone.", "Yes", yes);
  host.querySelector<HTMLElement>(".modal.confirm")!.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape", bubbles: true }));
  expect(host.querySelector(".modal")).toBeNull();
  expect(yes).not.toHaveBeenCalled();
  rulesDialog(host, "Rentcraft", "# Rules\nRoll the dice.", [["Round limit", "Unlimited"]]);
  expect(host.querySelector(".rules-text")!.textContent).toContain("Roll the dice.");
  expect(host.querySelector("dl")!.textContent).toContain("Round limitUnlimited");
});

it("the replay bar steps, seeks on release, plays at the chosen speed and stops at the end", async () => {
  const shown: number[] = [];
  let bar!: ReturnType<typeof mountReplayBar>;
  const load = vi.fn(async (n: number) => { shown.push(n); bar.shown(n, 4, `events at ${n}`); });
  const close = vi.fn();
  bar = mountReplayBar({ load, close });
  document.body.append(bar.element);
  expect(bar.element.classList.contains("loading")).toBe(true);
  bar.shown(0, 4, "start");
  expect(bar.element.querySelector(".tl-pos")!.textContent).toBe("decision 1 / 4");
  expect(bar.element.querySelector<HTMLButtonElement>('[aria-label="Start (Home)"]')!.disabled).toBe(true);
  bar.element.querySelector<HTMLButtonElement>('[aria-label="Next position (→)"]')!.click();
  await vi.waitFor(() => expect(shown).toEqual([1]));
  const scrub = bar.element.querySelector<HTMLInputElement>('input[type="range"]')!;
  scrub.value = "3"; scrub.dispatchEvent(new Event("input"));
  expect(load).toHaveBeenCalledTimes(1);
  scrub.dispatchEvent(new Event("change"));
  await vi.waitFor(() => expect(shown).toEqual([1, 3]));
  expect(bar.element.querySelector(".tl-events")!.textContent).toBe("events at 3");
  window.dispatchEvent(new KeyboardEvent("keydown", { key: "Home" }));
  await vi.waitFor(() => expect(shown.at(-1)).toBe(0));
  const speed = bar.element.querySelector("select")!;
  speed.value = "16"; speed.dispatchEvent(new Event("change"));
  bar.element.querySelector<HTMLButtonElement>('[aria-label="Play / pause (Space)"]')!.click();
  await vi.waitFor(() => expect(shown.slice(-3)).toEqual([1, 2, 3]), { timeout: 2000 });
  await vi.waitFor(() => expect(bar.element.querySelector('[aria-label="Play / pause (Space)"]')!.textContent).toBe("▶"));
  [...bar.element.querySelectorAll("button")].find(b => b.textContent === "✕ Close replay")!.click();
  expect(close).toHaveBeenCalledOnce();
  bar.dispose();
});

it("Rentcraft log lines are sentences, auctions merge into one line and trades can be filtered", () => {
  const ev = (kind: string, payload: Record<string, unknown>) => eventLine({ kind, payload }, board, names)!;
  expect(ev("buy", { player: 0, space: 1, price: 60 }).text).toBe("Ann buys Mill Street for $60");
  expect(ev("pay", { player: 1, amount: 6, reason: "rent", space: 1, to: 0 }).text).toBe("Bob pays $6 rent for Mill Street to Ann");
  expect(ev("card", { player: 2, deck: "opportunity", card: 3 }).text).toBe("Cid draws Opportunity: Advance to Start.");
  expect(eventLine({ kind: "trade_window", payload: {} }, board, names)).toBeNull();
  const lines = [ev("decline", { player: 0, space: 37 }), ev("bid", { player: 1, space: 37, bid: 100 }), ev("pass_bid", { player: 0, space: 37 }), ev("bid", { player: 2, space: 37, bid: 140 }), ev("auction_won", { player: 2, space: 37, price: 140 }),
    ev("trade", { player: 0, offer: { proposer: 0, target: 1, give_spaces: [1], get_spaces: [], give_cash: 0, get_cash: 90, give_cards: 0, get_cards: 0 } })];
  expect(compactLog(lines).map(l => l.text)).toEqual([
    "Ann sends Park Lane to auction", "🔨 Auction for Park Lane: 2 bids, highest Cid - $140", "Cid wins Park Lane at auction for $140", "Trade: Ann gives Mill Street to Bob for $90",
  ].map(t => t.replace("Park Lane", spaces[37].name)));
  const box = document.createElement("div");
  box.innerHTML = logHtml(lines, "trades");
  expect(box.textContent).toBe("🤝 Trade: Ann gives Mill Street to Bob for $90");
  expect(eventLine(memoryEvent({ kind: "trade_counter", who: 0, to: 1, give: [9], get: [], amount: -240, round: 1 }), board, names)!.text)
    .toBe(`Ann makes a counter-offer: Ann gives ${spaces[9].name} to Bob for $240`);
});

it("Rentcraft panel helpers read the real observation (names, net worth, tradeable sets, phase, hints)", () => {
  expect(seatNames([{ seat: 0, kind: "human", occupied: true, display_name: "Human" }, { seat: 1, kind: "human", occupied: true, display_name: "Human" }] as any, 3)).toEqual(["Human 1", "Human 2", "Player 3"]);
  const state = { players: [0, 1].map(id => ({ id, cash: 100, position: 0, in_jail: false, jail_turns: 0, jail_cards: [], bankrupt: false })),
    owner: spaces.map(s => (s.index === 1 || s.index === 3 ? 0 : s.index === 6 ? 1 : null)), buildings: spaces.map(s => (s.index === 3 ? 1 : 0)), mortgaged: spaces.map(s => s.index === 6),
    debts: [], round: 2, current: 0, phase: "post_roll", acting_seat: 0 } as any;
  expect(netWorth(board, state, 0)).toBe(100 + spaces[1].price + spaces[3].price + spaces[3].house_cost);
  expect(netWorth(board, state, 1)).toBe(100 + Math.floor(spaces[6].price / 2));
  expect(tradeableSpaces(board, state, 0)).toEqual([]);
  expect(tradeableSpaces(board, state, 1)).toEqual([6]);
  const t = { board, state, names, kinds: ["human", "bot"], me: 0 };
  expect(phaseText(t)).toBe("Build, mortgage or trade - then end your turn.");
  expect(phaseText({ ...t, me: 1 })).toBe("Ann manages property before ending the turn.");
  const box = document.createElement("div");
  box.innerHTML = playersHtml(t);
  expect(box.querySelector(".pl.me.act")!.textContent).toContain("Ann");
  expect(box.textContent).toContain("net worth $");
  const hints = tradeHints(t as any, 0, { target: 1, give_spaces: [], get_spaces: [6], give_cash: 50, get_cash: 0, give_cards: 0, get_cards: 0 });
  expect(hints.some(h => h.includes("interest"))).toBe(true);
  expect(hints.at(-1)).toBe(`After the trade you have $${100 - 50 - Math.ceil(Math.floor(spaces[6].price / 2) / 10)}.`);
});

it("Dice replay positions are described in words", () => {
  expect(describeDiceEvents([
    { sequence: 5, kind: "dice_rolled", payload: { opportunity: 1, phase: "active", values: { blue: 3, white: 3 } } },
    { sequence: 6, kind: "action_applied", payload: { player: 2, action: { kind: "select_die", params: { area: "green", die_id: "white", target: 0 } } } },
    { sequence: 7, kind: "window_published", payload: {} },
  ] as any)).toBe("Roll 1: blue 3, white 3 · Player 3 uses the white die on green field 1");
});
