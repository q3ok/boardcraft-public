// Short English lines for the events of one Dice Circuit replay position (shown under the replay timeline).
import type { GameEvent } from "../../play-types";

const cap = (s: string) => s.charAt(0).toUpperCase() + s.slice(1);

function field(params: Record<string, any>) {
  const t = params.target;
  if (params.area === "yellow" && Array.isArray(t)) return `yellow row ${t[0] + 1}, column ${t[1] + 1}`;
  if (params.area === "blue") return `blue ${t}`;
  return typeof t === "number" ? `${params.area} field ${t + 1}` : String(params.area ?? "");
}

/** "You" reads as a person: "You use", "You reroll". */
const yourVerbs = (line: string | null) => line && line.startsWith("You ")
  ? line.replace(/^You (uses|places|resolves|chooses|rerolls|takes|finishes|cannot)/, (_, verb: string) => "You " + ({ uses: "use", places: "place", resolves: "resolve", chooses: "choose", rerolls: "reroll", takes: "take", finishes: "finish", cannot: "cannot" } as Record<string, string>)[verb])
  : line;
export function describeDiceEvent(e: Pick<GameEvent, "kind" | "payload">, names: string[] = []): string | null {
  return yourVerbs(describeOne(e, names));
}
function describeOne(e: Pick<GameEvent, "kind" | "payload">, names: string[]): string | null {
  const p = e.payload ?? {};
  const player = (seat: unknown) => (typeof seat === "number" ? names[seat] ?? `Player ${seat + 1}` : "");
  switch (e.kind) {
    case "round_started": return `Round ${p.round} begins`;
    case "turn_started": {
      const who = player(p.player);
      const whose = who === "You" ? "Your" : `${who}'s`;
      return p.simulated ? `Round ${p.round ?? ""}: simulated opponent's turn`.replace("Round : ", "") : `${whose} turn`;
    }
    case "round_reward": return `Round ${p.round} reward: ${p.reward === "reroll" ? "a reroll" : p.reward === "extra" ? "an extra die" : String(p.reward).replaceAll("_", " ")}`;
    case "dice_rolled": {
      const values = Object.entries(p.values ?? {}).map(([die, v]) => `${die} ${v}`).join(", ");
      if (p.phase === "solo_passive") return `Simulated roll: ${values}`;
      return `Roll ${p.opportunity ?? ""}${p.phase === "passive" ? " (shared tray)" : ""}: ${values}`.replace("Roll  ", "Roll ");
    }
    case "passive_started": return "The others choose from the shared tray";
    case "action_applied":
    case "bonus_auto_resolved": {
      const a = p.action ?? {}, who = player(p.player), params = a.params ?? {};
      const auto = e.kind === "bonus_auto_resolved" ? " (automatic)" : "";
      if (a.kind === "select_die") return `${who} uses the ${params.die_id} die on ${field(params)}${auto}`;
      if (a.kind === "place_bonus") return `${who} places a bonus on ${field(params)}${auto}`;
      if (a.kind === "choose_bonus") return `${who} resolves the ${String(params.bonus_id ?? "").replaceAll(":", " ").replaceAll("_", " ")} bonus${auto}`;
      if (a.kind === "choose_round_bonus") return `${who} chooses the ${String(params.bonus ?? "").replaceAll("_", " ")} round bonus${auto}`;
      if (a.kind === "reroll") return `${who} rerolls${auto}`;
      if (a.kind === "extra_die") return `${who} takes an extra die${params.die_id ? ` (${params.die_id})` : ""}${auto}`;
      if (a.kind === "finish_extra") return `${who} finishes the extra dice${auto}`;
      return `${who}: ${String(a.kind ?? "move").replaceAll("_", " ")}${auto}`;
    }
    case "resigned": return `${player(p.player)} surrenders; the game ends`;
    case "game_finished": return `Game finished: ${(p.scores ?? []).map((s: number, i: number) => `${player(i)} ${s}`).join(", ")}`;
    case "window_published": {
      const entries = (p.entries ?? []) as { player: number; area: string; target: any; value?: number }[];
      const made = entries.filter(x => x.area !== "extras_used").map(x => `${player(x.player)} entered ${field(x)}${x.value != null ? ` (${x.value})` : ""}`);
      return made.length ? `Revealed: ${made.join(", ")}` : null;
    }
    case "tray_formed": return `Tray: ${(p.tray ?? []).join(", ")}`;
    case "opportunity_unused": return `${player(p.player)} cannot use roll ${p.opportunity}`;
    case "game_started": return null;
    default: return cap(e.kind.replaceAll("_", " "));
  }
}

export const describeDiceEvents = (events: GameEvent[], names: string[] = []) => events.map(e => describeDiceEvent(e, names)).filter(Boolean).join(" · ");
