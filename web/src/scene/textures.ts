import * as THREE from "three";
export function canvasTexture(canvas: HTMLCanvasElement, anisotropy = 8) {
  const t = new THREE.CanvasTexture(canvas);
  t.colorSpace = THREE.SRGBColorSpace;
  t.anisotropy = anisotropy;
  return t;
}
export function woodCanvas() {
  const c = document.createElement("canvas");
  c.width = c.height = 1024;
  const g = c.getContext("2d")!;
  g.fillStyle = "#664524";
  g.fillRect(0, 0, 1024, 1024);
  for (let y = 0; y < 1024; y++) {
    const grain = Math.sin(y * 0.11 + Math.sin(y * 0.032) * 2) * 0.5 + 0.5;
    g.fillStyle = `rgba(${49 + grain * 40},${27 + grain * 25},${13 + grain * 12},.44)`;
    g.fillRect(0, y, 1024, 1);
  }
  for (let k = 0; k < 100; k++) {
    g.strokeStyle = `rgba(39,17,5,${0.05 + (k % 7) * 0.011})`;
    g.lineWidth = 1;
    g.beginPath();
    for (let x = 0; x <= 1024; x += 16) {
      const y =
        k * 10 +
        Math.sin(x * 0.007 + k) * 2 +
        Math.sin(x * 0.015 + k * 0.5) * 1.3;
      if (x === 0) g.moveTo(x, y);
      else g.lineTo(x, y);
    }
    g.stroke();
  }
  for (let y = 0; y < 1024; y += 128) {
    const line = g.createLinearGradient(0, y, 0, y + 3);
    line.addColorStop(0, "#412a19");
    line.addColorStop(0.25, "#57391e");
    line.addColorStop(1, "#8a633a");
    g.fillStyle = line;
    g.fillRect(0, y, 1024, 3);
  }
  return c;
}
export function feltCanvas() {
  const c = document.createElement("canvas");
  c.width = c.height = 128;
  const g = c.getContext("2d")!;
  const pixels = g.createImageData(128, 128);
  let seed = 314159;
  for (let i = 0; i < pixels.data.length; i += 4) {
    seed = (Math.imul(seed, 1664525) + 1013904223) | 0;
    const n = ((seed >>> 24) / 255) * 12 - 6;
    pixels.data[i] = 40 + n;
    pixels.data[i + 1] = 80 + n;
    pixels.data[i + 2] = 57 + n;
    pixels.data[i + 3] = 255;
  }
  g.putImageData(pixels, 0, 0);
  return c;
}
