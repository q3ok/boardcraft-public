import * as THREE from "three";
import { RoundedBoxGeometry } from "three/addons/geometries/RoundedBoxGeometry.js";
import type { SharedTable3D, TableAppearance } from "../../scene/table";
import type { GameEvent, View } from "../../play-types";
import { ResourceOwner } from "../../scene/runtime";
import { animals, createPieces, playerColors } from "./pieces";
import { S, rot, seatRotation, tileLocal, tileRectWorld, toWorld, uvToIndex } from "./geometry";
import { CH, CW, boardTexture, deckName, deckTexture, deedTexture, jailCardTexture, pipTexture, woodTexture } from "./textures";
import type { RentBoard, RentState, Player } from "./types";
import { presentationEvents } from "./events";
const BT = 0.12, Y0 = BT;
const FACES = [1, 6, 2, 5, 3, 4], UP = new THREE.Vector3(0, 1, 0);
export const appearance: TableAppearance = {
  background: "#140c07", fog: [24, 44], exposure: 1.05, wood: woodTexture, size: 60, roughness: .62, orbit: [1.25, 4, 30],
  lighting(sc) {
    sc.add(new THREE.HemisphereLight("#ffe9cc", "#2a1a10", 0.55));
    const lamp = new THREE.SpotLight("#ffe2b8", 300, 45, 0.85, 0.55, 1.6);
    lamp.position.set(2, 14, 5);
    lamp.castShadow = true;
    lamp.shadow.mapSize.set(2048, 2048);
    lamp.shadow.bias = -0.0004;
    sc.add(lamp);
    const fill = new THREE.DirectionalLight("#b8c8ff", 0.35);
    fill.position.set(-8, 6, -6);
    sc.add(fill);
    return lamp.shadow;
  },
};
function disposeTree(obj: THREE.Object3D, keep: Set<unknown>) {
  obj.traverse((o) => {
    const m = o as THREE.Mesh;
    if (m.geometry && !keep.has(m.geometry))
      m.geometry.dispose();
    const mats = Array.isArray(m.material) ? m.material : m.material ? [m.material] : [];
    for (const mat of mats)
      if (!keep.has(mat))
        mat.dispose();
  });
}
interface PawnAnim {
  player: number;
  path: number[];
  start: number;
  step: number;
}
type Snapshot = {
  players: Player[];
  spaces: {
    index: number;
    owner: number | null;
    mortgaged: boolean;
    buildings: number;
  }[];
};
type Intent = (space: number | null, x: number, y: number) => void;
export class RentcraftScene {
  private table!: SharedTable3D;
  private scene = new THREE.Group();
  private resources = new ResourceOwner();
  private pieces = createPieces();
  private cardGeometry = this.resources.track(new THREE.BoxGeometry(1.3, .012, 1.3 * CH / CW));
  private paper = this.resources.track(new THREE.MeshStandardMaterial({ color: "#efe8d8" }));
  private cardMats = new Map<string, THREE.MeshStandardMaterial>();
  private view: Snapshot | null = null;
  private cursor: number | null = null;
  private revision: number | null = null;
  private rolls: number | null = null;
  private status = "";
  private stopMotion?: () => void;
  private reset = false;
  private get canvas() { return this.table.canvas; }
  private get camera() { return this.table.camera; }
  private board!: THREE.Mesh;
  private pawns = new Map<number, THREE.Group>();
  private pawnTile = new Map<number, number>();
  private buildings = new Map<number, THREE.Group>();
  private owners = new Map<number, THREE.Mesh>();
  private deedGroups: THREE.Group[] = [];
  private pickables: THREE.Mesh[] = [];
  private dice: THREE.Mesh[] = [];
  private anims: PawnAnim[] = [];
  private cardAnims: {
    mesh: THREE.Mesh;
    from: THREE.Vector3;
    to: THREE.Vector3;
    t0: number;
  }[] = [];
  private diceAnim: {
    t0: number;
    data: {
      from: THREE.Vector3;
      to: THREE.Vector3;
      qEnd: THREE.Quaternion;
      ax: THREE.Vector3;
      spin: number;
    }[];
  } | null = null;
  private hovered: number | null = null;
  private down: {
    x: number;
    y: number;
    t: number;
  } | null = null;
  private lifted: THREE.Mesh | null = null;
  private glow!: THREE.Mesh;
  private glowSpace: number | null = null;
  private pointer: {
    clientX: number;
    clientY: number;
  } | null = null;
  constructor(private spec: RentBoard, private hoverCb: Intent, private pickCb: Intent, private placeLabels: () => void) { }
  mount(table: SharedTable3D) {
    this.table = table;
    const r = table.renderer, sc = this.scene;
    table.scene.add(sc);
    const edge = new THREE.MeshStandardMaterial({ color: "#2a2420", roughness: 0.8 });
    const top = new THREE.MeshStandardMaterial({ map: boardTexture(this.spec, r.capabilities.getMaxAnisotropy()), roughness: 0.78 });
    this.board = new THREE.Mesh(new THREE.BoxGeometry(10, BT, 10), [edge, edge, top, edge, edge, edge]);
    this.board.position.y = BT / 2;
    this.board.castShadow = this.board.receiveShadow = true;
    sc.add(this.board);
    this.glow = new THREE.Mesh(new THREE.PlaneGeometry(1, 1), new THREE.MeshBasicMaterial({ color: "#ffd56a", transparent: true, opacity: 0.32, depthWrite: false, blending: THREE.AdditiveBlending }));
    this.glow.rotation.x = -Math.PI / 2;
    this.glow.visible = false;
    sc.add(this.glow);
    this.addDeck([S / 2 - 330, S / 2 - 330], "#ee8a1e", deckName(this.spec,"opportunity"), "?");
    this.addDeck([S / 2 + 330, S / 2 + 330], "#4ea6d4", deckName(this.spec,"community_fund"), "✉");
    const dieMats = FACES.map((n) => new THREE.MeshPhysicalMaterial({ map: pipTexture(n), roughness: 0.25, clearcoat: 0.8 }));
    for (let k = 0; k < 2; k++) {
      const d = new THREE.Mesh(new RoundedBoxGeometry(0.42, 0.42, 0.42, 4, 0.07), dieMats);
      d.castShadow = true;
      sc.add(d);
      this.dice.push(d);
    }
    this.placeDie(this.dice[0], 5, -0.6, 1.9, 0.3);
    this.placeDie(this.dice[1], 3, 0.1, 2.1, -0.5);
    // These faces are only a resting pose; nothing has been rolled until an observation says so.
    for (const die of this.dice) die.visible = false;
    const fitPoints = [-5.25, 5.25].flatMap(x => [-5.25, 5.25].map(z => new THREE.Vector3(x, .12, z)));
    for (const name of ["free", "top", "seat0", "seat1", "seat2", "seat3"])
      table.registerCamera(name, () => {
        const { pos, tgt } = this.preset(name);
        return { position: pos, target: tgt, fitPoints };
      });
    table.setCamera("seat0");
    for (const [kind, fn] of [["pointermove", this.onPointer], ["pointerdown", this.onDown], ["pointerup", this.onUp], ["pointerleave", this.onLeave], ["pointercancel", this.onLeave]] as const)
      this.resources.listen(this.canvas, kind, fn as EventListener);
    table.controls.addEventListener("change", this.placeLabels);
    this.resources.cleanup(() => table.controls.removeEventListener("change", this.placeLabels));
  }
  seatAnchor(seat: number): {
    x: number;
    y: number;
  } | null {
    const p = new THREE.Vector3(0, Y0, 5.2).applyAxisAngle(UP, seatRotation(seat)).project(this.camera);
    if (p.z > 1)
      return null;
    return { x: ((p.x + 1) / 2) * this.canvas.clientWidth, y: ((1 - p.y) / 2) * this.canvas.clientHeight };
  }
  highlight(space: number | null) {
    this.table.requestRender();
    if (space === this.glowSpace)
      return;
    this.glowSpace = space;
    this.glow.visible = space != null;
    if (space == null)
      return;
    const r = tileRectWorld(space);
    this.glow.position.set(r.x, Y0 + 0.004, r.z);
    this.glow.scale.set(r.w * 0.96, r.d * 0.96, 1);
  }
  update(dto: View<RentState>, events: GameEvent[], _actions: unknown = undefined) {
    const observation = dto.observation;
    if (!observation)
      return;
    const state = observation.data, cursor = dto.event_cursor ?? state.event_cursor;
    const seek = dto.status === "replay" || dto.status === "completed";
    if (this.status === dto.status && this.revision === observation.revision && this.cursor === cursor)
      return;
    this.status = dto.status;
    const receipts = presentationEvents(this.cursor, cursor, events, seek);
    this.reset = seek || !receipts.length;
    this.stopMotion?.();
    this.finishAnimations();
    const previous = this.view;
    const spaces = this.spec.spaces.map(({ index }) => ({ index, owner: state.owner[index], mortgaged: state.mortgaged[index], buildings: state.buildings[index] }));
    this.view = { players: state.players, spaces };
    this.ensurePawns();
    const now = performance.now();
    const trajectories = new Map<number, number[]>();
    for (const event of receipts) {
      const id = event.payload.player;
      if (event.kind === "move" || event.kind === "jail") {
        const path = trajectories.get(id) ?? [this.pawnTile.get(id) ?? state.players[id].position];
        const destination = event.kind === "jail" ? 10 : event.payload.to;
        if (path.at(-1) !== destination)
          path.push(...this.path(path.at(-1)!, destination, event.kind === "jail").slice(1));
        trajectories.set(id, path);
      }
    }
    for (const [player, path] of trajectories)
      if (path.length > 1 && path.at(-1) === state.players[player].position)
        this.anims.push({ player, path, start: now, step: Math.min(.16, 1.2 / (path.length - 1)) });
    const dice = state.last_roll ?? state.start_rolls?.at(-1)?.[1];
    for (const die of this.dice)
      die.visible = !!dice;
    if (dice?.length === 2 && dice.every(n => n >= 1 && n <= 6)) {
      if (!this.reset && this.rolls !== state.roll_count && receipts.some(e => e.kind === "roll" || e.kind === "start_roll"))
        this.rollDice([dice[0], dice[1]]);
      else if (this.reset) {
        this.placeDie(this.dice[0], dice[0], -.6, 1.9, .3);
        this.placeDie(this.dice[1], dice[1], .1, 2.1, -.5);
      }
    }
    this.rolls = state.roll_count;
    this.revision = observation.revision;
    this.cursor = cursor;
    state.players.forEach((p, i) => {
      this.pawns.get(i)!.visible = !p.bankrupt;
      if (!this.anims.some(a => a.player === i))
        this.pawnTile.set(i, p.position);
    });
    this.layoutPawns();
    if (!previous || spaces.some((s, i) => s.owner !== previous.spaces[i].owner || s.mortgaged !== previous.spaces[i].mortgaged || s.buildings !== previous.spaces[i].buildings)
      || state.players.some((p, i) => p.bankrupt !== previous.players[i].bankrupt || String(p.jail_cards) !== String(previous.players[i].jail_cards))) {
      this.rebuildBuildings();
      this.rebuildOwners();
      this.rebuildDeeds();
      this.lifted = null;
    }
    if (this.anims.length || this.cardAnims.length || this.diceAnim)
      this.stopMotion = this.table.animate((time, finish) => {
        if (finish)
          this.finishAnimations();
        else
          this.frame(time);
        this.table.invalidateShadows();
        return !!(this.anims.length || this.cardAnims.length || this.diceAnim);
      });
    this.table.invalidateShadows();
    this.placeLabels();
  }
  setAnimal(seat: number, animal: typeof animals[number]) {
    const old = this.pawns.get(seat);
    if (!old)
      return;
    const pawn = this.pieces.animalPawn(animal, playerColors[seat]);
    pawn.rotation.copy(old.rotation);
    pawn.position.copy(old.position);
    pawn.visible = old.visible;
    this.scene.remove(old);
    disposeTree(old, this.pieces.sharedBuilding);
    this.scene.add(pawn);
    this.pawns.set(seat, pawn);
    this.table.invalidateShadows();
  }
  dispose() {
    this.stopMotion?.();
    this.scene.removeFromParent();
    const handled = new Set<unknown>([this.paper, this.cardGeometry, ...this.cardMats.values(), ...this.pieces.sharedBuilding]);
    this.scene.traverse(object => {
      const mesh = object as THREE.Mesh;
      const release = (item?: {
        dispose(): void;
      }) => {
        if (item && !handled.has(item)) {
          handled.add(item);
          item.dispose();
        }
      };
      release(mesh.geometry);
      for (const mat of [mesh.material].flat())
        if (mat && !handled.has(mat)) {
          release((mat as THREE.MeshStandardMaterial).map ?? undefined);
          release(mat);
        }
    });
    this.resources.dispose();
    this.pieces.dispose();
    this.scene.clear();
    this.cardMats.clear();
  }
  private preset(name: string): {
    pos: THREE.Vector3;
    tgt: THREE.Vector3;
  } {
    if (name === "top")
      return { pos: new THREE.Vector3(0, 23, 0.62), tgt: new THREE.Vector3(0, 0, 0.6) };
    if (name === "free")
      return { pos: new THREE.Vector3(-4, 13.5, 14), tgt: new THREE.Vector3(0, 0, 2.4) };
    const seat = Number(name.slice(4));
    const a = seatRotation(seat);
    return { pos: new THREE.Vector3(0, 10.5, 14.5).applyAxisAngle(UP, a), tgt: new THREE.Vector3(0, 0, 2.2).applyAxisAngle(UP, a) };
  }
  private addDeck(p: [
    number,
    number
  ], color: string, label: string, glyph: string) {
    const side = new THREE.MeshStandardMaterial({ color: "#f4efe2", roughness: 0.9 });
    const top = new THREE.MeshStandardMaterial({ map: deckTexture(color, label, glyph), roughness: 0.7 });
    const m = new THREE.Mesh(new THREE.BoxGeometry(1.75, 0.16, 1.1), [side, side, top, side, side, side]);
    const w = toWorld(p);
    m.position.set(w.x, Y0 + 0.08, w.z);
    m.rotation.y = Math.PI / 4;
    m.castShadow = m.receiveShadow = true;
    this.scene.add(m);
  }
  private ensurePawns() {
    this.view!.players.forEach((_, i) => {
      if (this.pawns.has(i))
        return;
      const pawn = this.pieces.animalPawn(animals[i], playerColors[i]);
      pawn.rotation.y = -Math.PI / 2 + 0.4;
      this.scene.add(pawn);
      this.pawns.set(i, pawn);
      this.pawnTile.set(i, this.view!.players[i].position);
    });
  }
  private path(from: number, to: number, jailed: boolean): number[] {
    const fwd = (to - from + 40) % 40;
    if (jailed || fwd > 12 || fwd === 0)
      return [from, to];
    return Array.from({ length: fwd + 1 }, (_, k) => (from + k) % 40);
  }
  private pawnSpot(tile: number, player: number): THREE.Vector3 {
    const here = [...this.pawnTile.entries()].filter(([p, t]) => t === tile && !this.view!.players[p].bankrupt).map(([p]) => p).sort();
    const idx = Math.max(0, here.indexOf(player));
    const n = Math.max(1, here.length);
    const k = Math.floor(tile / 10);
    const base = tileLocal(tile, 0.6);
    const spread = tile % 10 === 0 ? 0.3 : 0.24;
    const local: [
      number,
      number
    ] = [base[0] + (idx - (n - 1) / 2) * spread * (S / 10), base[1]];
    const w = toWorld(rot(local, k));
    return new THREE.Vector3(w.x, Y0, w.z);
  }
  private layoutPawns() {
    for (const [p, pawn] of this.pawns)
      pawn.position.copy(this.pawnSpot(this.pawnTile.get(p)!, p));
  }
  private finishAnimations() {
    if (this.anims.length) {
      for (const a of this.anims)
        this.pawnTile.set(a.player, a.path[a.path.length - 1]);
      this.anims = [];
      this.layoutPawns();
    }
    if (this.diceAnim)
      this.stepDice(1);
    for (const a of this.cardAnims)
      a.mesh.position.copy(a.to);
    this.cardAnims = [];
  }
  private rebuildBuildings() {
    const v = this.view!;
    for (const [i, g] of this.buildings) {
      if (v.spaces[i].buildings === Number(g.userData.level))
        continue;
      this.scene.remove(g);
      disposeTree(g, this.pieces.sharedBuilding);
      this.buildings.delete(i);
    }
    v.spaces.forEach((s, i) => {
      if (!s.buildings || this.buildings.has(i))
        return;
      const g = new THREE.Group();
      g.userData.level = s.buildings;
      const k = Math.floor(i / 10);
      const loc = tileLocal(i, 0.11);
      const place = (p: [
        number,
        number
      ], hotel: boolean) => {
        const w = toWorld(rot(p, k));
        const m = this.pieces.building(hotel);
        m.position.set(w.x, Y0, w.z);
        m.rotation.y = (-k * Math.PI) / 2;
        g.add(m);
      };
      if (s.buildings === 5)
        place(loc, true);
      else
        for (let h = 0; h < s.buildings; h++)
          place([loc[0] + (h - (s.buildings - 1) / 2) * 0.195 * (S / 10), loc[1]], false);
      this.scene.add(g);
      this.buildings.set(i, g);
    });
  }
  private rebuildOwners() {
    for (const m of this.owners.values()) {
      this.scene.remove(m);
      disposeTree(m, new Set());
    }
    this.owners.clear();
    this.view!.spaces.forEach((s, i) => {
      if (s.owner == null)
        return;
      const m = this.pieces.ownerToken(playerColors[s.owner], s.mortgaged);
      const w = toWorld(rot(tileLocal(i, 0.93), Math.floor(i / 10)));
      m.position.set(w.x, Y0 + 0.013, w.z);
      this.scene.add(m);
      this.owners.set(i, m);
    });
  }
  private rebuildDeeds() {
    const before = new Map<number, {
      pos: THREE.Vector3;
      owner: number;
    }>();
    for (const m of this.pickables) {
      const sp = m.userData.space as number;
      before.set(sp, { pos: m.getWorldPosition(new THREE.Vector3()), owner: m.userData.owner as number });
    }
    this.cardAnims = [];
    for (const g of this.deedGroups)
      this.scene.remove(g);
    this.deedGroups = [];
    this.pickables = [];
    const v = this.view!;
    const card = (key: string, texture: () => THREE.Texture) => {
      let mat = this.cardMats.get(key);
      if (!mat) {
        mat = this.resources.track(new THREE.MeshStandardMaterial({ map: this.resources.track(texture()), roughness: .85 }));
        this.cardMats.set(key, mat);
      }
      const m = new THREE.Mesh(this.cardGeometry, [this.paper, this.paper, mat, this.paper, this.paper, this.paper]);
      m.castShadow = m.receiveShadow = true;
      return m;
    };
    v.players.forEach((p, pi) => {
      if (p.bankrupt)
        return;
      const seat = new THREE.Group();
      seat.rotation.y = seatRotation(pi);
      const mine = v.spaces.filter((s) => s.owner === pi).map((s) => s.index);
      const groups: {
        key: string;
        items: (number | string)[];
      }[] = [];
      for (const i of mine) {
        const b = this.spec.spaces[i];
        const key = b.group ?? b.kind;
        let g = groups.find((x) => x.key === key);
        if (!g)
          groups.push((g = { key, items: [] }));
        g.items.push(i);
      }
      for (const deck of p.jail_cards)
        groups.push({ key: deck, items: [deck] });
      const gap = 1.45;
      const x0 = (-(groups.length - 1) * gap) / 2;
      groups.forEach((gr, gi) => gr.items.forEach((it, k) => {
        const m = typeof it === "string" ? card(it, () => jailCardTexture(this.spec,it)) : card(it + ":" + v.spaces[it].mortgaged, () => deedTexture(this.spec, it, v.spaces[it].mortgaged));
        m.position.set(x0 + gi * gap, 0.006 + k * 0.013, 6.25 + k * 0.58);
        m.rotation.y = ((gi * 7 + k * 3) % 5) * 0.01 - 0.02;
        if (typeof it === "number") {
          m.userData.space = it;
          m.userData.owner = pi;
          this.pickables.push(m);
        }
        seat.add(m);
        const prev = typeof it === "number" ? before.get(it) : undefined;
        if (!this.reset && prev && prev.owner !== pi) {
          seat.updateMatrixWorld(true);
          const from = seat.worldToLocal(prev.pos.clone());
          this.cardAnims.push({ mesh: m, from, to: m.position.clone(), t0: performance.now() });
          m.position.copy(from);
        }
      }));
      this.scene.add(seat);
      this.deedGroups.push(seat);
    });
  }
  private faceQuat(v: number): THREE.Quaternion {
    const e: Record<number, [
      number,
      number,
      number
    ]> = {
      2: [0, 0, 0], 5: [Math.PI, 0, 0], 1: [0, 0, Math.PI / 2], 6: [0, 0, -Math.PI / 2], 3: [-Math.PI / 2, 0, 0], 4: [Math.PI / 2, 0, 0],
    };
    return new THREE.Quaternion().setFromEuler(new THREE.Euler(...e[v]));
  }
  private placeDie(d: THREE.Mesh, v: number, x: number, z: number, yaw: number) {
    d.position.set(x, Y0 + 0.21, z);
    d.quaternion.copy(new THREE.Quaternion().setFromAxisAngle(UP, yaw).multiply(this.faceQuat(v)));
  }
  private rollDice(values: [
    number,
    number
  ]) {
    this.diceAnim = {
      t0: performance.now(),
      data: this.dice.map((_, k) => ({
        from: new THREE.Vector3(3.4 + k * 0.4, 1.6, 3.6 - k * 0.3),
        to: new THREE.Vector3(-0.7 + k * 0.75 + (Math.random() - 0.5) * 0.3, Y0 + 0.21, 1.6 + (Math.random() - 0.5) * 0.8),
        qEnd: new THREE.Quaternion().setFromAxisAngle(UP, Math.random() * 6.28).multiply(this.faceQuat(values[k])),
        ax: new THREE.Vector3(Math.random() - 0.5, Math.random() - 0.5, Math.random() - 0.5).normalize(),
        spin: 14 + Math.random() * 8,
      })),
    };
  }
  private stepDice(t: number) {
    const a = this.diceAnim!;
    a.data.forEach((o, k) => {
      const d = this.dice[k];
      d.position.lerpVectors(o.from, o.to, 1 - Math.pow(1 - t, 3));
      d.position.y = o.to.y + Math.abs(Math.cos(t * Math.PI * 2.5)) * 1.4 * Math.pow(1 - t, 2);
      d.quaternion.copy(o.qEnd).premultiply(new THREE.Quaternion().setFromAxisAngle(o.ax, o.spin * (1 - t) * (1 - t)));
    });
    if (t >= 1)
      this.diceAnim = null;
  }
  private frame(now: number) {
    if (this.diceAnim)
      this.stepDice(Math.min(1, (now - this.diceAnim.t0) / 1300));
    if (this.anims.length) {
      const done: PawnAnim[] = [];
      for (const a of this.anims) {
        const pawn = this.pawns.get(a.player)!;
        const pos = (now - a.start) / 1000 / a.step;
        const seg = Math.min(Math.floor(pos), a.path.length - 1);
        if (seg >= a.path.length - 1) {
          done.push(a);
          continue;
        }
        const f = pos - seg;
        const p0 = this.pawnSpot(a.path[seg], a.player);
        const p1 = this.pawnSpot(a.path[seg + 1], a.player);
        pawn.position.lerpVectors(p0, p1, f);
        pawn.position.y = Y0 + Math.sin(Math.PI * f) * 0.35;
      }
      if (done.length) {
        for (const a of done)
          this.pawnTile.set(a.player, a.path[a.path.length - 1]);
        this.anims = this.anims.filter((a) => !done.includes(a));
        if (!this.anims.length)
          this.layoutPawns();
      }
    }
    if (this.cardAnims.length) {
      for (const a of this.cardAnims) {
        const t = Math.min(1, (now - a.t0) / 900);
        const e = t < 0.5 ? 2 * t * t : 1 - Math.pow(-2 * t + 2, 2) / 2;
        a.mesh.position.lerpVectors(a.from, a.to, e);
        a.mesh.position.y += Math.sin(Math.PI * e) * 1.2;
      }
      this.cardAnims = this.cardAnims.filter((a) => now - a.t0 < 900);
    }
  }
  private onPointer = (e: {
    clientX: number;
    clientY: number;
  }) => {
    this.pointer = { clientX: e.clientX, clientY: e.clientY };
    const c = this.table.pick(e, this.pickables);
    if (this.lifted && (!c || c.object !== this.lifted)) {
      this.lifted.position.y -= 0.08;
      this.lifted = null;
    }
    if (c) {
      const obj = c.object as THREE.Mesh;
      if (!this.lifted) {
        this.lifted = obj;
        obj.position.y += 0.08;
      }
      this.highlight(obj.userData.space as number);
      this.hovered = obj.userData.space as number;
      this.hoverCb?.(this.hovered, e.clientX, e.clientY);
      return;
    }
    this.highlight(null);
    const hit = this.table.pick(e, [this.board]);
    this.hovered = null;
    if (hit && hit.uv && hit.face && hit.face.normal.y > 0.5) {
      const i = uvToIndex(hit.uv.x, hit.uv.y);
      if (i >= 0)
        this.hovered = i;
    }
    this.hoverCb?.(this.hovered, e.clientX, e.clientY);
  };
  private onDown = (e: PointerEvent) => {
    this.down = { x: e.clientX, y: e.clientY, t: performance.now() };
  };
  private onUp = (e: PointerEvent) => {
    const d = this.down;
    this.down = null;
    if (!d || Math.hypot(e.clientX - d.x, e.clientY - d.y) > 6 || performance.now() - d.t > 600)
      return;
    this.onPointer(e);
    this.pickCb?.(this.hovered, e.clientX, e.clientY);
  };
  private onLeave = () => {
    this.down = null;
    if (this.lifted) {
      this.lifted.position.y -= .08;
      this.lifted = null;
    }
    this.pointer = null;
    this.highlight(null);
    this.hoverCb?.(null, 0, 0);
  };
}
