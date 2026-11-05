import { describe, it, expect } from "vitest";
import * as model from "../src/model";
const action = (die: string, area: string, target: unknown) => ({
  id: `${die}-${area}-${JSON.stringify(target)}`,
  kind: "select_die",
  params: { die_id: die, area, target },
  label: "Use a die.",
});
describe("authoritative interaction mapping", () => {
  it("maps only supplied legal actions and clears selected dice on new decisions", () => {
    expect(model.LegalSelection, "Legal selection is missing").toBeTypeOf(
      "function",
    );
    const m = new model.LegalSelection();
    const actions = [
      action("white", "yellow", [1, 0]),
      action("white", "orange", 0),
      action("blue", "blue", 7),
    ];
    m.update("d1", actions);
    m.selectDie("white");
    expect(m.targets().map((a) => a.params.area)).toEqual(["yellow", "orange"]);
    expect(m.pick("yellow", [1, 0])?.id).toBe(actions[0].id);
    expect(m.pick("yellow", [0, 0])).toBeUndefined();
    m.update("d2", actions);
    expect(m.selectedDie).toBeNull();
    m.update(null, actions);
    expect(m.targets()).toEqual([]);
  });
  it("uses the owning decision without restricting passive choices to the active seat", () => {
    expect(model.actionContext, "Decision mapping is missing").toBeTypeOf(
      "function",
    );
    const view = {
      observation: {
        revision: 9,
        viewer: 2,
        decision: { id: "own", player: 2, kind: "passive" },
        data: { active_player: 0 },
      },
    };
    expect(
      model.actionContext(
        view as any,
        { decision_id: "own", actions: [action("green", "green", 0)] } as any,
      ),
    ).toHaveLength(1);
    expect(
      model.actionContext(
        view as any,
        { decision_id: "old", actions: [action("green", "green", 0)] } as any,
      ),
    ).toEqual([]);
    view.observation.viewer = null as any;
    expect(
      model.actionContext(
        view as any,
        { decision_id: "own", actions: [action("green", "green", 0)] } as any,
      ),
    ).toEqual([]);
  });
  it("retains existing kept and tray slots when another die arrives", () => {
    expect(model.DiceSlots, "Stable slots are missing").toBeTypeOf("function");
    const slots = new model.DiceSlots();
    slots.update({
      yellow: { value: 6, zone: "kept" },
      blue: { value: 2, zone: "tray" },
      green: { value: 3, zone: "available" },
    } as any);
    const before = slots.get("blue");
    slots.update({
      yellow: { value: 6, zone: "kept" },
      blue: { value: 2, zone: "tray" },
      green: { value: 1, zone: "tray" },
    } as any);
    expect(slots.get("blue")).toEqual(before);
    expect(slots.get("green")?.slot).toBe(1);
  });
  it("animates only IDs from server roll events and records zone transfers separately", () => {
    expect(model.transitionDice, "Event transitions are missing").toBeTypeOf(
      "function",
    );
    const old = {
      yellow: { value: 5, zone: "kept" },
      blue: { value: 1, zone: "available" },
      white: { value: 2, zone: "tray" },
    };
    const next = {
      yellow: { value: 5, zone: "kept" },
      blue: { value: 4, zone: "available" },
      white: { value: 2, zone: "tray" },
    };
    expect(
      model.transitionDice(old as any, next as any, [
        { sequence: 1, kind: "dice_rolled", payload: { values: { blue: 4 } } },
      ]),
    ).toEqual({ rolled: ["blue"], transferred: [] });
    next.blue.zone = "tray";
    expect(model.transitionDice(old as any, next as any, [])).toEqual({
      rolled: [],
      transferred: ["blue"],
    });
  });
  it("rejects late response revisions and disposed session generations", () => {
    expect(
      model.ResponseContext,
      "Stale response context is missing",
    ).toBeTypeOf("function");
    const c = new model.ResponseContext();
    const generation = c.generation;
    expect(c.accept(generation, 5)).toBe(true);
    expect(c.accept(generation, 3)).toBe(false);
    c.reset();
    expect(c.accept(generation, 6)).toBe(false);
  });
});
