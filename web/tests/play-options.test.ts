// The compact options row, the Play page's address, model versions in a seat,
// and the Rentcraft debt and autopilot lines built from server facts.
import { afterEach, expect, it, vi } from "vitest";
import { settingsFields } from "../src/settings";
import { mountRoom, SessionStore, type RoomHost } from "../src/lobby";
import { autopilotHtml, debtInfoHtml, type Table } from "../src/games/rentcraft/panel";
import type { DebtSummary } from "../src/play-types";

const disposers: (() => void)[] = [];
afterEach(() => { disposers.splice(0).forEach(d => d()); document.body.replaceChildren(); vi.unstubAllGlobals(); });

it("the compact row offers presets as a select, keeps the schema order and has no Default/Range notes", () => {
  const root = document.createElement("div");
  const fields = settingsFields(root, "Options", { properties: {
    rounds: { title: "Round limit", type: ["integer", "null"], minimum: 1, default: null, order: 5,
      presets: [{ value: null, label: "no limit" }, { value: 100, label: "100" }, { value: 200, label: "200" }] },
    cash: { title: "Starting cash", type: "integer", minimum: 0, default: 1500, order: 1, description: "Money at the start." },
  } }, {}, {}, undefined, true);
  expect(root.querySelector("details, summary")).toBeNull();
  const rounds = root.querySelector<HTMLSelectElement>('select[aria-label="Round limit"]')!;
  expect([...rounds.options].map(o => o.textContent)).toEqual(["no limit", "100", "200"]);
  expect(rounds.closest<HTMLElement>(".bc-setting")!.style.order).toBe("5");
  const cash = root.querySelector<HTMLInputElement>('input[aria-label="Starting cash"]')!;
  expect(cash.closest<HTMLElement>(".bc-setting")!.style.order).toBe("1");
  expect(cash.closest<HTMLElement>(".bc-setting")!.title).toBe("Money at the start.");
  expect(root.textContent).not.toMatch(/Default|Range|Minimum|Money at the start/);
  // Untouched suggested values are not sent; a chosen one is.
  expect(fields.read()).toEqual({});
  rounds.selectedIndex = 1;
  rounds.dispatchEvent(new Event("change"));
  expect(fields.read()).toEqual({ rounds: 100 });
});

it("a bound just above zero reads as 'More than 0' in the full form, and no long dashes are used", () => {
  const root = document.createElement("div");
  settingsFields(root, "Live", { properties: {
    limit: { title: "Decision time limit", type: ["number", "null"], minimum: 5e-324, default: 60 },
    wait: { title: "Reaction time (s)", type: "number", minimum: 0, maximum: 60, default: 3 },
  } });
  expect(root.textContent).toContain("More than 0");
  expect(root.textContent).not.toContain("5e-324");
  expect(root.textContent).toContain("Range: 0-60");
  expect(root.textContent).not.toMatch(/[–—]/);
});

const games = [
  { game_id: "dice_circuit", name: "Dice Circuit", variants: ["base"], min_players: 1, max_players: 4 },
  { game_id: "rentcraft", name: "Rentcraft", variants: ["base"], min_players: 2, max_players: 4 },
];
function room(extra: Partial<RoomHost>, descriptors: unknown[] = []) {
  const paths: string[] = [];
  const root = document.createElement("div");
  document.body.append(root);
  const request = vi.fn(async (path: string) => {
    paths.push(path);
    if (path === "/api/games") return games;
    if (path.endsWith("/bots")) return descriptors;
    if (path.endsWith("/settings")) return { table: {}, live: {}, bots: {} };
    const offset = Number(new URL(path, "http://x").searchParams.get("offset"));
    return { total: 30, offset, limit: 12, items: [{ table_id: "f".repeat(32), game_id: "rentcraft", status: "lobby", players: 2, seats: [] }] };
  });
  const r = mountRoom(root, { origin: location.origin, store: new SessionStore(location.origin), request: request as any, alive: () => true,
    open: vi.fn(), go: vi.fn(), fail: vi.fn(), clearError: vi.fn(), ...extra });
  disposers.push(() => r.dispose());
  return { root, paths };
}

it("the Play page reads its filters, page and open form from the address and writes changes back", async () => {
  const setAddress = vi.fn();
  const { root, paths } = room({ params: { game: "rentcraft", status: "lobby", page: "2", new: "dice_circuit" }, setAddress });
  await vi.waitFor(() => expect(paths.some(p => p.startsWith("/api/tables?"))).toBe(true));
  const query = new URL(paths.find(p => p.startsWith("/api/tables?"))!, "http://x").searchParams;
  expect(Object.fromEntries(query)).toEqual({ game_id: "rentcraft", status: "lobby", offset: "12", limit: "12" });
  expect(root.querySelector('.bc-filter-games [aria-pressed="true"]')!.textContent).toBe("Rentcraft");
  // The form named in the address is open.
  await vi.waitFor(() => expect(root.querySelector<HTMLSelectElement>('[aria-label="Game"]')!.value).toBe("dice_circuit"));
  expect(setAddress).toHaveBeenLastCalledWith({ page: "2", game: "rentcraft", status: "lobby", new: "dice_circuit" });
  const status = root.querySelector<HTMLSelectElement>('[aria-label="Filter by status"]')!;
  status.value = "completed";
  status.dispatchEvent(new Event("change"));
  // A new filter starts again at page 1, so the page leaves the address.
  expect(setAddress).toHaveBeenLastCalledWith({ game: "rentcraft", status: "completed", new: "dice_circuit" });
});

it("a model with saved versions offers a Version select, and a running training says it is still training", async () => {
  const model = { choice_id: "run-final", display_name: "mokosz", kind: "model", reference: {}, description: "Own training.", training: true,
    versions: [{ choice_id: "run-ckpt-8", label: "checkpoint 8" }, { choice_id: "run-ckpt-16", label: "checkpoint 16" }] };
  const { root } = room({ preselect: { gameId: "dice_circuit", kind: "model", choiceId: "run-final", seat: 1 } }, [model]);
  await vi.waitFor(() => expect(root.querySelector<HTMLSelectElement>('[aria-label="Player 2 type"]')?.value).toBe("run-final"));
  const version = root.querySelector<HTMLSelectElement>('[aria-label="Player 2 model version"]')!;
  expect(version.closest("label")!.hidden).toBe(false);
  expect([...version.options].map(o => o.textContent)).toEqual(["latest save", "checkpoint 8", "checkpoint 16"]);
  const card = version.closest(".bc-seat-card")!;
  expect(card.textContent).toContain("The model is still training - it plays the version from the chosen save.");
  version.value = "run-ckpt-16";
  version.dispatchEvent(new Event("change"));
  expect(root.querySelector<HTMLSelectElement>('[aria-label="Player 2 type"]')!.value).toBe("run-ckpt-16");
  // The model picker keeps showing the model itself, and its versions stay offered.
  expect(root.querySelector<HTMLSelectElement>('[aria-label="Player 2 bot or model"]')!.value).toBe("run-final");
  expect(root.querySelector<HTMLSelectElement>('[aria-label="Player 2 model version"]')!.value).toBe("run-ckpt-16");
});

const table = (cash: number): Table => ({
  board: { spaces: [] } as any, names: ["Ala", "Bob"], kinds: ["human", "bot"], me: 0,
  state: { phase: "raise_funds", debts: [{ debtor: 0, creditor: 1, amount: 300 }], players: [{ cash }, { cash: 500 }] } as any,
});
const summary = (extra: Partial<DebtSummary>): DebtSummary => ({ amount: 300, cash: 50, mortgage_now: 100, building_sales: 150, mortgage_after_sales: 60,
  max_cash_from_bank: 360, bank_enough: true, needed_from_trades: 0, ...extra });

it("the debt line states what the server says the bank can raise", () => {
  const enough = debtInfoHtml(table(50), true, summary({}));
  expect(enough).toContain("The bank is enough: mortgages right now $100, building sales $150, mortgages after the sales $60 - up to $360 in all (debt $300).");
  expect(enough).toContain("Full bank liquidation");
  const short = debtInfoHtml(table(50), false, summary({ bank_enough: false, max_cash_from_bank: 200, needed_from_trades: 100 }));
  expect(short).toContain("The bank is not enough. Full liquidation and mortgages give at most $200. You still need $100 from sales to players.");
  expect(short).not.toContain("Full bank liquidation");
  // Without the server's summary the line stays the plain one.
  expect(debtInfoHtml(table(50), false)).toContain("You owe $300 to Bob and have $50.");
});

it("the autopilot line names the round it took over in", () => {
  expect(autopilotHtml(true, true, 7)).toContain("You ran out of time in round 7 - autopilot has been playing for you since.");
  expect(autopilotHtml(true, false)).toContain("You ran out of time - autopilot is playing for you.");
  expect(autopilotHtml(false, true, 7)).toBe("");
});
