"use client";

import { useSyncExternalStore } from "react";
import { Canvas } from "@react-three/fiber";
import { ParticleSphere } from "./particle-sphere";

// Mount detection without setState-in-effect (Next.js 16 / React 19 lint rule).
// The Canvas needs a real DOM, so we render nothing on the server and on the
// very first client render before hydration completes.
const subscribeNoop = () => () => {};
const getClientSnapshot = () => true;
const getServerSnapshot = () => false;

interface Props {
  mouseX?: number;
  mouseY?: number;
  isHovered?: boolean;
}

export function HeroSphere({ mouseX = 0, mouseY = 0, isHovered = false }: Props) {
  const mounted = useSyncExternalStore(
    subscribeNoop,
    getClientSnapshot,
    getServerSnapshot
  );
  if (!mounted) return null;

  return (
    <Canvas
      frameloop="always"
      camera={{ position: [0, 0, 5], fov: 75 }}
      gl={{ antialias: true, alpha: true }}
      style={{ background: "transparent", width: "100%", height: "100%" }}
    >
      <ambientLight intensity={0.4} />
      <ParticleSphere mouseX={mouseX} mouseY={mouseY} isHovered={isHovered} />
    </Canvas>
  );
}
