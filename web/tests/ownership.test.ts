import { it, expect } from "vitest";
import * as model from "../src/model";
function view(active: number, phase = "regular", players = 2, viewer = 0) {
  return {
    config: { players },
    observation: {
      viewer,
      data: {
        active_player: active,
        phase,
        regular_opportunity: ["passive", "round_bonus", "over"].includes(phase)
          ? 0
          : 1,
      },
    },
  } as any;
}
it("places kept dice on the active seat paper while the authoritative zones stay unchanged", () => {
  const pose = model.keptPose;
  expect(pose, "Kept ownership mapping is missing").toBeTypeOf("function");
  const own = pose(view(0), 0),
    other = pose(view(1), 0);
  expect(own).toEqual({ x: -5.79, y: 0.459, z: -2.06 });
  expect(other.x).toBeCloseTo(1.79);
  expect(other.z).toBeCloseTo(-6.94);
  expect(model.keptOwner(view(0))).toEqual({ kind: "seat", seat: 0 });
  expect(model.keptOwner(view(1))).toEqual({ kind: "seat", seat: 1 });
});
it("uses the same seat transform regardless of whether that seat is viewed from its own paper", () => {
  const pose = model.keptPose;
  expect(pose).toBeTypeOf("function");
  expect(pose(view(1, "regular", 3, 1), 1)).toEqual({
    x: -5.79,
    y: 0.459,
    z: -1.19,
  });
  const opponent = pose(view(1, "regular", 3, 2), 1);
  expect(opponent.x).toBeCloseTo(-5.71);
  expect(opponent.z).toBeCloseTo(-7.81);
});
it("gives the solo simulated passive upper dice a distinct opposing location, never the human paper", () => {
  const pose = model.keptPose;
  expect(pose).toBeTypeOf("function");
  const simulated = view(0, "passive", 1);
  expect(model.keptOwner(simulated)).toEqual({ kind: "simulated" });
  expect(model.keptOwner(view(0, "round_bonus", 1))).toEqual({
    kind: "simulated",
  });
  expect(model.keptOwner(view(0, "over", 1))).toEqual({ kind: "simulated" });
  const p = pose(simulated, 0);
  expect(p.z).toBeLessThan(-6);
  expect(p.x).toBeGreaterThan(2);
  expect(p).not.toEqual(pose(view(0, "regular", 1), 0));
});
