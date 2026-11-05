import { afterEach, expect, it, vi } from "vitest";
import { mountReplays } from "../src/replays";
import { mountTournamentsPage } from "../src/tournaments-page";

afterEach(() => { document.body.replaceChildren(); vi.unstubAllGlobals(); vi.useRealTimers(); });
const games = [{ game_id: "dice_circuit", name: "Dice Circuit", min_players: 1, max_players: 4, variants: ["base"] }];

const tour = (n: number, extra: any = {}) => ({ source: "tournament", tournament_id: "t".padEnd(32, "0"), tournament_name: "night run", match_id: `m${n}`, ordinal: n,
  game_id: "dice_circuit", players: 2, played_at: 1793714036, replay_path: `/api/tournaments/x/matches/m${n}/replay`,
  participants: [{ seat: 0, kind: "model", display_name: "mokosz-gen1", identity: "a" }, { seat: 1, kind: "bot", display_name: "Greedy bot", identity: "b" }],
  result: { scores: [167, 120], placements: [1, 2] }, ...extra });

function replayServer(page: { items: any[]; total: number; can_manage: boolean }) {
  const calls: { path: string; body?: unknown }[] = [];
  const request = vi.fn(async (path: string) => { calls.push({ path }); return { ...page, offset: 0, limit: 20 }; });
  const send = vi.fn(async (path: string, body: unknown) => { calls.push({ path, body }); return { deleted: 1 }; });
  return { calls, request, send };
}

function mount(server: ReturnType<typeof replayServer>) {
  const root = document.createElement("div"); document.body.append(root);
  const page = mountReplays(root, { request: server.request as any, send: server.send as any, games: async () => games, game: "",
    openTable: () => {}, openTournament: () => {}, setGame: () => {} });
  return { root, page };
}

it("replays: the server pages, orders and filters; the rows show the players' names", async () => {
  const server = replayServer({ items: [tour(3), tour(2)], total: 41, can_manage: false });
  const { root, page } = mount(server);
  await vi.waitFor(() => expect(root.querySelectorAll(".table-row.rec")).toHaveLength(2));
  expect(server.calls[0].path).toBe("/api/replays?offset=0&limit=20");
  const chips = [...root.querySelectorAll(".table-row.rec")][0].querySelectorAll(".chip");
  expect([...chips].map(c => c.textContent)).toEqual(["🏆 mokosz-gen1 · 167", "Greedy bot · 120"]);
  expect(root.textContent).not.toContain("model ·");
  // No selection or deletion where the server does not allow managing.
  expect(root.querySelector("[data-sel]")).toBeNull();
  expect([...root.querySelectorAll("button")].some(b => !b.hidden && b.textContent?.includes("Delete"))).toBe(false);
  expect(root.querySelector(".bc-pager")!.textContent).toContain("page 1 of 3");
  const q = root.querySelector<HTMLInputElement>('[aria-label="Player"]')!;
  q.value = "moko"; q.dispatchEvent(new Event("input"));
  root.querySelector<HTMLInputElement>('.fcheck input')!.click();
  const t = root.querySelector<HTMLInputElement>('[aria-label="Tournament"]')!;
  t.value = "night"; t.dispatchEvent(new Event("input"));
  await vi.waitFor(() => expect(server.calls.some(c => c.path === "/api/replays?offset=0&limit=20&q=moko&won=1&tournament=night")).toBe(true));
  [...root.querySelectorAll("button")].find(b => b.textContent === "Older →")!.click();
  await vi.waitFor(() => expect(server.calls.at(-1)!.path).toBe("/api/replays?offset=20&limit=20&q=moko&won=1&tournament=night"));
  page.dispose();
});

it("replays: on a private server replays are selected and deleted by the server", async () => {
  const server = replayServer({ items: [tour(3), { source: "table", table_id: "c".repeat(32), game_id: "dice_circuit", players: 1, played_at: 1793714036,
    participants: [{ seat: 0, kind: "human", display_name: "Alice" }], result: { scores: [130], placements: [1] }, replay_path: "/x" }], total: 2, can_manage: true });
  vi.stubGlobal("confirm", vi.fn(() => true));
  const { root, page } = mount(server);
  await vi.waitFor(() => expect(root.querySelectorAll("[data-sel]")).toHaveLength(2));
  const all = [...root.querySelectorAll<HTMLButtonElement>("button")];
  const bulk = all.find(b => b.textContent?.startsWith("🗑 Delete selected"))!;
  expect(bulk.disabled).toBe(true);
  root.querySelectorAll<HTMLInputElement>("[data-sel]").forEach(c => c.click());
  expect(bulk.textContent).toBe("🗑 Delete selected (2)");
  expect(bulk.disabled).toBe(false);
  bulk.click();
  await vi.waitFor(() => expect(server.send).toHaveBeenCalledTimes(1));
  expect(server.send).toHaveBeenCalledWith("/api/replays/delete", { replays: [
    { source: "tournament", tournament_id: "t".padEnd(32, "0"), match_id: "m3" }, { source: "table", table_id: "c".repeat(32) }] });
  await vi.waitFor(() => expect(server.calls.filter(c => c.path.startsWith("/api/replays?"))).toHaveLength(2));
  // One row: its own 🗑 button.
  root.querySelector<HTMLButtonElement>('button[title="Delete the replay"]')!.click();
  await vi.waitFor(() => expect(server.send).toHaveBeenCalledTimes(2));
  expect(server.send.mock.calls[1][1]).toEqual({ replays: [{ source: "tournament", tournament_id: "t".padEnd(32, "0"), match_id: "m3" }] });
  page.dispose();
});

it("tournaments: the list keeps the server's newest-first order, knockouts placed by their creation time", async () => {
  const t = (id: string, name: string, created: number) => ({ tournament_id: id.padEnd(32, "0"), spec: { name, config: { game_id: "dice_circuit", players: 2 }, match_count: 4, rotations: [0, 1], seats: ["greedy", "random"] },
    status: "completed", revision: 1, spec_sha: "s", created, done: 4, results: [] });
  const knockout = { knockout_id: "k".padEnd(32, "0"), spec: { name: "middle knockout", config: { game_id: "dice_circuit", players: 2 } }, status: "completed", revision: 1, error: null,
    created: 200, rounds: [], plan: [{}], labels: { candidates: [] }, winner: null, games_done: 0, games_total: 0 };
  const request = vi.fn(async (path: string) => {
    if (path === "/api/tournaments") return [t("c", "newest", 300), t("b", "Knockout: middle knockout · round 1", 250), t("a", "oldest", 100)];
    if (path === "/api/knockouts") return [knockout];
    if (path.includes("/bots")) return [];
    return {};
  });
  const root = document.createElement("div"); document.body.append(root);
  const page = mountTournamentsPage(root, { request: request as any, games: async () => games, game: "", selected: null, setAddress: () => {} });
  await vi.waitFor(() => expect(root.querySelectorAll(".bc-tlist .exp")).toHaveLength(3));
  expect([...root.querySelectorAll(".bc-tlist .exp b")].map(b => b.textContent?.trim())).toEqual(["newest", "middle knockout", "oldest"]);
  page.dispose();
});
