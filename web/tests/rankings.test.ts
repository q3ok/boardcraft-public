import { afterEach, expect, it, vi } from "vitest";
import { mountRankings } from "../src/rankings";

afterEach(() => { document.body.replaceChildren(); });
const games = [{ game_id: "dice_circuit", name: "Dice Circuit", min_players: 1, max_players: 4 }, { game_id: "rentcraft", name: "Rentcraft", min_players: 2, max_players: 4 }];
const row = (extra: any) => ({ identity: "9d2d96721511541df59a88f611e0986730d545e292bb297e1ecbdc47ab11d666", name: "Strategist", kind: "bot", games: 200, ranked: true,
  mean_score: 150.5, ci: 2.1, median: 151, best_score: 190, wins: 0, win_rate: 0, ...extra });
const key = (players: number) => ({ game_id: "dice_circuit", variant: "base", players, rules_version: "1" });

function server() {
  const paths: string[] = [];
  const request = vi.fn(async (path: string) => {
    paths.push(path);
    const q = new URLSearchParams(path.split("?")[1]);
    const players = Number(q.get("players"));
    if (players === 1) return { game_id: "dice_circuit", players: 1, min_ranked_games: 30, computed_at: 1, tables: [{ key: key(1), engine_version: "1", games: 400, reference_name: "Greedy bot", rows: [
      row({ paired: { vs: "x", vs_name: "Greedy bot", delta: 12.3, ci: 1.5, pairs: 200 } }),
      row({ identity: "27467be63d7e", name: "2x32-test-rewards", kind: "model", mean_score: 120, games: 12, ranked: false, paired: null })] }] };
    if (players === 4) return { game_id: "dice_circuit", players: 4, min_ranked_games: 30, computed_at: 1, tables: [{ key: key(4), engine_version: "1", games: 200, reference_name: null, rows: [
      row({ elo: 1612.4, avg_place: 1.4, wins: 120, win_rate: 0.6 }), row({ identity: "fbc0", name: "mokosz-gen0", kind: "model", elo: 1387.6, avg_place: 3.1, wins: 10, win_rate: 0.05 })] }] };
    return { game_id: "dice_circuit", players, min_ranked_games: 30, computed_at: null, tables: [] };
  });
  return { paths, request };
}

it("rankings: the global solo ranking of Dice Circuit with names, kinds, interval, median, paired column and the unranked block", async () => {
  const { paths, request } = server();
  const address = vi.fn();
  const root = document.createElement("div"); document.body.append(root);
  const page = mountRankings(root, { request: request as any, games: async () => games, game: "dice_circuit", players: "1", setAddress: address });
  await vi.waitFor(() => expect(root.querySelector(".rank-table")).not.toBeNull());
  expect(paths).toContain("/api/rankings/global?game_id=dice_circuit&players=1");
  expect(address).toHaveBeenCalledWith("dice_circuit", 1);
  const heads = [...root.querySelectorAll(".rank-table th")].map(t => t.textContent);
  expect(heads).toEqual(["#", "Player", "Kind", "Games", "Mean score ± 95%", "Median", "vs Greedy bot, same dice", "Best score"]);
  const text = root.textContent!;
  expect(text).toContain("Strategist");
  expect(text).toContain("🤖 bot");
  expect(text).toContain("+12.3");
  expect(text).toContain("Too few games (fewer than 30) - not ranked yet");
  expect(text).not.toContain("9d2d96721511");
  expect(root.querySelector(".rank-table b")!.getAttribute("title")).toContain("9d2d96721511");
  expect([...root.querySelectorAll('[aria-label="Table size"] button')].map(b => b.textContent)).toEqual(["Solo", "2 players", "3 players", "4 players"]);
  expect(text).toContain("Solo: there is no rival");
  expect(text).toContain("How it is calculated:");
  // The browser computes nothing: one read per table size, no fit request.
  expect(paths.some(p => p.includes("/fit"))).toBe(false);
  page.dispose();
});

it("rankings: 4 players show the rating columns; an empty size says so; Rentcraft offers its own sizes", async () => {
  const { paths, request } = server();
  const address = vi.fn();
  const root = document.createElement("div"); document.body.append(root);
  const page = mountRankings(root, { request: request as any, games: async () => games, game: "dice_circuit", players: "4", setAddress: address });
  await vi.waitFor(() => expect(root.querySelector(".rank-table")).not.toBeNull());
  expect([...root.querySelectorAll(".rank-table th")].map(t => t.textContent)).toEqual(["#", "Player", "Kind", "Rating", "Games", "Wins", "% wins", "Avg. place", "Mean score"]);
  expect(root.querySelector(".rank-table tbody tr")!.textContent).toContain("1612");
  [...root.querySelectorAll<HTMLButtonElement>('[aria-label="Table size"] button')].find(b => b.textContent === "2 players")!.click();
  await vi.waitFor(() => expect(root.textContent).toContain("No recorded 2-player games yet."));
  expect(address).toHaveBeenLastCalledWith("dice_circuit", 2);
  [...root.querySelectorAll<HTMLButtonElement>('[aria-label="Game"] button')].find(b => b.textContent === "Rentcraft")!.click();
  await vi.waitFor(() => expect(paths).toContain("/api/rankings/global?game_id=rentcraft&players=2"));
  expect([...root.querySelectorAll('[aria-label="Table size"] button')].map(b => b.textContent)).toEqual(["2 players", "3 players", "4 players"]);
  page.dispose();
});
