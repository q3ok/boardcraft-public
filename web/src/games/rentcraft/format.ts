// Shared wording and small helpers of the Rentcraft table screen (HTML-string components escape every server text).
import type { View } from "../../play-types";
import { animals, type Animal } from "./pieces";
import type { RentBoard, RentState } from "./types";

export const esc = (s: unknown) => String(s ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]!);

/** Money as the English UI shows it: $1,500. */
export const money = (n: number): string => `${n < 0 ? "−" : ""}$${Math.abs(Math.round(n)).toLocaleString("en-US")}`;

/** CSS class of a board space's header colour (`.grp-<group>`; stations, utilities and specials have their own). */
export const groupClass = (group: string | null | undefined, fallback = "special"): string => `grp-${group ?? fallback}`;
/** CSS class of seat N's player colour (`.pc-N` sets --pc); `pcbg` paints a background with it. */
export const seatClass = (seat: number): string => `pc-${seat % 4}`;

export const ANIMAL_EMOJI: Record<Animal, string> = {
  elephant: "🐘", giraffe: "🦒", lion: "🦁", rhinoceros: "🦏", zebra: "🦓", hippopotamus: "🦛", crocodile: "🐊", gorilla: "🦍",
};
export const seatAnimal = (seat: number): Animal => animals[seat % animals.length];

export const GROUP_NAME: Record<string, string> = {
  brown: "brown", light_blue: "light blue", pink: "pink", orange: "orange",
  red: "red", yellow: "yellow", green: "green", dark_blue: "dark blue",
};

const KIND_LABEL: Record<string, string> = { human: "human", agent: "agent" };
type Seats = NonNullable<View["seats"]>;

/** Seat names from the table roster; equal names get the seat number so that every player stays distinguishable. */
export function seatNames(seats: Seats | undefined, players: number): string[] {
  const names = Array.from({ length: players }, (_, i) => seats?.find(s => s.seat === i)?.display_name?.trim() || `Player ${i + 1}`);
  return names.map((n, i) => (names.filter(x => x === n).length > 1 ? `${n} ${i + 1}` : n));
}
/** "human", "agent", "RL model" or "bot", for the small tag next to a name. */
export function seatKind(seats: Seats | undefined, seat: number): string {
  const s = seats?.find(x => x.seat === seat);
  if (!s) return "";
  if (KIND_LABEL[s.kind]) return KIND_LABEL[s.kind];
  const ref = (s as { bot_ref?: { kind?: string } | null }).bot_ref;
  return ref?.kind && ref.kind !== "builtin" ? "RL model" : "bot";
}

/** Net worth as the engine counts it at the end: cash, board price (half when mortgaged) and buildings at cost. */
export function netWorth(board: RentBoard, state: RentState, player: number): number {
  let total = state.players[player]?.cash ?? 0;
  state.owner?.forEach((o, i) => {
    if (o !== player) return;
    const s = board.spaces[i];
    total += state.mortgaged?.[i] ? Math.floor(s.price / 2) : s.price;
    total += (state.buildings?.[i] ?? 0) * s.house_cost;
  });
  return total;
}

/** Spaces of one colour set. */
export const groupMembers = (board: RentBoard, group: string) => board.spaces.filter(s => s.group === group).map(s => s.index);

/** The player's spaces that may be given away (no buildings anywhere in the colour set). */
export function tradeableSpaces(board: RentBoard, state: RentState, pid: number): number[] {
  return board.spaces
    .filter(s => state.owner[s.index] === pid)
    .filter(s => s.kind !== "property" || !s.group || groupMembers(board, s.group).every(i => !state.buildings[i]))
    .map(s => s.index);
}

/** Interest and immediate payoff of a mortgaged space, as the engine charges them (10% of the mortgage, rounded up). */
export function mortgageCost(board: RentBoard, space: number) {
  const principal = board.spaces[space].mortgage_value ?? Math.floor(board.spaces[space].price / 2);
  const interest = Math.ceil(principal / 10);
  return { principal, interest, payoff: principal + interest };
}

/** Seconds as "0:42" or "1:05". */
export function formatLeft(seconds: number): string {
  const s = Math.max(0, Math.ceil(seconds));
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
}
