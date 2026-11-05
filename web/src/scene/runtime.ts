import { Camera, Object3D, Raycaster, Vector2 } from "three";
export class ResourceOwner {
  private resources = new Set<{ dispose: () => void }>();
  private cleanups: (() => void)[] = [];
  private disposed = false;
  track<T extends { dispose: () => void }>(value: T): T {
    this.resources.add(value);
    return value;
  }
  cleanup(fn: () => void) {
    this.cleanups.push(fn);
    return fn;
  }
  listen(
    target: EventTarget,
    type: string,
    fn: EventListenerOrEventListenerObject,
    options?: AddEventListenerOptions,
  ) {
    target.addEventListener(type, fn, options);
    this.cleanup(() => target.removeEventListener(type, fn, options));
  }
  dispose() {
    if (this.disposed) return;
    this.disposed = true;
    for (const fn of this.cleanups.splice(0).reverse()) fn();
    for (const r of this.resources) r.dispose();
    this.resources.clear();
  }
}
export class RenderScheduler {
  private frame: ((time: number) => boolean | void) | null = null;
  private raf = 0;
  private disposed = false;
  constructor(
    private requestFrame = (cb: FrameRequestCallback) =>
      requestAnimationFrame(cb),
    private cancelFrame = (id: number) => cancelAnimationFrame(id),
    private hidden = () => document.hidden,
  ) {}
  setFrame(fn: (time: number) => boolean | void) {
    this.frame = fn;
  }
  request() {
    if (this.disposed || this.raf || this.hidden()) return;
    this.raf = this.requestFrame((t) => {
      this.raf = 0;
      if (this.disposed || this.hidden()) return;
      if (this.frame?.(t)) this.request();
    });
  }
  pause() {
    if (this.raf) this.cancelFrame(this.raf);
    this.raf = 0;
  }
  dispose() {
    this.disposed = true;
    this.pause();
    this.frame = null;
  }
}
export function pickObject(
  x: number,
  y: number,
  rect: { left: number; top: number; width: number; height: number },
  camera: Camera,
  objects: Object3D[],
  ray = new Raycaster(),
) {
  if (!rect.width || !rect.height) return;
  ray.setFromCamera(
    new Vector2(
      ((x - rect.left) / rect.width) * 2 - 1,
      (-(y - rect.top) / rect.height) * 2 + 1,
    ),
    camera,
  );
  return ray.intersectObjects(objects, true)[0];
}
