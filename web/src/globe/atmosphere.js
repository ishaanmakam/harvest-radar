import * as THREE from "three";

/**
 * Two cheap shells, no post-processing. The outer halo (back faces of a slightly larger sphere)
 * is a fresnel band that is brightest right at the limb and fades outward; the inner rim (front
 * faces just above the surface) tints the edge of the day side blue. Both read the sun: the halo
 * is fullest on the day side, warms and strengthens along the terminator, and nearly vanishes
 * on the night side.
 */

const vertexShader = `
  varying vec3 vNormal;
  varying vec3 vWorldPos;
  void main() {
    vNormal = normalize(mat3(modelMatrix) * normal);
    vec4 world = modelMatrix * vec4(position, 1.0);
    vWorldPos = world.xyz;
    gl_Position = projectionMatrix * viewMatrix * world;
  }
`;

// On the back shell the visible band runs from n·v = -uInner (right at the Earth's limb) to 0
// (the shell's own silhouette), so t goes 1 → 0 across the halo.
const haloFragment = `
  uniform vec3 uTint;
  uniform vec3 uSun;
  uniform vec3 uCamPos;
  uniform float uInner;
  varying vec3 vNormal;
  varying vec3 vWorldPos;
  void main() {
    vec3 n = normalize(vNormal);
    vec3 v = normalize(uCamPos - vWorldPos);
    float t = clamp(-dot(n, v) / uInner, 0.0, 1.0);
    float band = pow(t, 2.4);
    float sunDot = dot(n, normalize(uSun));
    float day = smoothstep(-0.45, 0.25, sunDot);
    float terminator = 1.0 - smoothstep(0.0, 0.45, abs(sunDot));
    vec3 blue = vec3(0.36, 0.64, 1.0);
    vec3 warm = vec3(1.0, 0.62, 0.38);
    vec3 col = mix(blue, warm, terminator * 0.55);
    float strength = band * (0.08 + 0.72 * day + 0.45 * terminator * day);
    gl_FragColor = vec4(col * uTint * strength, strength);
  }
`;

const rimFragment = `
  uniform vec3 uTint;
  uniform vec3 uSun;
  uniform vec3 uCamPos;
  varying vec3 vNormal;
  varying vec3 vWorldPos;
  void main() {
    vec3 n = normalize(vNormal);
    vec3 v = normalize(uCamPos - vWorldPos);
    float fresnel = pow(1.0 - clamp(dot(n, v), 0.0, 1.0), 3.2);
    float sunDot = dot(n, normalize(uSun));
    float day = smoothstep(-0.3, 0.3, sunDot);
    float terminator = 1.0 - smoothstep(0.0, 0.4, abs(sunDot));
    vec3 col = mix(vec3(0.42, 0.68, 1.0), vec3(1.0, 0.7, 0.45), terminator * 0.5);
    float strength = fresnel * (0.05 + 0.5 * day + 0.25 * terminator);
    gl_FragColor = vec4(col * uTint * strength, strength);
  }
`;

export function makeAtmosphere(radius, tint = 0xffffff) {
  const outerRadius = radius * 1.035;
  const uniforms = {
    uSun: { value: new THREE.Vector3(1, 0, 0) },
    uCamPos: { value: new THREE.Vector3(0, 0, 3) },
    uInner: { value: Math.sqrt(1 - (radius / outerRadius) ** 2) },
    uTint: { value: new THREE.Color(tint) },
  };
  const halo = new THREE.Mesh(
    new THREE.SphereGeometry(outerRadius, 72, 72),
    new THREE.ShaderMaterial({
      uniforms,
      vertexShader,
      fragmentShader: haloFragment,
      side: THREE.BackSide,
      blending: THREE.AdditiveBlending,
      transparent: true,
      depthWrite: false,
    }),
  );
  const rim = new THREE.Mesh(
    new THREE.SphereGeometry(radius * 1.004, 72, 72),
    new THREE.ShaderMaterial({
      uniforms,
      vertexShader,
      fragmentShader: rimFragment,
      side: THREE.FrontSide,
      blending: THREE.AdditiveBlending,
      transparent: true,
      depthWrite: false,
    }),
  );
  const group = new THREE.Group();
  group.add(rim, halo);
  return {
    group,
    /** Sun direction and camera position, both in world space. */
    update(sunWorld, cameraPos) {
      uniforms.uSun.value.copy(sunWorld);
      uniforms.uCamPos.value.copy(cameraPos);
    },
    dispose() {
      halo.geometry.dispose();
      halo.material.dispose();
      rim.geometry.dispose();
      rim.material.dispose();
    },
  };
}
