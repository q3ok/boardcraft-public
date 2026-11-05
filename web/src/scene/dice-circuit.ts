import * as THREE from "three";
import { dieSupportHeight, trayRimClearance } from "./motion";
import { emblemSource } from "../emblem";
import { RoundedBoxGeometry } from "three/addons/geometries/RoundedBoxGeometry.js";
import { SharedTable3D } from "./table";
import { ResourceOwner } from "./runtime";
import { DiceFactory, faceQuaternion } from "./dice";
import { canvasTexture, feltCanvas } from "./textures";
import { blankSheet, drawSheet, cellAt, PAPER_W, PAPER_H } from "./sheet";
import {
  DiceSlots,
  transitionDice,
  keptOwner,
  keptPose,
  paperPlacement,
  seatLabel,
  seatNames,
  type LegalSelection,
} from "../model";
import {
  DICE,
  type DiceState,
  type DieId,
  type View,
  type BoardSpec,
  type GameEvent,
  type Action,
} from "../games/dice-circuit/types";
const SW = 6,
  SH = 28 / 3,
  SHEET_X = -3.5,
  SHEET_Z = 1.5;
// the sheet cameras frame the paper (and its pad) with a small margin, so that it fills the uncovered screen area
export const PAPER_FRAME_W = SW + 0.14,
  PAPER_FRAME_H = SH + 0.14;
interface Paper {
  mesh: THREE.Mesh;
  canvas: HTMLCanvasElement;
  texture: THREE.CanvasTexture;
  group: THREE.Group;
  seat: number;
}
export class DiceCircuitScene {
  readonly resources = new ResourceOwner();
  readonly group = new THREE.Group();
  private table!: SharedTable3D;
  private factory!: DiceFactory;
  private dice = new Map<DieId, THREE.Group>();
  private papers: Paper[] = [];
  private slots = new DiceSlots();
  private previous: DiceState | null = null;
  private previousOwner = "";
  private view: View | null = null;
  private cancels: (() => void)[] = [];
  private paperKey = "";
  private lobbyPapers = false;
  private sheetSeat = 0;
  private cameraName = "table";
  private props: THREE.Object3D[] = [];
  private simulatedGroup = new THREE.Group();
  private disposed = false;
  constructor(
    private spec: BoardSpec,
    private selection: LegalSelection,
    private onDie: (id: DieId) => void,
    private onTarget: (a: Action) => void,
  ) {}
  mount(table: SharedTable3D) {
    this.table = table;
    table.scene.add(this.group);
    this.factory = new DiceFactory(this.resources);
    for (const id of DICE) {
      const die = this.factory.create(id);
      die.visible = false; // shown only once an observation says what was rolled
      this.dice.set(id, die);
      this.group.add(die);
    }
    this.buildTray();
    this.buildFelt();
    this.buildPencil();
    this.buildSimulatedSpace();
    const image = emblemSource();
    this.resources.listen(image, "load", () => {
      if (this.disposed || !this.view) return;
      this.paperKey = "";
      this.update(this.view, [], this.selection.actions);
    });
    table.registerCamera("table", () => {
      const a = table.container.clientWidth / table.container.clientHeight;
      const k = Math.max(1, 1.3 / a);
      return {
        target: new THREE.Vector3(-0.1, 0, 0.5),
        position: new THREE.Vector3(0.6, 16.4 * k, 13.3 * k),
        // your sheet, the rolling area and the tray stay whole beside the side panels
        fitPoints: [[-6.7, -6], [6.4, -6], [6.4, 6.4], [-6.7, 6.4]].map(([x, z]) => new THREE.Vector3(x, 0.05, z)),
      };
    });
    // Every preset is only a camera position over the same table: nothing is hidden, and the player can orbit,
    // tilt, pan and zoom from any of them (the fit points frame the preset inside the uncovered part of the screen).
    const corners = (x: number, z: number, w: number, d: number) =>
      [[-1, -1], [1, -1], [1, 1], [-1, 1]].map(([a, b]) => new THREE.Vector3(x + (a * w) / 2, 0.05, z + (b * d) / 2));
    table.registerCamera("top", () => ({
      target: new THREE.Vector3(-0.2, 0, -3.6),
      position: new THREE.Vector3(-0.2, 27, 0.6),
      fitPoints: corners(-0.2, -3.6, 21, 22),
    }));
    const paperView = (pos: THREE.Vector3, facing: number) => ({
      target: pos.clone(),
      // a little in front of the paper, looking down at it, so its rows read upright and the dice stay in reach
      position: pos.clone().add(new THREE.Vector3(0, 9.5, 2.6 * facing)),
      fitPoints: corners(pos.x, pos.z, PAPER_FRAME_W, PAPER_FRAME_H),
    });
    table.registerCamera("sheet", () => paperView(new THREE.Vector3(SHEET_X, 0, SHEET_Z), 1));
    table.registerCamera("opponents", () => {
      const p =
        this.papers.find((p) => p.seat === this.sheetSeat) ?? this.papers[0];
      const pos = p?.group.position ?? new THREE.Vector3(0, 0, -9);
      return paperView(pos.clone(), p?.group.rotation.y === 0 ? 1 : -1);
    });
    let down: { x: number; y: number } | null = null;
    this.resources.listen(table.canvas, "pointerdown", ((e: PointerEvent) => {
      down = { x: e.clientX, y: e.clientY };
    }) as EventListener);
    this.resources.listen(table.canvas, "pointerup", ((e: PointerEvent) => {
      if (!down || Math.hypot(e.clientX - down.x, e.clientY - down.y) > 8)
        return;
      down = null;
      this.pick(e);
    }) as EventListener);
    this.resources.listen(table.canvas, "pointermove", ((e: PointerEvent) => {
      const legal = [...this.dice]
        .filter(([id]) =>
          this.selection.actions.some((a) => a.params.die_id === id),
        )
        .map(([, d]) => d);
      table.canvas.style.cursor = table.pick(e, legal)
        ? "pointer"
        : this.selection.targets().length
          ? "crosshair"
          : "grab";
    }) as EventListener);
    table.setCamera("table");
  }
  private mesh(
    geometry: THREE.BufferGeometry,
    material: THREE.Material | THREE.Material[],
  ) {
    const m = new THREE.Mesh(this.resources.track(geometry), material);
    m.castShadow = m.receiveShadow = true;
    return m;
  }
  private mat(params: THREE.MeshStandardMaterialParameters) {
    return this.resources.track(new THREE.MeshStandardMaterial(params));
  }
  private buildTray() {
    const group = new THREE.Group();
    group.position.set(4.3, 0, -3.5);
    group.rotation.y = -0.08;
    this.group.add(group);
    this.props.push(group);
    const round = (w: number, h: number, r: number) => {
      const shape = new THREE.Shape();
      const x = -w / 2,
        y = -h / 2;
      shape.moveTo(x + r, y);
      shape.lineTo(x + w - r, y);
      shape.quadraticCurveTo(x + w, y, x + w, y + r);
      shape.lineTo(x + w, y + h - r);
      shape.quadraticCurveTo(x + w, y + h, x + w - r, y + h);
      shape.lineTo(x + r, y + h);
      shape.quadraticCurveTo(x, y + h, x, y + h - r);
      shape.lineTo(x, y + r);
      shape.quadraticCurveTo(x, y, x + r, y);
      return shape;
    };
    const metal = this.resources.track(
      new THREE.MeshPhysicalMaterial({
        color: "#c0c5c8",
        metalness: 1,
        roughness: 0.29,
        clearcoat: 0.3,
        envMapIntensity: 1.15,
      }),
    );
    const base = this.mesh(
      new RoundedBoxGeometry(4.9, 0.22, 4.9, 6, 0.38),
      metal,
    );
    base.position.y = 0.13;
    group.add(base);
    const shape = round(4.85, 4.85, 0.56);
    shape.holes.push(round(4.25, 4.25, 0.38));
    const geometry = new THREE.ExtrudeGeometry(shape, {
      depth: 0.38,
      bevelEnabled: true,
      bevelSize: 0.065,
      bevelThickness: 0.06,
      bevelSegments: 4,
      curveSegments: 20,
    });
    geometry.rotateX(-Math.PI / 2);
    const rim = this.mesh(geometry, metal);
    rim.position.y = 0.22;
    group.add(rim);
    const felt = this.resources.track(canvasTexture(feltCanvas()));
    felt.wrapS = felt.wrapT = THREE.RepeatWrapping;
    felt.repeat.set(5, 5);
    const floor = this.mesh(
      new RoundedBoxGeometry(4.23, 0.04, 4.23, 5, 0.32),
      this.mat({
        map: felt,
        color: "#60936b",
        roughness: 1,
        envMapIntensity: 0.2,
      }),
    );
    floor.position.y = 0.255;
    group.add(floor);
  }
  private buildFelt() {
    const texture = this.resources.track(canvasTexture(feltCanvas()));
    texture.wrapS = texture.wrapT = THREE.RepeatWrapping;
    texture.repeat.set(8, 8);
    const felt = this.mesh(
      new RoundedBoxGeometry(5.4, 0.055, 3.65, 4, 0.22),
      this.mat({ map: texture, color: "#5f8867", roughness: 1 }),
    );
    felt.position.set(3.3, 0.045, 3.2);
    this.group.add(felt);
    this.props.push(felt);
    // The felt is where the available dice are rolled; a printed strip says so when it is empty.
    const canvas = document.createElement("canvas");
    canvas.width = 640;
    canvas.height = 72;
    const g = canvas.getContext("2d");
    if (g) {
      g.fillStyle = "#f5f0de";
      g.fillRect(0, 0, 640, 72);
      g.font = "600 34px Arial";
      g.textAlign = "center";
      g.textBaseline = "middle";
      g.fillStyle = "#31553a";
      g.fillText("Rolling area · available dice", 320, 37);
    }
    const label = this.mesh(
      new THREE.PlaneGeometry(3.4, 0.38),
      this.resources.track(new THREE.MeshBasicMaterial({ map: this.resources.track(canvasTexture(canvas)), toneMapped: false })),
    );
    label.rotation.x = -Math.PI / 2;
    label.position.set(3.3, 0.08, 4.72);
    this.group.add(label);
    this.props.push(label);
  }
  private buildSimulatedSpace() {
    const texture = this.resources.track(canvasTexture(feltCanvas()));
    texture.wrapS = texture.wrapT = THREE.RepeatWrapping;
    texture.repeat.set(5, 2);
    const felt = this.mesh(
      new RoundedBoxGeometry(4.6, 0.055, 1.5, 4, 0.2),
      this.mat({ map: texture, color: "#64826b", roughness: 1 }),
    );
    felt.position.set(4.3, 0.045, -7.4);
    this.simulatedGroup.add(felt);
    const canvas = document.createElement("canvas");
    canvas.width = 640;
    canvas.height = 88;
    const g = canvas.getContext("2d")!;
    g.fillStyle = "#f5f0de";
    g.fillRect(0, 0, 640, 88);
    g.font = "600 38px Arial";
    g.textAlign = "center";
    g.textBaseline = "middle";
    g.fillStyle = "#31553a";
    g.fillText("Simulated turn", 320, 44);
    const map = this.resources.track(canvasTexture(canvas));
    const label = this.mesh(
      new THREE.PlaneGeometry(3.2, 0.44),
      this.resources.track(
        new THREE.MeshBasicMaterial({ map, toneMapped: false }),
      ),
    );
    label.rotation.x = -Math.PI / 2;
    label.position.set(1.1, 0.06, -7.4);
    this.simulatedGroup.add(label);
    this.group.add(this.simulatedGroup);
  }
  private buildPencil() {
    const group = new THREE.Group();
    const body = this.mesh(
      new THREE.CylinderGeometry(0.065, 0.065, 3.6, 6),
      this.mat({ color: "#246147", roughness: 0.4, metalness: 0.1 }),
    );
    body.rotation.z = Math.PI / 2;
    group.add(body);
    const tip = this.mesh(
      new THREE.ConeGeometry(0.065, 0.35, 6),
      this.mat({ color: "#d6b98c", roughness: 0.8 }),
    );
    tip.rotation.z = -Math.PI / 2;
    tip.position.x = 1.97;
    group.add(tip);
    const lead = this.mesh(
      new THREE.ConeGeometry(0.025, 0.1, 6),
      this.mat({ color: "#292a26" }),
    );
    lead.rotation.z = -Math.PI / 2;
    lead.position.x = 2.18;
    group.add(lead);
    const cap = this.mesh(
      new THREE.CylinderGeometry(0.07, 0.07, 0.15, 12),
      this.mat({ color: "#c5c6c3", metalness: 1, roughness: 0.25 }),
    );
    cap.rotation.z = Math.PI / 2;
    cap.position.x = -1.8;
    group.add(cap);
    group.position.set(-7.1, 0.09, 2);
    group.rotation.y = 1.25;
    this.group.add(group);
    this.props.push(group);
  }
  private addPaper(seat: number) {
    const canvas = document.createElement("canvas");
    const texture = this.resources.track(
      canvasTexture(
        canvas,
        this.table.renderer.capabilities.getMaxAnisotropy(),
      ),
    );
    const edge = this.mat({ color: "#e2dbc4", roughness: 1 });
    const top = this.resources.track(
      new THREE.MeshBasicMaterial({ map: texture, toneMapped: false }),
    );
    const mesh = this.mesh(new THREE.BoxGeometry(SW, 0.035, SH), [
      edge,
      edge,
      top,
      edge,
      edge,
      edge,
    ]);
    mesh.position.y = 0.08;
    const group = new THREE.Group();
    group.add(mesh);
    const pad = this.mesh(
      new THREE.BoxGeometry(SW + 0.06, 0.025, SH + 0.05),
      edge,
    );
    pad.position.set(0.04, 0.025, -0.02);
    pad.rotation.y = -0.009;
    group.add(pad);
    // Without an observation (a lobby) nobody has a viewpoint yet, so seat 0 sits in front.
    const placement = paperPlacement(this.view?.observation?.viewer ?? null, seat);
    group.position.set(placement.x, 0, placement.z);
    group.rotation.y = placement.rotation;
    this.group.add(group);
    const paper = { mesh, canvas, texture, group, seat };
    mesh.userData.paper = paper;
    this.papers.push(paper);
    return paper;
  }
  private ensurePapers(view: View) {
    const o = view.observation;
    const own = o?.viewer ?? 0;
    const count = o ? o.data.sheets.length : view.config.players;
    if (this.papers.length) {
      if (!this.lobbyPapers || !o) return;
      // The sheets drawn for the lobby now take their real places around the table, own sheet first.
      this.papers.sort((a, b) => Number(b.seat === own) - Number(a.seat === own) || a.seat - b.seat);
      for (const p of this.papers) {
        const placement = paperPlacement(o.viewer, p.seat);
        p.group.position.set(placement.x, 0, placement.z);
        p.group.rotation.y = placement.rotation;
      }
      this.lobbyPapers = false;
      this.paperKey = "";
      return;
    }
    this.lobbyPapers = !o;
    this.addPaper(own);
    for (let seat = 0; seat < count; seat++)
      if (seat !== own) this.addPaper(seat);
  }
  /** Blank sheets for a table that has not started: printed layout only, no marks, scores or rolls. */
  private drawLobbyPapers(players: number) {
    const key = JSON.stringify(["lobby", players]);
    if (key === this.paperKey) return;
    this.paperKey = key;
    for (const p of this.papers) {
      drawSheet(p.canvas, this.spec, blankSheet(this.spec), this.view ? seatNames(this.view)[p.seat] ?? `Player ${p.seat + 1}` : `Player ${p.seat + 1}`, 0, []);
      p.texture.needsUpdate = true;
    }
  }
  setCamera(name: string) {
    this.cameraName = name;
    if (name === "opponents" && this.sheetSeat === this.papers[0]?.seat)
      this.sheetSeat =
        this.papers.find((p) => p !== this.papers[0])?.seat ?? this.sheetSeat;
    this.applyVisibility();
    this.table.setCamera(name);
    this.table.invalidateShadows();
  }
  /** The cameras never hide anything: the tray, the dice, every sheet and the pencil stay on the table. */
  private applyVisibility() {
    for (const p of this.props) p.visible = true;
    for (const p of this.papers) p.group.visible = true;
    const observed = !!this.view?.observation;
    // Before the first observation there are no rolls to show, so the dice stay away.
    for (const [, d] of this.dice) d.visible = observed;
    this.simulatedGroup.visible = observed && keptOwner(this.view!).kind === "simulated";
  }
  setOpponent(seat: number) {
    this.sheetSeat = seat;
    this.setCamera("opponents");
  }
  private pose(id: DieId, dice: DiceState) {
    const slot = this.slots.get(id)!;
    if (slot.zone === "kept") {
      const p = keptPose(this.view!, slot.slot);
      return new THREE.Vector3(p.x, p.y, p.z);
    }
    if (slot.zone === "tray") {
      const x = ((slot.slot % 3) - 1) * 1.12,
        z = (Math.floor(slot.slot / 3) - 0.5) * 1.35;
      return new THREE.Vector3(
        x * Math.cos(-0.08) + z * Math.sin(-0.08) + 4.3,
        0.635,
        -x * Math.sin(-0.08) + z * Math.cos(-0.08) - 3.5,
      );
    }
    const i = DICE.indexOf(id);
    return new THREE.Vector3(
      1.7 + (i % 3) * 1.45,
      0.435,
      2.4 + Math.floor(i / 3) * 1.65,
    );
  }
  update(view: View, events: GameEvent[], _actions: Action[]) {
    this.view = view;
    this.ensurePapers(view);
    if (!view.observation) {
      this.drawLobbyPapers(view.config.players);
      this.applyVisibility();
      this.table.invalidateShadows();
      return;
    }
    const data = view.observation.data;
    const key = JSON.stringify([
      data.sheets,
      data.round,
      this.selection.targets().map((a) => a.id),
    ]);
    if (key !== this.paperKey) {
      this.paperKey = key;
      for (const p of this.papers) {
        drawSheet(
          p.canvas,
          this.spec,
          data.sheets[p.seat],
          seatLabel(view, p.seat),
          data.round,
          p === this.papers[0] ? this.selection.targets() : [],
        );
        p.texture.needsUpdate = true;
      }
    }
    const transition = transitionDice(this.previous, data.dice, events);
    const owner = JSON.stringify(keptOwner(view));
    const ownerChanged = owner !== this.previousOwner;
    const changed =
      JSON.stringify(this.previous) !== JSON.stringify(data.dice) ||
      ownerChanged;
    if (changed || transition.rolled.length) {
      for (const cancel of this.cancels.splice(0)) cancel();
      this.slots.update(data.dice);

      for (const id of DICE) {
        const d = this.dice.get(id)!,
          end = this.pose(id, data.dice),
          q = faceQuaternion(data.dice[id].value).premultiply(
            new THREE.Quaternion().setFromAxisAngle(
              new THREE.Vector3(0, 1, 0),
              ((DICE.indexOf(id) % 3) - 1) * 0.13,
            ),
          );
        const rolled = transition.rolled.includes(id),
          transfer =
            transition.transferred.includes(id) ||
            (ownerChanged && data.dice[id].zone === "kept");
        if (!this.previous) {
          d.position.copy(
            rolled
              ? new THREE.Vector3(
                  1.7 + (DICE.indexOf(id) % 3) * 1.45,
                  0.435,
                  2.4 + Math.floor(DICE.indexOf(id) / 3) * 1.65,
                )
              : end,
          );
          d.quaternion.copy(q);
        }
        if (rolled || transfer) {
          const start = d.position.clone(),
            fromQ = d.quaternion.clone();
          const t0 = performance.now();
          const rollingTransfer = rolled && data.dice[id].zone !== "available";
          const rollEnd = rollingTransfer
            ? new THREE.Vector3(
                1.7 + (DICE.indexOf(id) % 3) * 1.45,
                0.435,
                2.4 + Math.floor(DICE.indexOf(id) / 3) * 1.65,
              )
            : end.clone();
          const duration = rolled ? 900 : 420;
          const full = duration + (rollingTransfer ? 430 : 0);
          const axis = new THREE.Vector3(0.6, 0.15, 0.8).normalize();
          this.cancels.push(
            this.table.animate((time, finish) => {
              const raw = finish ? 1 : Math.min(1, (time - t0) / full);
              const millis = raw * full;
              const t = Math.min(1, millis / duration);
              const e = 1 - Math.pow(1 - t, 3);
              d.position.lerpVectors(start, rollEnd, e);
              if (rolled) {
                d.position.x +=
                  Math.sin(t * Math.PI) * (DICE.indexOf(id) % 2 ? 0.4 : -0.4);
                const spin = 7.5 * (1 - t) * (1 - t);
                d.quaternion
                  .copy(q)
                  .premultiply(
                    new THREE.Quaternion().setFromAxisAngle(axis, spin),
                  );
                const support = dieSupportHeight(d.quaternion);
                d.position.y =
                  Math.max(d.position.y, support + 0.08) +
                  Math.abs(Math.sin(t * Math.PI * 3)) * 0.12 * (1 - t);
              } else {
                d.quaternion.slerpQuaternions(fromQ, q, e);
                d.position.y += Math.sin(t * Math.PI) * 0.12;
              }
              if (rollingTransfer && millis > duration) {
                const transferT = Math.min(1, (millis - duration) / 430);
                d.position.lerpVectors(
                  rollEnd,
                  end,
                  1 - Math.pow(1 - transferT, 3),
                );
                d.position.y += Math.sin(transferT * Math.PI) * 0.12;
                d.quaternion.copy(q);
              }
              if (raw < 1)
                d.position.y = Math.max(
                  d.position.y,
                  trayRimClearance(d.position.x, d.position.z),
                );
              if (raw >= 1) {
                d.position.copy(end);
                d.quaternion.copy(q);
              }
              this.table.renderer.shadowMap.needsUpdate = true;
              return raw < 1;
            }),
          );
        } else {
          d.position.copy(end);
          d.quaternion.copy(q);
        }
      }
    }
    for (const id of DICE)
      this.factory.highlight(
        id,
        this.selection.selectedDie === id,
        this.selection.actions.some((a) => a.params.die_id === id),
      );
    this.previous = structuredClone(data.dice);
    this.previousOwner = owner;
    this.applyVisibility();
    this.table.invalidateShadows();
  }
  private pick(event: PointerEvent) {
    const legal = [...this.dice]
      .filter(([id]) =>
        this.selection.actions.some((a) => a.params.die_id === id),
      )
      .map(([, d]) => d);
    const hit = this.table.pick(event, legal);
    if (hit) {
      let o: THREE.Object3D | null = hit.object;
      while (o && !o.userData.dieId) o = o.parent;
      if (o) this.onDie(o.userData.dieId);
      return;
    }
    const paper = this.papers[0];
    if (!paper || !this.selection.targets().length) return;
    const h = this.table.pick(event, [paper.mesh]);
    if (!h) return;
    const local = paper.mesh.worldToLocal(h.point.clone());
    const c = cellAt(
      this.spec,
      (local.x / SW + 0.5) * PAPER_W,
      (local.z / SH + 0.5) * PAPER_H,
    );
    if (c) {
      const action = this.selection.pick(c.area, c.target);
      if (action) this.onTarget(action);
    }
  }
  dispose() {
    if (this.disposed) return;
    this.disposed = true;
    for (const c of this.cancels.splice(0)) c();
    this.resources.dispose();
    this.group.removeFromParent();
    this.group.clear();
    this.dice.clear();
    this.papers = [];
    this.previous = null;
  }
}
