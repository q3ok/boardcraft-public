import type { GameCommand } from "../../play-types";
export type Area = "yellow" | "blue" | "green" | "orange" | "purple";
export type AreaScores = Record<Area, number>;
export type DieId = Area | "white";
export type TurnPhase = "regular" | "active_extra" | "passive" | "round_bonus" | "over";
export type Target = number | number[];
export type DiceState = Record<
  DieId,
  { value: number; zone: "available" | "kept" | "tray" }
>;
export interface Sheet {
  yellow: number[][];
  blue: number[];
  green: number;
  orange: number[];
  purple: number[];
  earned_rewards: string[];
  /** Resources are kept on paper tracks: an earned one marks the next empty box, a used one crosses out the oldest marked box. */
  rerolls: number; // available = rerolls_earned - rerolls_used
  rerolls_earned: number;
  rerolls_used: number;
  extras: number; // available = extras_earned - extras_used
  extras_earned: number;
  extras_used: number;
  emblem_count: number;
}
export interface Action {
  id: string;
  kind: string;
  params: Record<string, any>;
  label: string;
}
export interface GameEvent {
  sequence: number;
  kind: string;
  payload: Record<string, any>;
}
export interface Observation {
  game_id: string;
  viewer: number | null;
  revision: number;
  decision: { id: string; player: number; kind: string } | null;
  data: {
    round: number;
    active_player: number;
    phase: TurnPhase | "bonus";
    turn_phase: TurnPhase;
    regular_opportunity: number;
    dice: DiceState;
    sheets: Sheet[];
    event_cursor: number;
    rounds_total?: number;
    window?: {
      stage: string;
      extra_used?: string[];
      bonus_stack: {
        frames: {
          pending: { source_id: string; kind: string }[];
          ancestry: string[];
        }[];
        current: { id: string; kind: string } | null;
      };
    };
    score_summary?: { areas: AreaScores; emblem_points: number; total: number }[];
    result?: {
      scores: number[];
      area_scores: AreaScores[];
      emblem_scores: number[];
      placements: number[];
      reason: string;
    };
  };
}
export interface View {
  table_id: string;
  status: string;
  config: { game_id: string; variant: string; players: number };
  observation: Observation | null;
  seats?: {
    seat: number;
    kind: string;
    occupied: boolean;
    display_name?: string | null;
    bot_ref?: unknown;
    availability?: {
      status: "available" | "unavailable";
      reference: unknown;
    } | null;
  }[];
}
export interface BotDescriptor {
  choice_id: string;
  display_name: string;
  kind: "builtin" | "model";
  reference: unknown;
}
export interface ActionList {
  table_id?: string;
  decision_id: string | null;
  actions: Action[];
  /** Seat commands outside a decision (Dice: surrender) and the table revision they are sent with. */
  commands?: GameCommand[];
  expected_revision?: number;
}
export interface BoardSpec {
  rounds: string[];
  resourceSlots: number;
  yellow: {
    rows: (number | null)[][];
    premarked: number[][];
    columnPoints: number[];
    rowBonuses: string[];
    mainDiagonalBonus: string;
  };
  blue: {
    rows: (number | null)[][];
    pointsByCount: number[];
    rowBonuses: string[];
    columnBonuses: string[];
  };
  green: {
    thresholds: number[];
    pointsByCount: number[];
    bonusesByPosition1Based: Record<string, string>;
  };
  orange: {
    multipliers: number[];
    bonusesByPosition1Based: Record<string, string>;
  };
  purple: { length: number; bonusesByPosition1Based: Record<string, string> };
}
export const DICE: DieId[] = [
  "yellow",
  "blue",
  "green",
  "orange",
  "purple",
  "white",
];
export const AREAS: Area[] = ["yellow", "blue", "green", "orange", "purple"];
export const COLORS: Record<DieId, string> = {
  yellow: "#edbd32",
  blue: "#397fd1",
  green: "#399866",
  orange: "#ef893e",
  purple: "#945ac6",
  white: "#f3eee1",
};
