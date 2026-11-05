import * as THREE from "three";
import { OrbitControls } from "three/addons/controls/OrbitControls.js";
import { RoomEnvironment } from "three/addons/environments/RoomEnvironment.js";
import { ResourceOwner, RenderScheduler, pickObject } from "./runtime";
import { canvasTexture, woodCanvas } from "./textures";
export interface CameraPreset {
  target: THREE.Vector3;
  position: THREE.Vector3;
  orthographic?: boolean;
  width?: number;
  height?: number;
  fitPoints?: THREE.Vector3[];
  /** Orthographic only: the player may zoom (wheel, pinch) and pan (drag); choosing the preset again restores the framing. */
  adjustable?: boolean;
}
/** Fit a perspective preset inside the uncovered part of the canvas before applying its view offset. */
export function fitPerspective(camera:THREE.PerspectiveCamera, points:THREE.Vector3[], widthFraction:number, heightFraction:number) {
  camera.clearViewOffset();
  camera.zoom=1;
  camera.updateProjectionMatrix();
  camera.updateMatrixWorld();
  let scale=1;
  for(const point of points){
    const projected=point.clone().project(camera);
    scale=Math.min(scale,widthFraction/Math.abs(projected.x),heightFraction/Math.abs(projected.y));
  }
  camera.zoom=scale;
  camera.updateProjectionMatrix();
}
export interface TableAppearance {
  background: string;
  fog: [number, number];
  exposure: number;
  lighting(scene: THREE.Scene): THREE.LightShadow;
  wood(): THREE.Texture;
  size: number;
  roughness: number;
  orbit: [number, number, number];
}
export interface GameScene {
  mount(table: SharedTable3D): void;
  update(view: unknown, events: unknown[], actions: unknown[]): void;
  dispose(): void;
}
export class SharedTable3D {
  readonly renderer: THREE.WebGLRenderer;
  readonly scene = new THREE.Scene();
  readonly raycaster = new THREE.Raycaster();
  readonly resources = new ResourceOwner();
  readonly scheduler = new RenderScheduler();
  readonly controls: OrbitControls;
  camera: THREE.PerspectiveCamera | THREE.OrthographicCamera =
    new THREE.PerspectiveCamera(40, 1, 0.1, 140);
  readonly reduced = matchMedia("(prefers-reduced-motion: reduce)");
  private cancelFlight?: () => void;
  private animations = new Set<(time: number, finish: boolean) => boolean>();
  private presets = new Map<string, () => CameraPreset>();
  private preset = "table";
  private orbiting=false;
  private adjusted=false; // the player zoomed or panned an adjustable orthographic preset
  private disposed = false;
  private inset = { top: 90, right: 24, bottom: 180, left: 24 };
  private shadow: THREE.LightShadow;
  constructor(
    readonly canvas: HTMLCanvasElement,
    readonly container: HTMLElement,
    onFailure: (message: string) => void = () => {},
    appearance?: TableAppearance,
  ) {
    this.renderer = new THREE.WebGLRenderer({
      canvas,
      antialias: true,
      powerPreference: "low-power",
    });
    const r = this.renderer;
    r.setPixelRatio(Math.min(devicePixelRatio || 1, 2));
    r.shadowMap.enabled = true;
    r.shadowMap.type = THREE.PCFSoftShadowMap;
    r.shadowMap.autoUpdate = false;
    r.toneMapping = THREE.ACESFilmicToneMapping;
    r.toneMappingExposure = appearance?.exposure ?? 1.12;
    r.outputColorSpace = THREE.SRGBColorSpace;
    this.scene.background = new THREE.Color(appearance?.background ?? "#27190f");
    this.scene.fog = new THREE.Fog(appearance?.background ?? "#27190f", ...(appearance?.fog ?? [40,95] as const));
    if (appearance) {
      this.shadow = appearance.lighting(this.scene);
    } else {
      const room = new RoomEnvironment();
      const generator = new THREE.PMREMGenerator(r);
      const environment = this.resources.track(generator.fromScene(room, 0.04));
      this.scene.environment = environment.texture;
      room.dispose();
      generator.dispose();
      this.scene.add(new THREE.HemisphereLight("#fff2df", "#624124", 0.7));
      const key = new THREE.DirectionalLight("#ffdfb6", 2.8);
      key.position.set(-8, 17, 8);
      key.castShadow = true;
      key.shadow.mapSize.set(2048, 2048);
      Object.assign(key.shadow.camera, {
        left: -17,
        right: 17,
        top: 17,
        bottom: -17,
        near: 1,
        far: 45,
      });
      key.shadow.bias = -0.0003;
      key.shadow.normalBias = 0.015;
      key.shadow.radius = 3;
      this.shadow = key.shadow;
      this.scene.add(key);
      const fill = new THREE.DirectionalLight("#cfddff", 0.7);
      fill.position.set(10, 8, -7);
      this.scene.add(fill);
    }
    const wood = this.resources.track(
      appearance?.wood() ?? canvasTexture(woodCanvas(), r.capabilities.getMaxAnisotropy()),
    );
    wood.wrapS = wood.wrapT = THREE.RepeatWrapping;
    if (!appearance) wood.repeat.set(5, 5);
    const table = new THREE.Mesh(
      this.resources.track(new THREE.PlaneGeometry(appearance?.size ?? 80, appearance?.size ?? 80)),
      this.resources.track(
        new THREE.MeshStandardMaterial({
          map: wood,
          roughness: appearance?.roughness ?? 0.48,
          metalness: appearance ? 0 : 0.02,
        }),
      ),
    );
    table.rotation.x = -Math.PI / 2;
    table.receiveShadow = true;
    this.scene.add(table);
    this.camera.position.set(0, 16, 14);
    this.controls = new OrbitControls(this.camera, canvas);
    this.controls.enableDamping = false;
    this.controls.maxPolarAngle = appearance?.orbit[0] ?? 1.27;
    this.controls.minDistance = appearance?.orbit[1] ?? 5;
    this.controls.maxDistance = appearance?.orbit[2] ?? 42;
    this.controls.enablePan = true;
    this.controls.minZoom = 1; // an adjustable orthographic preset starts fitted: zoom only in
    this.controls.maxZoom = 5;
    this.controls.addEventListener("change", this.requestRender);
    this.controls.addEventListener("start", this.onControlStart);
    const resize = new ResizeObserver(() => this.resize());
    resize.observe(container);
    this.resources.cleanup(() => resize.disconnect());
    this.resources.listen(document, "visibilitychange", () => {
      if (document.hidden) {
        this.finishAnimations();
        this.scheduler.pause();
      } else this.requestRender();
    });
    this.resources.listen(this.reduced, "change", () => {
      if (this.reduced.matches) this.finishAnimations();
      this.requestRender();
    });
    this.resources.listen(canvas, "webglcontextlost", (e) => {
      e.preventDefault();
      this.scheduler.pause();
      onFailure(
        "The 3D view lost its graphics connection. Reload this page to return to your table.",
      );
    });
    this.resources.listen(canvas, "webglcontextrestored", () => {
      this.renderer.shadowMap.needsUpdate = true;
      this.requestRender();
    });
    this.scheduler.setFrame((t) => {
      for (const a of [...this.animations])
        if (!a(t, false)) this.animations.delete(a);
      this.renderer.render(this.scene, this.camera);
      return this.animations.size > 0;
    });
    this.resize();
  }
  private onControlStart = () => {
    this.cancelFlight?.();
    if (this.camera instanceof THREE.OrthographicCamera) {
      this.adjusted = true;
      return;
    }
    this.orbiting=true;
  };
  registerCamera(name: string, preset: () => CameraPreset) {
    this.presets.set(name, preset);
  }
  setInsets(inset: Partial<typeof this.inset>) {
    Object.assign(this.inset, inset);
    this.resize();
  }
  setCamera(name: string, transitionMs = 0) {
    if (!this.presets.has(name)) return;
    this.cancelFlight?.();
    const from = this.camera.position.clone(), target = this.controls.target.clone();
    this.preset = name;
    this.orbiting=false;
    this.adjusted=false;
    this.applyCamera();
    if (transitionMs > 0) {
      const end = this.camera.position.clone(), aim = this.controls.target.clone(), start = performance.now();
      this.cancelFlight = this.animate((now, finish) => {
        const t = finish ? 1 : Math.min(1, Math.max(0,(now-start)/transitionMs));
        const ease=t<0.5 ? 4*t*t*t : 1-Math.pow(-2*t+2,3)/2;
        this.camera.position.lerpVectors(from,end,ease);
        this.camera.position.y += Math.sin(Math.PI*ease)*4;
        this.controls.target.lerpVectors(target,aim,ease);
        this.controls.update();
        return t<1;
      });
    }
    this.requestRender();
  }
  private applyCamera() {
    const get = this.presets.get(this.preset);
    if (!get) return;
    const p = get();
    const w = Math.max(1, this.container.clientWidth),
      h = Math.max(1, this.container.clientHeight);
    const safeW = Math.max(100, w - this.inset.left - this.inset.right),
      safeH = Math.max(100, h - this.inset.top - this.inset.bottom);
    const aspect = w / h;
    // a resize (or new insets) re-frames the preset but keeps what the player did: the zoom and the pan
    const kept =
      this.adjusted && p.adjustable && this.camera instanceof THREE.OrthographicCamera
        ? { zoom: this.camera.zoom, pan: this.controls.target.clone().sub(p.target) }
        : null;
    if (p.orthographic) {
      if (!(this.camera instanceof THREE.OrthographicCamera))
        this.camera = new THREE.OrthographicCamera(-1, 1, 1, -1, 0.1, 140);
      const span = Math.max(
        ((p.height ?? 10) * h) / safeH,
        ((p.width ?? 7) * w) / safeW / aspect,
      );
      this.camera.left = (-span * aspect) / 2;
      this.camera.right = (span * aspect) / 2;
      this.camera.top = span / 2;
      this.camera.bottom = -span / 2;
      this.camera.zoom = 1;
      this.controls.enableRotate = false;
      this.controls.enablePan = !!p.adjustable;
      this.controls.enableZoom = !!p.adjustable;
      // dragging pans the paper (rotating is off in these views)
      this.controls.mouseButtons.LEFT = p.adjustable ? THREE.MOUSE.PAN : THREE.MOUSE.ROTATE;
      this.controls.touches.ONE = p.adjustable ? THREE.TOUCH.PAN : THREE.TOUCH.ROTATE;
    } else {
      this.controls.mouseButtons.LEFT = THREE.MOUSE.ROTATE;
      this.controls.touches.ONE = THREE.TOUCH.ROTATE;
      if (!(this.camera instanceof THREE.PerspectiveCamera))
        this.camera = new THREE.PerspectiveCamera(40, aspect, 0.1, 140);
      this.camera.aspect = aspect;
      this.controls.enableRotate = true;
      this.controls.enablePan = true;
      this.controls.enableZoom = true;
    }
    this.controls.object = this.camera;
    this.camera.position.copy(p.position);
    this.controls.target.copy(p.target);
    this.camera.lookAt(p.target);
    if (kept) {
      this.camera.position.add(kept.pan);
      this.controls.target.add(kept.pan);
      this.camera.zoom = kept.zoom;
    }
    if(p.fitPoints && this.camera instanceof THREE.PerspectiveCamera)
      fitPerspective(this.camera,p.fitPoints,safeW/w,safeH/h);
    const centerX = this.inset.left + safeW / 2,
      centerY = this.inset.top + safeH / 2;
    this.camera.setViewOffset(w, h, w / 2 - centerX, h / 2 - centerY, w, h);
    this.camera.updateProjectionMatrix();
    this.controls.update();
  }
  resize = () => {
    if (this.disposed) return;
    const w = Math.max(1, this.container.clientWidth),
      h = Math.max(1, this.container.clientHeight);
    this.renderer.setSize(w, h, false);
    if (!this.orbiting) this.applyCamera();
    else if (this.camera instanceof THREE.PerspectiveCamera) {
      this.camera.aspect=w/h;
      const width=Math.max(1,w-this.inset.left-this.inset.right), height=Math.max(1,h-this.inset.top-this.inset.bottom);
      this.camera.setViewOffset(w,h,(w-width)/2-this.inset.left,(h-height)/2-this.inset.top,w,h);
      this.camera.updateProjectionMatrix();
    }
    this.requestRender();
  };
  requestRender = () => {
    this.scheduler.request();
  };
  invalidateShadows() {
    this.renderer.shadowMap.needsUpdate = true;
    this.requestRender();
  }
  animate(fn: (time: number, finish: boolean) => boolean) {
    if (this.reduced.matches || document.hidden) {
      fn(performance.now(), true);
      this.invalidateShadows();
      return () => {};
    }
    this.animations.add(fn);
    this.requestRender();
    return () => this.animations.delete(fn);
  }
  private finishAnimations() {
    for (const fn of this.animations) fn(performance.now(), true);
    this.animations.clear();
    this.renderer.shadowMap.needsUpdate = true;
  }
  pick(event: { clientX: number; clientY: number }, objects: THREE.Object3D[]) {
    this.scene.updateMatrixWorld(true);
    this.camera.updateMatrixWorld(true);
    return pickObject(
      event.clientX,
      event.clientY,
      this.canvas.getBoundingClientRect(),
      this.camera,
      objects,
      this.raycaster,
    );
  }
  dispose() {
    if (this.disposed) return;
    this.disposed = true;
    this.scheduler.dispose();
    this.animations.clear();
    this.controls.removeEventListener("change", this.requestRender);
    this.controls.removeEventListener("start", this.onControlStart);
    this.controls.dispose();
    this.resources.dispose();
    this.shadow.dispose();
    this.scene.clear();
    this.scene.environment = null;
    this.renderer.renderLists.dispose();
    this.renderer.dispose();
    this.renderer.forceContextLoss();
  }
}
