import { it, expect, vi } from "vitest";
import {
  Vector3,
  PerspectiveCamera,
  Mesh,
  PlaneGeometry,
  MeshBasicMaterial,
} from "three";
import * as runtime from "../src/scene/runtime";
import * as layout from "../src/scene/sheet";
import * as dice from "../src/scene/dice";
import { dieSupportHeight, trayRimClearance } from "../src/scene/motion";
import { Quaternion, Euler } from "three";
import sheet from "../../src/boardcraft/games/dice_circuit/data/base-sheet.json";
it("maps every final face to the upward normal", () => {
  expect(dice.faceQuaternion, "Final face mapping is missing").toBeTypeOf(
    "function",
  );
  for (let n = 1; n <= 6; n++)
    expect(
      new Vector3(...dice.FACE_NORMALS[n])
        .applyQuaternion(dice.faceQuaternion(n))
        .distanceTo(new Vector3(0, 1, 0)),
    ).toBeLessThan(1e-6);
});
it("keeps a rolling cube supported and clears the physical tray rim with a low transfer", () => {
  const q = new Quaternion().setFromEuler(new Euler(0.6, 0.2, 0.8));
  expect(dieSupportHeight(q)).toBeGreaterThan(0.36);
  expect(dieSupportHeight(q)).toBeLessThan(0.63);
  expect(trayRimClearance(4.3, -3.5)).toBe(0.435);
  expect(trayRimClearance(4.3, -1.25)).toBeGreaterThan(1.02);
  expect(trayRimClearance(4.3, -1.25)).toBeLessThan(1.1);
});
it("uses canonical sheet cells and all printed reward positions", () => {
  expect(layout.sheetCells, "Canonical sheet layout is missing").toBeTypeOf(
    "function",
  );
  const cells = layout.sheetCells(sheet);
  expect(cells.filter((c) => c.area === "yellow")).toHaveLength(16);
  expect(
    cells.find(
      (c) => c.area === "yellow" && JSON.stringify(c.target) === "[0,3]",
    )?.printed,
  ).toBeNull();
  expect(cells.filter((c) => c.area === "blue").map((c) => c.target)).toEqual([
    2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12,
  ]);
  expect(layout.sheetRewards(sheet)).toHaveLength(31);
  const c = cells.find((c) => c.area === "orange" && c.target === 3)!;
  expect(c.printed).toBe("×2");
  expect(layout.cellAt(sheet, c.x + c.w / 2, c.y + c.h / 2)?.target).toBe(3);
});
it("raycasts against legal objects only using container-relative coordinates", () => {
  expect(runtime.pickObject, "Legal raycaster is missing").toBeTypeOf(
    "function",
  );
  const camera = new PerspectiveCamera(45, 1, 0.1, 10);
  camera.position.z = 3;
  camera.updateMatrixWorld();
  const mesh = new Mesh(new PlaneGeometry(2, 2), new MeshBasicMaterial());
  mesh.updateMatrixWorld();
  expect(
    runtime.pickObject(
      150,
      250,
      { left: 100, top: 200, width: 100, height: 100 },
      camera,
      [mesh],
    )?.object,
  ).toBe(mesh);
  expect(
    runtime.pickObject(
      150,
      250,
      { left: 100, top: 200, width: 100, height: 100 },
      camera,
      [],
    ),
  ).toBeUndefined();
  mesh.geometry.dispose();
  (mesh.material as MeshBasicMaterial).dispose();
});
it("owns resources exactly once and releases listeners, resize and pending frames", () => {
  expect(runtime.ResourceOwner, "Resource ownership is missing").toBeTypeOf(
    "function",
  );
  const r = new runtime.ResourceOwner();
  const dispose = vi.fn();
  r.track({ dispose });
  r.track({ dispose });
  const same = { dispose: vi.fn() };
  r.track(same);
  r.track(same);
  r.dispose();
  r.dispose();
  expect(dispose).toHaveBeenCalledTimes(2);
  expect(same.dispose).toHaveBeenCalledTimes(1);
  const callbacks = new Map<number, FrameRequestCallback>();
  let id = 0;
  const scheduler = new runtime.RenderScheduler(
    (cb) => {
      callbacks.set(++id, cb);
      return id;
    },
    (n) => {
      callbacks.delete(n);
    },
    () => false,
  );
  const render = vi.fn();
  scheduler.setFrame(render);
  scheduler.request();
  scheduler.request();
  expect(callbacks.size).toBe(1);
  callbacks.get(id)!(0);
  callbacks.clear();
  expect(render).toHaveBeenCalledTimes(1);
  scheduler.request();
  scheduler.dispose();
  expect(callbacks.size).toBe(0);
});
it("does not schedule or render hidden scenes", () => {
  let hidden = true;
  const callbacks: FrameRequestCallback[] = [];
  const render = vi.fn();
  const scheduler = new runtime.RenderScheduler(
    (cb) => {
      callbacks.push(cb);
      return callbacks.length;
    },
    () => {},
    () => hidden,
  );
  scheduler.setFrame(render);
  scheduler.request();
  expect(callbacks).toHaveLength(0);
  hidden = false;
  scheduler.request();
  hidden = true;
  callbacks[0](0);
  expect(render).not.toHaveBeenCalled();
  scheduler.dispose();
});

import * as shared from "../src/scene/table";
it("fits the four board corners above a mobile HUD without changing the desktop preset",()=>{
  const camera=new PerspectiveCamera(40,360/800,.1,100);
  camera.position.set(0,10.5,14.5);camera.lookAt(0,0,2.2);camera.updateMatrixWorld();
  const corners=[-5,5].flatMap(x=>[-5,5].map(z=>new Vector3(x,.12,z)));
  const fit=(shared as any).fitPerspective;
  expect(fit).toBeTypeOf("function");
  fit(camera,corners,1,.54);
  for(const p of corners){const projected=p.clone().project(camera);expect(Math.abs(projected.x)).toBeLessThanOrEqual(1);expect(Math.abs(projected.y)).toBeLessThanOrEqual(.54);}
  camera.aspect=2;fit(camera,corners,.83,1);expect(camera.zoom).toBe(1);
});
