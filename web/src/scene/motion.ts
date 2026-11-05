import { Matrix4, Quaternion } from "three";
export function dieSupportHeight(q: Quaternion, halfSize = 0.36) {
  const m = new Matrix4().makeRotationFromQuaternion(q).elements;
  return halfSize * (Math.abs(m[1]) + Math.abs(m[5]) + Math.abs(m[9]));
}
export function trayRimClearance(x: number, z: number) {
  const dx = x - 4.3,
    dz = z + 3.5;
  const localX = dx * Math.cos(-0.08) - dz * Math.sin(-0.08),
    localZ = dx * Math.sin(-0.08) + dz * Math.cos(-0.08);
  const distance = Math.max(Math.abs(localX), Math.abs(localZ));
  const band = Math.max(0, 1 - Math.abs(distance - 2.25) / 0.85);
  return 0.435 + 0.66 * band * band * (3 - 2 * band);
}
