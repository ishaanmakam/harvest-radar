import * as THREE from "three";

const vertexShader = `
  varying vec3 vNormal;
  varying vec3 vWorldPos;
  void main() {
    vNormal = normalize(normalMatrix * normal);
    vec4 world = modelMatrix * vec4(position, 1.0);
    vWorldPos = world.xyz;
    gl_Position = projectionMatrix * viewMatrix * world;
  }
`;

const fragmentShader = `
  varying vec3 vNormal;
  varying vec3 vWorldPos;
  void main() {
    vec3 viewDir = normalize(cameraPosition - vWorldPos);
    float fresnel = pow(max(0.7 - dot(normalize(vNormal), viewDir), 0.0), 3.0);
    gl_FragColor = vec4(0.353, 0.690, 1.0, fresnel);
  }
`;

export function makeAtmosphere(radius) {
  return new THREE.Mesh(
    new THREE.SphereGeometry(radius * 1.08, 64, 64),
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
