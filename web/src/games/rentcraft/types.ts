export interface Space {
  index: number;
  name: string;
  kind: string;
  group: string | null;
  price: number;
  rents: number[];
  house_cost: number;
  mortgage_value: number;
  tax: number;
}
export interface RentBoard {
  spaces: Space[];
  cards: Record<string, {
    id: number;
    text: string;
    effect: string;
  }[]>;
  start_salary: number;
  station_rents: number[];
  utility_multipliers: number[];
}
export interface Player {
  id: number;
  cash: number;
  position: number;
  in_jail: boolean;
  jail_turns: number;
  jail_cards: string[];
  bankrupt: boolean;
}
export interface Offer {
  proposer: number;
  target: number;
  give_spaces: number[];
  get_spaces: number[];
  give_cash: number;
  get_cash: number;
  give_cards: number;
  get_cards: number;
}
export interface RentState {
  players: Player[];
  owner: (number | null)[];
  buildings: number[];
  mortgaged: boolean[];
  roll_count: number;
  last_roll: number[] | null;
  start_rolls?: [
    number,
    number[],
    number
  ][];
  pending_buy?: number | null;
  round: number;
  current: number;
  phase: string;
  acting_seat: number | null;
  bank_houses: number;
  bank_hotels: number;
  pending_takeovers: [
    number,
    number
  ][];
  auction?: {
    space: number;
    high_bid: number;
    high_bidder: number | null;
  } | null;
  debts: {
    debtor: number;
    creditor: number | null;
    amount: number;
  }[];
  trade?: {
    offer: Offer;
    history: Offer[];
    exchanges: number;
  } | null;
  trades_this_turn?: number;
  /** Seats whose trade request waits for the nearest safe point. */
  trade_requests?: number[];
  /** The table's rule overrides (defaults come from the settings schema). */
  settings?: Record<string, unknown>;
  window?: {
    kind: string;
    serial: number;
    space: number | null;
    /** [debtor, creditor or null for the bank, amount, details] */
    payments: unknown[];
    queue?: number[];
    requested?: number[];
  } | null;
  memory?: {
    log: {
      kind: string;
      who: number | null;
      space?: number | null;
      card?: [
        string,
        number
      ] | null;
      amount?: number;
      round: number;
    }[];
  };
  result?: {
    scores: number[];
    placements: number[];
    reason: string;
    details?: { winner: number; rounds: number; by_round_limit: boolean };
  };
}
