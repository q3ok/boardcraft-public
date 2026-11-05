import {
  type Action,
  type ActionList,
  type DiceState,
  type DieId,
  type GameEvent,
  type Target,
  type View,
} from "./games/dice-circuit/types";
/** Seat names from the table roster ("Strategist", a model, "Human"); equal names get their seat number. */
type Roster = { config?: { players?: number }; seats?: { seat: number; display_name?: string | null }[] };
export function seatNames(view: Roster): string[] {
  const count = Math.max(view.config?.players ?? 0, view.seats?.length ?? 0);
  const names = Array.from({ length: count }, (_, i) => view.seats?.find((s) => s.seat === i)?.display_name?.trim() || `Player ${i + 1}`);
  return names.map((n, i) => (names.filter((x) => x === n).length > 1 ? `${n} ${i + 1}` : n));
}
/** "You" for the viewer's own seat, the seat name otherwise. */
export function seatLabel(view: Roster & { observation?: { viewer: number | null } | null }, seat: number): string {
  return seat === view.observation?.viewer ? "You" : seatNames(view)[seat] ?? `Player ${seat + 1}`;
}
export interface WindowEntry { player: number; area: string; target: any; value?: number }
/** What each sheet gained between two public sheet lists (a published simultaneous window); never used before the reveal. */
export function windowEntries(before: any[] | undefined, after: any[] | undefined): WindowEntry[] {
  if (!before || !after || before.length !== after.length) return [];
  const out: WindowEntry[] = [];
  after.forEach((now, player) => {
    const old = before[player];
    if (!old || !now) return;
    const had = new Set((old.yellow ?? []).map((c: number[]) => c.join(",")));
    for (const cell of now.yellow ?? []) if (!had.has(cell.join(","))) out.push({ player, area: "yellow", target: cell });
    for (const value of now.blue ?? []) if (!(old.blue ?? []).includes(value)) out.push({ player, area: "blue", target: value });
    for (let f = old.green ?? 0; f < (now.green ?? 0); f++) out.push({ player, area: "green", target: f });
    for (const area of ["orange", "purple"])
      for (let f = (old[area] ?? []).length; f < (now[area] ?? []).length; f++) out.push({ player, area, target: f, value: now[area][f] });
    if ((now.extras_used ?? 0) > (old.extras_used ?? 0)) out.push({ player, area: "extras_used", target: now.extras_used - old.extras_used });
  });
  return out;
}
export function sameTarget(a: unknown, b: unknown) {
  return JSON.stringify(a) === JSON.stringify(b);
}
export function actionContext(view: View, actions: ActionList): Action[] {
  const o = view.observation;
  return o && o.viewer !== null && o.decision?.id === actions.decision_id
    ? actions.actions
    : [];
}
export class LegalSelection {
  decisionId: string | null = null;
  selectedDie: DieId | null = null;
  actions: Action[] = [];
  update(id: string | null, actions: Action[]) {
    if (id !== this.decisionId) this.selectedDie = null;
    this.decisionId = id;
    this.actions = id ? actions : [];
    if (
      this.selectedDie &&
      !this.actions.some((a) => a.params.die_id === this.selectedDie)
    )
      this.selectedDie = null;
  }
  selectDie(id: DieId) {
    if (this.actions.some((a) => a.params.die_id === id)) this.selectedDie = id;
  }
  targets() {
    return this.actions.filter(
      (a) =>
        a.params.area &&
        (!a.params.die_id || a.params.die_id === this.selectedDie),
    );
  }
  pick(area: string, target: Target) {
    return this.targets().find(
      (a) => a.params.area === area && sameTarget(a.params.target, target),
    );
  }
}
export class DiceSlots {
  private slots = new Map<string, { zone: string; slot: number }>();
  update(dice: DiceState) {
    for (const [id, s] of [...this.slots])
      if (!dice[id as DieId] || dice[id as DieId].zone !== s.zone)
        this.slots.delete(id);
    for (const [id, die] of Object.entries(dice)) {
      if (this.slots.has(id)) continue;
      const used = new Set(
        [...this.slots.values()]
          .filter((s) => s.zone === die.zone)
          .map((s) => s.slot),
      );
      let slot = 0;
      while (used.has(slot)) slot++;
      this.slots.set(id, { zone: die.zone, slot });
    }
  }
  get(id: string) {
    return this.slots.get(id);
  }
  clear() {
    this.slots.clear();
  }
}
export function transitionDice(
  old: DiceState | null,
  next: DiceState,
  events: GameEvent[],
) {
  const rolled = new Set<string>();
  for (const e of events)
    if (e.kind === "dice_rolled")
      for (const id of Object.keys(e.payload.values ?? {}))
        if (next[id as DieId]) rolled.add(id);
  return {
    rolled: [...rolled],
    transferred: old
      ? Object.keys(next).filter(
          (id) => old[id as DieId]?.zone !== next[id as DieId].zone,
        )
      : [],
  };
}
export {ResponseContext} from "./play-types";
export function paperPlacement(viewer: number | null, seat: number) {
  const front = viewer ?? 0;
  if (seat === front) return { x: -3.5, z: 1.5, rotation: 0 };
  const index = seat > front ? seat - 1 : seat;
  return { x: [-0.5, -8, 7][index], z: -10.5, rotation: Math.PI };
}
export function keptOwner(
  view: View,
): { kind: "seat"; seat: number } | { kind: "simulated" } {
  const data = view.observation!.data;
  return view.config.players === 1 &&
    (data.phase === "passive" || data.regular_opportunity === 0)
    ? { kind: "simulated" }
    : { kind: "seat", seat: data.active_player };
}
export function keptPose(view: View, slot: number) {
  const owner = keptOwner(view);
  if (owner.kind === "simulated")
    return { x: 4.3 + (slot - 1) * 1.22, y: 0.435, z: -7.4 };
  const p = paperPlacement(view.observation!.viewer, owner.seat);
  const direction = p.rotation === 0 ? 1 : -1;
  return {
    x: p.x - 2.29 * direction,
    y: 0.459,
    z: p.z + (-3.56 + slot * 0.87) * direction,
  };
}
