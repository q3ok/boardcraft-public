export const S = 2048;
export const CR = (S * 1.6) / 12.2;
export const W = S / 12.2;
export const BOARD_SIZE = 10;
export type P2 = [
  number,
  number
];
export function rot(p: P2, k: number): P2 {
  let x = p[0] - S / 2;
  let y = p[1] - S / 2;
  for (let r = 0; r < k; r++)
    [x, y] = [-y, x];
  return [x + S / 2, y + S / 2];
}
export function tileLocal(i: number, frac = 0.5): P2 {
  const j = i % 10;
  if (j === 0)
    return [S - CR / 2, S - CR / 2];
  return [S - CR - (j - 0.5) * W, S - CR + CR * frac];
}
export function toWorld(p: P2): {
  x: number;
  z: number;
} {
  return { x: (p[0] / S - 0.5) * BOARD_SIZE, z: (p[1] / S - 0.5) * BOARD_SIZE };
}
export function tileWorld(i: number, frac = 0.5): {
  x: number;
  z: number;
} {
  return toWorld(rot(tileLocal(i, frac), Math.floor(i / 10)));
}
export function uvToIndex(u: number, v: number): number {
  const p: P2 = [u * S, (1 - v) * S];
  for (let k = 0; k < 4; k++) {
    const [x, y] = rot(p, (4 - k) % 4);
    if (y >= S - CR) {
      if (x >= S - CR)
        return k * 10;
      if (x >= CR)
        return k * 10 + Math.floor((S - CR - x) / W) + 1;
    }
  }
  return -1;
}
export function seatRotation(seat: number): number {
  return (-seat * Math.PI) / 2;
}
export function tileRectWorld(i: number): {
  x: number;
  z: number;
  w: number;
  d: number;
} {
  const c = tileWorld(i);
  const corner = i % 10 === 0;
  const along = ((corner ? CR : W) / S) * BOARD_SIZE;
  const across = (CR / S) * BOARD_SIZE;
  const side = Math.floor(i / 10) % 2 === 1;
  return { x: c.x, z: c.z, w: side ? across : along, d: side ? along : across };
}
