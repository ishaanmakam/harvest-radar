import * as THREE from "three";

const vertexShader = `
  varying vec3 vNormal;
  void main() {
    vNormal = normalize(normalMatrix * normal);
    gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0);
  }
`;

const fragmentShader = `
  varying vec3 vNormal;
  void main() {
    float fresnel = pow(clamp(0.65 - dot(normalize(vNormal), vec3(0.0, 0.0, 1.0)), 0.0, 1.0), 4.0);
    gl_FragColor = vec4(0.353, 0.690, 1.0, fresnel * 0.6);
  }
`;

export function makeAtmosphere(radius) {
  return new THREE.Mesh(
    new THREE.SphereGeometry(radius * 1.032, 64, 64),
    new THREE.ShaderMaterial({
      vertexShader,
      fragmentShader,
      side: THREE.BackSide,
      blending: THREE.AdditiveBlending,
      transparent: true,
      depthWrite: false,
    }),
  );
}
