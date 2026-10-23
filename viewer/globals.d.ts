// The vendored three.js (r128) is a classic script that sets a global THREE.
import type * as Three from "three";

declare global {
  const THREE: typeof Three;
}
