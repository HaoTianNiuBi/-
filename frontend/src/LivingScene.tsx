import { useEffect, useRef, useState } from "react";
import gsap from "gsap";
import { useGSAP } from "@gsap/react";
gsap.registerPlugin(useGSAP);

type Props = {
  conversationId: string | null;
  rounds: number;
  scopeKey: string;
  paused: boolean;
  archive?: boolean;
};
const load = (src: string) =>
  new Promise<HTMLImageElement>((resolve, reject) => {
    const img = new Image();
    img.onload = () => resolve(img);
    img.onerror = reject;
    img.src = src;
  });
let images: Promise<HTMLImageElement[]> | undefined;
const assets = () =>
  (images ??= Promise.all(
    ["tree", "root", "root-network", "soil"].map((n) =>
      load(`/assets/scene/${n}.png`),
    ),
  ).catch((e) => {
    images = undefined;
    throw e;
  }));
const clamp = (v: number) => Math.max(0, Math.min(1, v));

export default function LivingScene({
  conversationId,
  rounds,
  scopeKey,
  paused,
  archive = false,
}: Props) {
  const canvas = useRef<HTMLCanvasElement>(null),
    host = useRef<HTMLDivElement>(null);
  const [ready, setReady] = useState(false),
    [failed, setFailed] = useState(false),
    [reduced, setReduced] = useState(false);
  const resources = useRef<HTMLImageElement[]>([]);
  const animation = useRef({ camera: 0, growth: 0.25 });
  const flags = useRef({ paused, reduced });
  flags.current = { paused, reduced };
  const currentRounds = useRef(rounds);
  currentRounds.current = rounds;
  useEffect(() => {
    const m = matchMedia("(prefers-reduced-motion: reduce)");
    const update = () => setReduced(m.matches);
    update();
    m.addEventListener("change", update);
    let alive = true;
    assets()
      .then((value) => {
        if (alive) {
          resources.current = value;
          setReady(true);
        }
      })
      .catch(() => {
        if (alive) setFailed(true);
      });
    return () => {
      alive = false;
      m.removeEventListener("change", update);
    };
  }, []);
  useGSAP(
    () => {
      if (!ready) return;
      const target = archive ? -1 : conversationId ? 1 : 0;
      const duration =
        reduced || paused
          ? 0
          : archive || animation.current.camera < 0
            ? 1.05
            : conversationId
            ? currentRounds.current === 0
              ? 1.35
              : 0.25
            : 0.7;
      gsap.to(animation.current, {
        camera: target,
        duration,
        ease: "power2.inOut",
        overwrite: "auto",
      });
    },
    {
      scope: host,
      dependencies: [archive, conversationId, ready, reduced, paused],
      revertOnUpdate: false,
    },
  );
  useGSAP(
    () => {
      if (!ready) return;
      gsap.to(animation.current, {
        growth: clamp(0.25 + rounds * 0.16),
        duration: reduced || paused ? 0 : 1.6,
        ease: "power1.inOut",
        overwrite: "auto",
      });
    },
    {
      scope: host,
      dependencies: [rounds, scopeKey, ready, reduced, paused],
      revertOnUpdate: false,
    },
  );
  useEffect(() => {
    if (!ready || !canvas.current || !host.current) return;
    const c = canvas.current;
    const buffer = document.createElement("canvas");
    const ctx = buffer.getContext("2d");
    const screen = c.getContext("2d", { alpha: false });
    if (!ctx || !screen) return;
    const [tree, root, network, soil] = resources.current;
    const rootLayer = document.createElement("canvas");
    rootLayer.width = 1280;
    rootLayer.height = 800;
    const rc = rootLayer.getContext("2d")!;
    const mask = document.createElement("canvas");
    mask.width = 1280;
    mask.height = 800;
    const mc = mask.getContext("2d")!;
    const points = [
      [1250, -50],
      [1180, 130],
      [1130, 360],
      [950, 470],
      [720, 520],
      [400, 625],
      [-80, 830],
    ];
    const spine: number[][] = [];
    for (let j = 0; j < points.length - 1; j++)
      for (let i = 0; i < 40; i++) {
        const t = i / 40;
        const a = points[Math.max(0, j - 1)],
          b = points[j],
          d = points[j + 1],
          e = points[Math.min(points.length - 1, j + 2)];
        spine.push(
          [0, 1].map(
            (k) =>
              0.5 *
              (2 * b[k] +
                (-a[k] + d[k]) * t +
                (2 * a[k] - 5 * b[k] + 4 * d[k] - e[k]) * t * t +
                (-a[k] + 3 * b[k] - 3 * d[k] + e[k]) * t * t * t),
          ),
        );
      }
    let width = 0,
      height = 0,
      frame = 0,
      last = 0,
      windTime = 0,
      scroll = 0,
      cachedGrowth = -1;
    const resize = () => {
      const box = host.current!.getBoundingClientRect();
      width = box.width;
      height = box.height;
      // Keep the last complete frame visible until its replacement is ready.
    };
    const observer = new ResizeObserver(resize);
    observer.observe(host.current);
    resize();
    const onScroll = () => {
      scroll = window.scrollY;
    };
    onScroll();
    window.addEventListener("scroll", onScroll, { passive: true });
    function updateRoot(progress: number) {
      if (Math.abs(progress - cachedGrowth) < 0.003) return;
      cachedGrowth = progress;
      rc.clearRect(0, 0, 1280, 800);
      rc.drawImage(root, 0, 0, 1280, 800);
      if (progress > 0.998) return;
      mc.clearRect(0, 0, 1280, 800);
      mc.beginPath();
      mc.moveTo(...(spine[0] as [number, number]));
      const n = Math.min(
        spine.length - 1,
        Math.round(progress * (spine.length - 1)),
      );
      for (let i = 1; i <= n; i++) mc.lineTo(spine[i][0], spine[i][1]);
      mc.lineWidth = 325;
      mc.lineCap = "round";
      mc.lineJoin = "round";
      mc.strokeStyle = "#fff";
      mc.filter = "blur(14px)";
      mc.stroke();
      mc.filter = "none";
      rc.globalCompositeOperation = "destination-in";
      rc.drawImage(mask, 0, 0);
      rc.globalCompositeOperation = "source-over";
    }
    const draw = (now: number) => {
      frame = requestAnimationFrame(draw);
      if (document.hidden) return;
      if (now - last < 32) return;
      const dt = Math.min(0.1, (now - last) / 1000);
      last = now;
      if (!flags.current.paused && !flags.current.reduced) windTime += dt;
      const camera = animation.current.camera;
      const mix = Math.max(0, camera),
        canopy = Math.max(0, -camera),
        growth = animation.current.growth;
      const dpr = Math.min(devicePixelRatio || 1, 1.5);
      const pixelWidth = Math.max(1, Math.round(width * dpr));
      const pixelHeight = Math.max(1, Math.round(height * dpr));
      if (buffer.width !== pixelWidth || buffer.height !== pixelHeight) {
        buffer.width = pixelWidth;
        buffer.height = pixelHeight;
      }
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      ctx.clearRect(0, 0, width, height);
      ctx.fillStyle = "#f3efdf";
      ctx.fillRect(0, 0, width, height);
      const scale = Math.max(width / 1280, height / 900),
        offset = width - 1280 * scale;
      const y = mix * (640 + Math.min(scroll, 1200) * 0.1);
      ctx.save();
      ctx.translate(offset, 0);
      ctx.scale(scale, scale);
      ctx.translate(mix * 80, -y);
      if (canopy) {
        ctx.translate(640, 310);
        ctx.scale(1 + canopy * .75, 1 + canopy * .75);
        ctx.translate(-640 + canopy * 55, -310 + canopy * 135);
      }
      // All environmental layers share one ground coordinate and root crown.
      ctx.save();
      ctx.beginPath();
      ctx.moveTo(-600, 690);
      for (let x = -600; x < 1800; x += 12)
        ctx.lineTo(x, 690 + Math.sin(x * 0.08) * 3);
      ctx.lineTo(1800, 2400);
      ctx.lineTo(-600, 2400);
      ctx.closePath();
      ctx.clip();
      ctx.drawImage(soil, -600, 684, 1880, 1500);
      ctx.fillStyle = "rgba(246,239,222,.22)";
      ctx.fillRect(-600, 684, 1880, 1500);
      ctx.globalAlpha = 0.43;
      ctx.drawImage(network, -150, 687, 1450, 1100);
      ctx.restore();
      for (let i = 0; i < 48; i++) {
        const weight = (1 - i / 48) ** 2,
          sway =
            (Math.sin(windTime * 0.8 + i * 0.05) * 8 +
              Math.sin(windTime * 1.3 + i * 0.1) * 2) *
            weight;
        ctx.drawImage(
          tree,
          0,
          (i * tree.height) / 48,
          tree.width,
          tree.height / 48 + 1,
          sway,
          -30 + i * 15,
          1280,
          16,
        );
      }
      updateRoot(growth);
      ctx.save();
      ctx.transform(0.82, 0, -0.72, 0.85, -25, 690);
      ctx.globalAlpha = 0.77;
      ctx.drawImage(rootLayer, 0, 0);
      ctx.restore();
      if (mix > 0.8 && !flags.current.paused && !flags.current.reduced) {
        for (let i = 0; i < 8; i++) {
          const p =
            (growth - ((windTime * 0.035 + i * 0.075) % growth) + growth) %
            growth;
          const pt =
            spine[
              Math.min(spine.length - 1, Math.floor(p * (spine.length - 1)))
            ];
          ctx.globalAlpha = 0.5;
          ctx.fillStyle = "#efd5a2";
          ctx.beginPath();
          ctx.arc(
            pt[0] * 0.82 - pt[1] * 0.72 - 25,
            pt[1] * 0.85 + 690,
            2,
            0,
            7,
          );
          ctx.fill();
        }
        ctx.globalAlpha = 1;
      }
      ctx.restore();
      const wash = ctx.createLinearGradient(0, 0, width, 0);
      const small = width < 600;
      wash.addColorStop(0, `rgba(249,246,236,${small ? 0.86 : 0.78})`);
      wash.addColorStop(0.55, `rgba(249,246,236,${small ? 0.72 : 0.4})`);
      wash.addColorStop(1, "rgba(249,246,236,.04)");
      ctx.fillStyle = wash;
      ctx.fillRect(0, 0, width, height);
      if (c.width !== pixelWidth || c.height !== pixelHeight) {
        c.width = pixelWidth;
        c.height = pixelHeight;
      }
      screen.drawImage(buffer, 0, 0);
    };
    frame = requestAnimationFrame(draw);
    return () => {
      cancelAnimationFrame(frame);
      observer.disconnect();
      window.removeEventListener("scroll", onScroll);
    };
  }, [ready]);
  return (
    <div ref={host} className="living-scene" aria-hidden="true">
      <canvas ref={canvas} />
      {failed && <span className="scene-fallback" />}
    </div>
  );
}
