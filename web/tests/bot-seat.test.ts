// A bot seat in the new-table form is only the choice of the bot, as in the shared design: no "Bot settings" block.
import { afterEach, expect, it, vi } from "vitest";
import { mountRoom, SessionStore } from "../src/lobby";

const disposers: (() => void)[] = [];
afterEach(() => { disposers.splice(0).forEach(d => d()); document.body.replaceChildren(); vi.unstubAllGlobals(); });
const games = [{ game_id: "rentcraft", name: "Rentcraft", variants: ["base"], min_players: 2, max_players: 4 }];
const bots = [{ choice_id: "miser", display_name: "Miser", kind: "builtin", reference: {} }];

it("a bot seat has no Bot settings block and the table is created with the bot's own settings", async () => {
  const bodies: { path: string; body: any }[] = [];
  const root = document.createElement("div"); document.body.append(root);
  const request = vi.fn(async (path: string, body?: any) => {
    if (body !== undefined) { bodies.push({ path, body }); return { table_id: "a".repeat(32), creator_credential: "c", seats: [] }; }
    if (path === "/api/games") return games;
    if (path.endsWith("/bots")) return bots;
    if (path.endsWith("/settings")) return { table: {}, live: {}, bots: { miser: { properties: { caution: { type: "number", default: 0.5, title: "Caution" } } } } };
    return { total: 0, offset: 0, limit: 12, items: [] };
  });
  const room = mountRoom(root, { origin: location.origin, store: new SessionStore(location.origin), request: request as any, alive: () => true,
    open: vi.fn(), go: vi.fn(), fail: vi.fn(), clearError: vi.fn(), preselect: { gameId: "rentcraft", kind: "bot", choiceId: "miser", seat: 1 } });
  disposers.push(() => room.dispose());
  await vi.waitFor(() => expect(root.querySelector<HTMLSelectElement>('[aria-label="Player 2 type"]')?.value).toBe("miser"));
  const card = root.querySelector<HTMLSelectElement>('[aria-label="Player 2 type"]')!.closest(".bc-seat-card")!;
  expect(card.textContent).not.toContain("Bot settings");
  expect(card.textContent).not.toContain("Caution");
  expect(root.textContent).not.toContain("Bot settings");
});
