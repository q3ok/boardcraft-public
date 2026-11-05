export interface Action {
  id: string;
  kind: string;
  label: string;
  params: Record<string, unknown>;
}
export interface GameEvent {
  sequence: number;
  kind: string;
  payload: Record<string, any>;
}
export interface GameCommand {
  kind: string;
  label: string;
  scope: "seat" | "decision";
  params_schema: SettingSchema;
}
export interface CommandInput {
  expectedRevision: number;
  decisionId?: string;
  kind: string;
  params: Record<string, unknown>;
}
export interface ActionList {
  decision_id: string | null;
  actions: Action[];
  commands?: GameCommand[];
  expected_revision?: number;
  /** Server facts for the viewer's decision (Rentcraft: what the bank can raise for a debt). */
  context?: { debt?: DebtSummary };
}
/** The bank financing of the viewer's debt, computed by the server. */
export interface DebtSummary {
  amount: number;
  cash: number;
  mortgage_now: number;
  building_sales: number;
  mortgage_after_sales: number;
  max_cash_from_bank: number;
  bank_enough: boolean;
  needed_from_trades: number;
}
export interface Observation<T = Record<string, unknown>> {
  viewer: number | null;
  game_id: string;
  revision: number;
  decision: {
    id: string;
    player: number;
    kind: string;
  } | null;
  data: T & {
    event_cursor: number;
  };
}
export interface View<T = Record<string, unknown>> {
  table_id: string;
  status: string;
  config: {
    game_id: string;
    variant: string;
    settings?:Record<string,unknown>;
    players: number;
  };
  live?: Record<string,unknown>;
  event_cursor?: number;
  revision?: number;
  observation: Observation<T> | null;
  seats?: {
    seat: number;
    kind: string;
    occupied: boolean;
    display_name?: string | null;
    availability?: {
      status: string;
    } | null;
  }[];
  control?: {
    autopilot: boolean;
    /** The round the autopilot took over in. */
    autopilot_round?: number;
    can_reclaim: boolean;
    commands: string[];
    deadline: null | {
      at: number;
      effect: string;
      kind: "window" | "decision";
    };
  };
  /** The server lets any client manage this table (a private server with open management). */
  can_manage?: boolean;
}
export interface BotDescriptor {
  choice_id: string;
  display_name: string;
  description?: string;
  kind: "builtin" | "model";
  reference: unknown;
  /** UI-only heading of the picker group (host catalogs, for example one per training run). Never posted. */
  group?: string;
  /** Other playable versions of this model (final, checkpoints, latest save); each is its own choice. */
  versions?: { choice_id: string; label: string }[];
  /** The model's training is still running: it plays the version from the chosen save. */
  training?: boolean;
}
export interface SettingSchema {
  type?: string | string[];
  title?: string;
  description?: string;
  properties?: Record<string, SettingSchema>;
  default?: unknown;
  enum?: unknown[];
  items?: SettingSchema;
  minimum?: number;
  maximum?: number;
  minItems?: number;
  /** UI-only English labels keyed by String(value) for `enum`. Never a source of values or defaults. */
  enumLabels?: Record<string, string>;
  /** UI-only: the default depends on the table roster, so a blank field means "Auto". Never posted. */
  automatic?: boolean;
  /** UI-only: a few suggested values with labels; a compact form offers them as a select. */
  presets?: { value: unknown; label: string }[];
  /** UI-only: the place of the field in a compact form row (lower first). */
  order?: number;
}
/** One game as listed by `GET /api/games`; `description` is optional module-owned English text. */
export interface GameInfo {
  game_id: string;
  name: string;
  description?: string;
  variants: string[];
  min_players: number;
  max_players: number;
  bot_kinds?: string[];
  capabilities?: { seat_operations: boolean };
}
/** The safe public summary of a listed table (`GET /api/tables`). */
export interface TableSummary {
  table_id: string;
  game_id: string;
  variant: string;
  players: number;
  status: "lobby" | "playing" | "completed" | string;
  seats: { seat: number; kind: string; occupied: boolean; display_name: string | null; token?: string }[];
  /** The seat that won a completed table. */
  winner?: number | null;
  /** The table's number on this server (the directory order; never reused). */
  number?: number;
  /** The round in play and the round count (null without a limit). */
  round?: number;
  rounds?: number | null;
  /** The server lets any client manage this table (a private server with open management). */
  can_manage?: boolean;
}
export interface TableList {
  items: TableSummary[];
  total: number;
  offset: number;
  limit: number;
}
export interface Settings {
  table: SettingSchema;
  live: SettingSchema;
  bots: Record<string, SettingSchema>;
  simulation_defaults: Record<string, unknown>;
}
export class ResponseContext {
  generation = 0;
  revision = -1;
  reset() {
    this.generation++;
    this.revision = -1;
  }
  accept(generation: number, revision: number) {
    if (generation !== this.generation || revision < this.revision)
      return false;
    this.revision = revision;
    return true;
  }
}
