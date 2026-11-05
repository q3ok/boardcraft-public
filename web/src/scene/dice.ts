import * as THREE from "three";
import { RoundedBoxGeometry } from "three/addons/geometries/RoundedBoxGeometry.js";
import { ResourceOwner } from "./runtime";
import { COLORS, type DieId } from "../games/dice-circuit/types";
export const FACE_NORMALS: Record<number, [number, number, number]> = {
  1: [0, 1, 0],
  6: [0, -1, 0],
  2: [1, 0, 0],
  5: [-1, 0, 0],
  3: [0, 0, 1],
  4: [0, 0, -1],
};
export function faceQuaternion(value: number) {
  return new THREE.Quaternion().setFromUnitVectors(
    new THREE.Vector3(...FACE_NORMALS[value]),
    new THREE.Vector3(0, 1, 0),
  );
}
const PIPS: Record<number, [number, number][]> = {
  1: [[0, 0]],
  2: [
    [-1, 1],
    [1, -1],
  ],
  3: [
    [-1, 1],
    [0, 0],
    [1, -1],
  ],
  4: [
    [-1, -1],
    [-1, 1],
    [1, -1],
    [1, 1],
  ],
  5: [
    [-1, -1],
    [-1, 1],
    [0, 0],
    [1, -1],
    [1, 1],
  ],
  6: [
    [-1, -1],
    [-1, 0],
    [-1, 1],
    [1, -1],
    [1, 0],
    [1, 1],
  ],
};
export class DiceFactory {
  private geometry;
  private pipGeometry;
  private dishGeometry;
  private materials = new Map<DieId, THREE.MeshPhysicalMaterial>();
  constructor(private owner: ResourceOwner) {
    this.geometry = owner.track(
      new RoundedBoxGeometry(0.72, 0.72, 0.72, 4, 0.1),
    );
    this.pipGeometry = owner.track(new THREE.CircleGeometry(0.048, 16));
    this.dishGeometry = owner.track(new THREE.CircleGeometry(0.062, 16));
  }
  create(id: DieId) {
    const group = new THREE.Group();
    group.userData.dieId = id;
    const material = this.owner.track(
      new THREE.MeshPhysicalMaterial({
        color: COLORS[id],
        roughness: 0.26,
        clearcoat: 0.8,
        clearcoatRoughness: 0.18,
        envMapIntensity: 1,
      }),
    );
    this.materials.set(id, material);
    const body = new THREE.Mesh(this.geometry, material);
    body.castShadow = body.receiveShadow = true;
    group.add(body);
    const pip = this.owner.track(
      new THREE.MeshStandardMaterial({
        color: id === "white" || id === "yellow" ? "#44311a" : "#f7f4e7",
        roughness: 0.46,
      }),
    );
    const dish = this.owner.track(
      new THREE.MeshStandardMaterial({
        color: new THREE.Color(COLORS[id]).multiplyScalar(0.48),
        roughness: 0.5,
      }),
    );
    for (let n = 1; n <= 6; n++) {
      const face = new THREE.Group();
      face.quaternion.setFromUnitVectors(
        new THREE.Vector3(0, 0, 1),
        new THREE.Vector3(...FACE_NORMALS[n]),
      );
      for (const [x, y] of PIPS[n]) {
        const d = new THREE.Mesh(this.dishGeometry, dish);
        d.position.set(x * 0.15, y * 0.15, 0.3606);
        face.add(d);
        const p = new THREE.Mesh(this.pipGeometry, pip);
        p.position.set(x * 0.15, y * 0.15, 0.3612);
        face.add(p);
      }
      group.add(face);
    }
    return group;
  }
  highlight(id: DieId, selected: boolean, legal: boolean) {
    const m = this.materials.get(id)!;
    m.emissive.set(selected ? "#cca044" : legal ? "#214a31" : "#000000");
    m.emissiveIntensity = selected ? 0.4 : legal ? 0.13 : 0;
  }
}
