// Game log: English sentences from the structured events and the observation's log, merged auctions, highlighted trades
// and the "trades only" filter.
import type { GameEvent } from "../../play-types";
import { esc, money } from "./format";
import type { Offer, RentBoard } from "./types";

export type LogFilter = "all" | "trades";
export interface LogLine { text: string; trade?: boolean; bid?: { space: number; who: number; amount: number }; pass?: boolean }

const DECK: Record<string, string> = { opportunity: "Opportunity", community_fund: "Community Fund" };
const WINDOW: Record<string, string> = {
  payment: "a payment", acquisition: "after a purchase", after_trade: "after a trade", turn_end: "end of the turn", request: "requested by a player",
};
export const windowLabel = (kind: string | undefined) => WINDOW[kind ?? ""] ?? (kind ?? "").replaceAll("_", " ");
/** Events that only frame other events; they are not written to the log. */
const SILENT = new Set(["trade_window", "trade_window_end", "start_player", "game_over"]);

export function offerTerms(offer: Offer, board: RentBoard, names: string[]): string {
  const side = (spaces: number[] = [], cash = 0, cards = 0) =>
    [...spaces.map(i => board.spaces[i]?.name ?? `space ${i}`), ...(cash ? [money(cash)] : []), ...(cards ? [`${cards} jail card${cards > 1 ? "s" : ""}`] : [])].join(", ") || "nothing";
  return `${names[offer.proposer] ?? "?"} gives ${side(offer.give_spaces, offer.give_cash, offer.give_cards)} to ${names[offer.target] ?? "?"} for ${side(offer.get_spaces, offer.get_cash, offer.get_cards)}`;
}

/** One event as a log line; null for events the log leaves out. */
export function eventLine(event: Pick<GameEvent, "kind" | "payload">, board: RentBoard, names: string[]): LogLine | null {
  const p = event.payload ?? {};
  const who = p.player == null ? "" : names[p.player] ?? `Player ${p.player + 1}`;
  const space = (i: unknown) => (typeof i === "number" ? board.spaces[i]?.name ?? `space ${i}` : "");
  const to = (i: unknown) => (typeof i === "number" ? names[i] ?? `Player ${i + 1}` : "the bank");
  const dice = Array.isArray(p.dice) ? p.dice.join(" + ") : "";
  switch (event.kind) {
    case "start_roll": return { text: `${who} rolls ${dice} for the starting order` };
    case "roll": return { text: `${who} rolls ${dice}${p.for_card ? " for the card" : ""}` };
    case "move": return { text: `${who} moves to ${space(p.to)}` };
    case "pass_go": return { text: `${who} passes Start and collects ${money(p.amount ?? 200)}` };
    case "jail": return { text: `${who} goes to jail` };
    case "jail_release": return { text: `${who} leaves jail` };
    case "card": {
      const text = board.cards?.[p.deck]?.find(c => c.id === p.card)?.text ?? p.text ?? "";
      return { text: `${who} draws ${DECK[p.deck] ?? "a card"}: ${text}` };
    }
    case "buy": return { text: `${who} buys ${space(p.space)} for ${money(p.price ?? p.amount ?? 0)}` };
    case "decline": return { text: `${who} sends ${space(p.space)} to auction` };
    case "bid": return { text: `${who} bids ${money(p.bid ?? p.amount ?? 0)} on ${space(p.space)}`, bid: { space: p.space, who: p.player, amount: p.bid ?? p.amount ?? 0 } };
    case "pass_bid": return { text: `${who} passes in the auction`, pass: true };
    case "auction_won": return { text: `${who} wins ${space(p.space)} at auction for ${money(p.price ?? p.amount ?? 0)}` };
    case "auction_unsold": return { text: `Nobody buys ${space(p.space)} at auction` };
    case "pay": {
      const why = p.reason === "rent" ? ` rent for ${space(p.space)}` : p.reason === "tax" ? " tax" : p.reason === "interest" ? ` interest on ${space(p.space)}` : p.reason === "jail_fine" ? " the jail fine" : "";
      return { text: `${who} pays ${money(p.amount ?? 0)}${why} to ${to(p.to)}` };
    }
    case "debt": return { text: `${who} owes ${money(p.amount ?? 0)} to ${to(p.to)}` };
    case "build": return { text: `${who} builds ${p.level === 5 ? "a hotel" : "a house"} on ${space(p.space)}${p.cost ?? p.amount ? ` for ${money(p.cost ?? p.amount)}` : ""}` };
    case "sell_building": return { text: `${who} sells ${p.space == null ? "buildings" : `a building on ${space(p.space)}`}${p.refund ?? p.amount ? ` for ${money(p.refund ?? p.amount)}` : ""}` };
    case "mortgage": return { text: `${who} mortgages ${space(p.space)} for ${money(p.amount ?? 0)}` };
    case "unmortgage": return { text: `${who} repays the mortgage on ${space(p.space)} (${money(p.amount ?? 0)})` };
    case "trade_offer": return p.offer ? { text: `${names[p.offer.proposer] ?? who} proposes a trade: ${offerTerms(p.offer, board, names)}`, trade: true } : null;
    case "trade_counter": return p.offer ? { text: `${names[p.offer.proposer] ?? who} makes a counter-offer: ${offerTerms(p.offer, board, names)}`, trade: true } : null;
    case "trade": return p.offer ? { text: `Trade: ${offerTerms(p.offer, board, names)}`, trade: true } : null;
    case "trade_reject": return { text: `${who} rejects the trade offer`, trade: true };
    case "trade_request": return { text: `${who} asks for a trade`, trade: true };
    case "resign": return { text: `${who} surrenders` };
    case "bankrupt": return { text: `${who} goes bankrupt${p.to != null ? ` to ${to(p.to)}` : ""}` };
    default: return SILENT.has(event.kind) ? null : { text: `${who ? who + ": " : ""}${event.kind.replaceAll("_", " ")}` };
  }
}

/** An entry of the observation's own log (kept by the game for every viewer) as an event, so it reads the same way. */
export interface MemoryEntry {
  kind: string; who: number | null; to?: number | null; space?: number | null; card?: [string, number] | null;
  give?: number[]; get?: number[]; amount?: number; round: number;
}
export function memoryEvent(e: MemoryEntry): Pick<GameEvent, "kind" | "payload"> {
  const amount = e.amount ?? 0;
  const payload: Record<string, unknown> = { player: e.who, space: e.space ?? undefined, to: e.to ?? undefined, amount, price: amount, cost: amount, refund: amount };
  if (e.card) Object.assign(payload, { deck: e.card[0], card: e.card[1] });
  if (e.kind.startsWith("trade") && e.who != null && e.to != null)
    payload.offer = { proposer: e.who, target: e.to, give_spaces: e.give ?? [], get_spaces: e.get ?? [], give_cash: Math.max(amount, 0), get_cash: Math.max(-amount, 0), give_cards: 0, get_cards: 0 };
  return { kind: e.kind, payload };
}

/** Replaces a run of bids and passes in one auction with a single line showing the highest bid. */
export function compactLog(lines: LogLine[]): LogLine[] {
  const out: LogLine[] = [];
  type Run = { space: number; who: string; amount: number; n: number; name: string };
  let run = null as Run | null;
  const flush = () => {
    if (run) out.push({ text: `🔨 Auction for ${run.name}: ${run.n === 1 ? "1 bid" : `${run.n} bids`}, highest ${run.who} - ${money(run.amount)}` });
    run = null;
  };
  for (const l of lines) {
    if (l.bid) {
      if (run && run.space !== l.bid.space) flush();
      const m = /^(.+) bids \$[\d,]+ on (.+)$/.exec(l.text);
      const before = (run as Run | null)?.n ?? 0;
      run = { space: l.bid.space, who: m?.[1] ?? "", name: m?.[2] ?? "", amount: l.bid.amount, n: before + 1 };
      continue;
    }
    if (run && l.pass) continue;
    flush();
    out.push(l);
  }
  flush();
  return out;
}

/** Log HTML: newest entries on top, trades highlighted. */
export function logHtml(lines: LogLine[], filter: LogFilter): string {
  let items = compactLog(lines);
  if (filter === "trades") items = items.filter(l => l.trade);
  if (!items.length) return `<div class="empty">${filter === "trades" ? "No trades yet." : "No events yet."}</div>`;
  return items.slice().reverse().map(l => (l.trade ? `<div class="trade">🤝 ${esc(l.text)}</div>` : `<div>${esc(l.text)}</div>`)).join("");
}

/** Log panel with the filter switch. */
export function logPanelHtml(): string {
  return `<div class="panel log"><div class="log-head"><h4>Log</h4>
    <div class="seg" role="group" aria-label="Log filter"><button type="button" class="on" data-log="all" aria-pressed="true">All</button><button type="button" data-log="trades" aria-pressed="false">🤝 Trades</button></div></div>
    <div class="log-lines" aria-live="off"></div></div>`;
}
