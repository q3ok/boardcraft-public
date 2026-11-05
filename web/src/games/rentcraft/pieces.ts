import * as THREE from "three";
export const animals = ["elephant", "giraffe", "lion", "rhinoceros", "zebra", "hippopotamus", "crocodile", "gorilla"] as const;
export type Animal = typeof animals[number];
export const playerColors = ["#e53935", "#1e88e5", "#43a047", "#fdd835"];
export function createPieces() {
  const metal = new THREE.MeshPhysicalMaterial({ color: "#cfd3d8", metalness: 1, roughness: 0.28, clearcoat: 0.4 });
  function M(geo: THREE.BufferGeometry, x: number, y: number, z: number, rx = 0, ry = 0, rz = 0, sx = 1, sy = 1, sz = 1) {
    const m = new THREE.Mesh(geo, metal);
    m.position.set(x, y, z);
    m.rotation.set(rx, ry, rz);
    m.scale.set(sx, sy, sz);
    m.castShadow = true;
    return m;
  }
  const sph = (r: number) => new THREE.SphereGeometry(r, 28, 18);
  const cyl = (a: number, b: number, h: number) => new THREE.CylinderGeometry(a, b, h, 20);
  const cone = (r: number, h: number) => new THREE.ConeGeometry(r, h, 20);
  const cap = (r: number, l: number) => new THREE.CapsuleGeometry(r, l, 8, 16);
  const tube = (pts: [
    number,
    number,
    number
  ][], r: number) => new THREE.TubeGeometry(new THREE.CatmullRomCurve3(pts.map((p) => new THREE.Vector3(...p))), 20, r, 10, false);
  function legs(g: THREE.Group, r: number, h: number, dx: number, dz: number) {
    for (const [x, z] of [[-dx, dz], [dx, dz], [-dx, -dz], [dx, -dz]])
      g.add(M(cyl(r, r * 0.9, h), x, h / 2, z));
  }
  const BUILDERS: Record<Animal, (g: THREE.Group) => void> = {
    elephant: (g) => {
      legs(g, 0.042, 0.13, 0.08, 0.06);
      g.add(M(sph(0.13), 0, 0.22, 0, 0, 0, 0, 1.3, 1, 1));
      g.add(M(sph(0.09), 0.17, 0.29, 0));
      g.add(M(sph(0.08), 0.15, 0.29, 0.09, 0, 0, 0, 0.35, 1.1, 1));
      g.add(M(sph(0.08), 0.15, 0.29, -0.09, 0, 0, 0, 0.35, 1.1, 1));
      g.add(M(tube([[0.24, 0.27, 0], [0.29, 0.18, 0], [0.29, 0.08, 0], [0.32, 0.04, 0]], 0.026), 0, 0, 0));
      g.add(M(cone(0.014, 0.08), 0.24, 0.22, 0.035, 0, 0, -2.2));
      g.add(M(cone(0.014, 0.08), 0.24, 0.22, -0.035, 0, 0, -2.2));
    },
    giraffe: (g) => {
      legs(g, 0.018, 0.2, 0.06, 0.04);
      g.add(M(sph(0.085), 0, 0.24, 0, 0, 0, -0.15, 1.45, 0.85, 0.85));
      g.add(M(tube([[0.09, 0.26, 0], [0.12, 0.38, 0], [0.14, 0.5, 0]], 0.03), 0, 0, 0));
      g.add(M(cap(0.032, 0.06), 0.18, 0.53, 0, 0, 0, Math.PI / 2 + 0.25));
      g.add(M(cyl(0.007, 0.007, 0.04), 0.14, 0.58, 0.015));
      g.add(M(cyl(0.007, 0.007, 0.04), 0.14, 0.58, -0.015));
    },
    lion: (g) => {
      legs(g, 0.026, 0.12, 0.08, 0.045);
      g.add(M(cap(0.07, 0.17), 0, 0.17, 0, 0, 0, Math.PI / 2));
      g.add(M(sph(0.105), 0.13, 0.25, 0, 0, 0, 0, 0.8, 1, 1));
      g.add(M(sph(0.065), 0.19, 0.24, 0));
      g.add(M(new THREE.BoxGeometry(0.05, 0.045, 0.05), 0.25, 0.22, 0));
      g.add(M(tube([[-0.16, 0.18, 0], [-0.23, 0.14, 0], [-0.25, 0.22, 0], [-0.27, 0.27, 0]], 0.01), 0, 0, 0));
      g.add(M(sph(0.025), -0.27, 0.28, 0));
    },
    rhinoceros: (g) => {
      legs(g, 0.045, 0.1, 0.09, 0.06);
      g.add(M(sph(0.13), 0, 0.19, 0, 0, 0, 0, 1.45, 0.95, 1));
      g.add(M(sph(0.08), 0.2, 0.17, 0, 0, 0, -0.3, 1.3, 0.85, 0.9));
      g.add(M(cone(0.028, 0.11), 0.29, 0.24, 0, 0, 0, -0.35));
      g.add(M(cone(0.018, 0.06), 0.23, 0.25, 0, 0, 0, -0.2));
    },
    zebra: (g) => {
      legs(g, 0.022, 0.17, 0.08, 0.04);
      g.add(M(cap(0.065, 0.16), 0, 0.23, 0, 0, 0, Math.PI / 2));
      g.add(M(tube([[0.1, 0.26, 0], [0.15, 0.34, 0], [0.18, 0.38, 0]], 0.032), 0, 0, 0));
      g.add(M(cap(0.035, 0.08), 0.22, 0.36, 0, 0, 0, 2.2));
      g.add(M(new THREE.BoxGeometry(0.09, 0.03, 0.012), 0.14, 0.36, 0, 0, 0, -0.9));
      g.add(M(cone(0.015, 0.05), 0.19, 0.43, 0.02));
      g.add(M(cone(0.015, 0.05), 0.19, 0.43, -0.02));
      g.add(M(tube([[-0.13, 0.25, 0], [-0.17, 0.18, 0]], 0.008), 0, 0, 0));
    },
    hippopotamus: (g) => {
      legs(g, 0.04, 0.07, 0.09, 0.06);
      g.add(M(sph(0.14), 0, 0.17, 0, 0, 0, 0, 1.45, 0.85, 1));
      g.add(M(sph(0.09), 0.2, 0.17, 0, 0, 0, 0, 1.2, 0.8, 1.05));
      g.add(M(sph(0.025), 0.17, 0.25, 0.05));
      g.add(M(sph(0.025), 0.17, 0.25, -0.05));
    },
    crocodile: (g) => {
      g.add(M(cap(0.055, 0.32), 0, 0.07, 0, 0, 0, Math.PI / 2, 1, 1, 0.8));
      g.add(M(new THREE.BoxGeometry(0.16, 0.04, 0.07), 0.26, 0.06, 0));
      g.add(M(cone(0.035, 0.2), -0.28, 0.06, 0, 0, 0, Math.PI / 2, 1, 1, 0.7));
      for (const [x, z] of [[0.1, 0.06], [0.1, -0.06], [-0.1, 0.06], [-0.1, -0.06]])
        g.add(M(cyl(0.015, 0.015, 0.05), x, 0.025, z * 1.3));
      for (let k = 0; k < 4; k++)
        g.add(M(cone(0.015, 0.03), -0.12 + k * 0.08, 0.13, 0));
    },
    gorilla: (g) => {
      g.add(M(sph(0.12), 0, 0.2, 0, 0, 0, 0, 1, 1.1, 1.05));
      g.add(M(sph(0.07), 0.06, 0.37, 0));
      g.add(M(cap(0.035, 0.14), 0.05, 0.16, 0.12, 0.25, 0, 0));
      g.add(M(cap(0.035, 0.14), 0.05, 0.16, -0.12, -0.25, 0, 0));
      g.add(M(sph(0.05), 0.0, 0.06, 0.06));
      g.add(M(sph(0.05), 0.0, 0.06, -0.06));
    },
  };
  function animalPawn(kind: Animal, color: string): THREE.Group {
    const g = new THREE.Group();
    const base = new THREE.Mesh(cyl(0.19, 0.21, 0.05), new THREE.MeshPhysicalMaterial({ color, roughness: 0.3, clearcoat: 1 }));
    base.position.y = 0.025;
    base.castShadow = true;
    g.add(base);
    const fig = new THREE.Group();
    BUILDERS[kind](fig);
    fig.position.y = 0.05;
    g.add(fig);
    g.scale.setScalar(1.4);
    return g;
  }
  const houseMat = new THREE.MeshStandardMaterial({ color: "#1d8a3e", roughness: 0.4 });
  const hotelMat = new THREE.MeshStandardMaterial({ color: "#c8161d", roughness: 0.4 });
  const sharedBuilding = new Set<unknown>([houseMat, hotelMat, metal]);
  function building(hotel: boolean): THREE.Group {
    const g = new THREE.Group();
    const mat = hotel ? hotelMat : houseMat;
    const w = hotel ? 0.4 : 0.17;
    const d = hotel ? 0.22 : 0.17;
    const h = hotel ? 0.2 : 0.14;
    const body = new THREE.Mesh(new THREE.BoxGeometry(w, h, d), mat);
    body.position.y = h / 2;
    body.castShadow = true;
    g.add(body);
    const sh = new THREE.Shape();
    sh.moveTo(-d / 2 - 0.008, 0);
    sh.lineTo(d / 2 + 0.008, 0);
    sh.lineTo(0, hotel ? 0.09 : 0.08);
    sh.closePath();
    const roof = new THREE.Mesh(new THREE.ExtrudeGeometry(sh, { depth: w + 0.01, bevelEnabled: false }), mat);
    roof.rotation.y = Math.PI / 2;
    roof.position.set(-(w + 0.01) / 2, h, 0);
    roof.castShadow = true;
    g.add(roof);
    return g;
  }
  function ownerToken(color: string, mortgaged: boolean): THREE.Mesh {
    const c = new THREE.Color(color);
    if (mortgaged)
      c.multiplyScalar(0.35);
    const m = new THREE.Mesh(new THREE.CylinderGeometry(0.075, 0.075, 0.025, 24), new THREE.MeshStandardMaterial({ color: c, roughness: 0.4, metalness: 0.2 }));
    m.castShadow = true;
    return m;
  }
  return { animalPawn, building, ownerToken, sharedBuilding, dispose() {
      metal.dispose();
      houseMat.dispose();
      hotelMat.dispose();
    } };
}
