import type { GameEvent } from "../../play-types";
export function presentationEvents(previous: number | null, cursor: number, events: GameEvent[], reset: boolean): GameEvent[] {
  if (reset || previous === null || cursor <= previous)
    return [];
  const fresh = events.filter(e => e.sequence > previous && e.sequence <= cursor);
  return fresh.length === cursor - previous && fresh.every((e, i) => e.sequence === previous + i + 1) ? fresh : [];
}
