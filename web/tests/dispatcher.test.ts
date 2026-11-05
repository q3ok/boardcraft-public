import { it, expect, vi } from "vitest";
import * as entry from "../src/game-view";
it("routes a different registered game and forwards update and disposal without game-specific conditions", () => {
  const mount = (entry as any).mountRegisteredGameView;
  expect(mount, "Module dispatcher is missing").toBeTypeOf("function");
  const update = vi.fn(),
    dispose = vi.fn();
  const factory = vi.fn(() => ({ update, dispose }));
  const root = document.createElement("div");
  const options = {
    gameId: "fixture",
    boardSpec: { cells: [1, 2] },
    rulesText: "Fixture rules",
  };
  const view = mount(root, options, { fixture: factory });
  const observation = { observation: { data: { unrelated: "fixture data" } } };
  view.update(observation, [], { decision_id: null, actions: [] });
  view.dispose();
  expect(factory).toHaveBeenCalledExactlyOnceWith(root, options);
  expect(update).toHaveBeenCalledExactlyOnceWith(observation, [], {
    decision_id: null,
    actions: [],
  });
  expect(dispose).toHaveBeenCalledTimes(1);
});
it("explains an unavailable game separately from graphics failure", () => {
  const mount = (entry as any).mountRegisteredGameView;
  expect(mount).toBeTypeOf("function");
  const root = document.createElement("div");
  const view = mount(
    root,
    { gameId: "unknown", boardSpec: {}, rulesText: "" },
    {},
  );
  expect(root.textContent).toContain("This game has no playable table");
  expect(root.textContent).not.toContain("WebGL");
  view.dispose();
  expect(root.childElementCount).toBe(0);
});

it("drops a pending module after disposal and delivers only the latest pre-mount snapshot", async () => {
  let ready!: (mount: any) => void;
  const factory = vi.fn(() => ({update:vi.fn(), dispose:vi.fn()}));
  const loader = () => new Promise(resolve => { ready=resolve; });
  const root=document.createElement("div"), options={gameId:"fixture",boardSpec:{},rulesText:""};
  const mount=(entry as any).mountLazyGameView;
  expect(mount).toBeTypeOf("function");
  const first=mount(root,options,{fixture:loader});
  first.dispose();ready(factory);await Promise.resolve();
  expect(factory).not.toHaveBeenCalled();
  const second=mount(root,options,{fixture:loader});
  second.update({revision:1});second.update({revision:2});ready(factory);await Promise.resolve();
  expect(factory.mock.results[0].value.update).toHaveBeenCalledTimes(1);
  expect(factory.mock.results[0].value.update.mock.calls[0][0]).toEqual({revision:2});
  second.dispose();
});
