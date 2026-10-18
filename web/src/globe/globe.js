import * as THREE from "three";
import { OrbitControls } from "three/examples/jsm/controls/OrbitControls.js";
import { makeAtmosphere } from "./atmosphere.js";
import { latLngFromVector3, latLngToVector3, subsolarPoint } from "./geo.js";
import { createSatellites } from "./satellites.js";
import { FEATURED } from "../places.js";
import colorUrl from "../assets/earth/earth_atmos_2048.jpg";
import normalUrl from "../assets/earth/earth_normal_2048.jpg";
import specUrl from "../assets/earth/earth_specular_2048.jpg";
import lightsUrl from "../assets/earth/earth_lights_2048.png";

const RADIUS = 1;
const ARC_ANGLE = THREE.MathUtils.degToRad(25);
const ARC_REVEAL = 0.6;
const ARC_FADE = 0.45;
const HOME = new THREE.Vector3(0.22, 0.62, 3.12);
const Y_AXIS = new THREE.Vector3(0, 1, 0);
const SPIN_RATE = 0.05; // rad/s of the slow spin
const SPIN_RESUME_AFTER = 6; // seconds after the last interaction
const SPIN_RAMP = 1.5; // seconds to ease the spin back in
const STAR_COUNT = 4500;

const lightsVertex = `
  varying vec2 vUv;
  varying vec3 vNormal;
  void main() {
    vUv = uv;
    vNormal = normalize(mat3(modelMatrix) * normal);
    gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0);
  }
`;

// City lights come up across the terminator and sit on the night side only.
const lightsFragment = `
  uniform sampler2D map;
  uniform vec3 uSun;
  varying vec2 vUv;
  varying vec3 vNormal;
  void main() {
    float sunDot = dot(normalize(vNormal), normalize(uSun));
    float night = 1.0 - smoothstep(-0.2, 0.1, sunDot);
    vec3 col = texture2D(map, vUv).rgb * vec3(1.0, 0.88, 0.7);
    gl_FragColor = vec4(col * night * 1.2, 1.0);
  }
`;

const starVertex = `
  attribute float aSize;
  attribute float aPhase;
  attribute vec3 aColor;
  uniform float uScale;
  uniform float uTime;
  varying vec3 vColor;
  varying float vTwinkle;
  void main() {
    vColor = aColor;
    vTwinkle = 0.82 + 0.18 * sin(uTime * (0.5 + fract(aPhase) * 1.3) + aPhase);
    vec4 mv = modelViewMatrix * vec4(position, 1.0);
    gl_PointSize = max(1.0, aSize * uScale * vTwinkle / -mv.z);
    gl_Position = projectionMatrix * mv;
  }
`;

const starFragment = `
  varying vec3 vColor;
  varying float vTwinkle;
  void main() {
    float d = length(gl_PointCoord - 0.5);
    float a = smoothstep(0.5, 0.1, d);
    gl_FragColor = vec4(vColor * vTwinkle, a);
  }
`;

function easeInOut(t) {
  return t < 0.5 ? 2 * t * t : 1 - ((-2 * t + 2) ** 2) / 2;
}

function yawFacing(dir) {
  return Math.atan2(-dir.x, dir.z);
}

function shortestYaw(from, to) {
  let delta = to - from;
  while (delta > Math.PI) delta -= Math.PI * 2;
  while (delta < -Math.PI) delta += Math.PI * 2;
  return from + delta;
}

function insideBounds(lat, lng, bounds) {
  if (!bounds || bounds.length !== 4) return false;
  const [west, south, east, north] = bounds;
  return lng >= west && lng <= east && lat >= south && lat <= north;
}

/**
 * A procedural sky: a few thousand points, no texture. Four in ten sit in a tilted band with a
 * roughly normal spread off its plane, which reads as the Milky Way; the rest are uniform. A few
 * are bright, most are faint, and the tint runs from blue-white to warm.
 */
function makeStars() {
  const positions = new Float32Array(STAR_COUNT * 3);
  const colors = new Float32Array(STAR_COUNT * 3);
  const sizes = new Float32Array(STAR_COUNT);
  const phases = new Float32Array(STAR_COUNT);
  const bandNormal = new THREE.Vector3(0.35, 0.8, 0.5).normalize();
  const u = new THREE.Vector3().crossVectors(bandNormal, new THREE.Vector3(1, 0, 0)).normalize();
  const v = new THREE.Vector3().crossVectors(bandNormal, u).normalize();
  const dir = new THREE.Vector3();
  for (let i = 0; i < STAR_COUNT; i += 1) {
    if (i < STAR_COUNT * 0.4) {
      const along = Math.random() * Math.PI * 2;
      const off = (Math.random() + Math.random() + Math.random() - 1.5) * 0.24;
      dir.copy(u).multiplyScalar(Math.cos(along)).addScaledVector(v, Math.sin(along)).addScaledVector(bandNormal, Math.tan(off)).normalize();
    } else {
      dir.randomDirection();
    }
    dir.multiplyScalar(45 + Math.random() * 10);
    positions.set([dir.x, dir.y, dir.z], i * 3);
    const tint = Math.random();
    const base = tint < 0.12 ? [0.7, 0.8, 1.0] : tint < 0.22 ? [1.0, 0.85, 0.7] : [0.92, 0.95, 1.0];
    const mag = Math.random();
    const bright = mag ** 3;
    const k = 0.42 + 0.58 * bright;
    colors.set([base[0] * k, base[1] * k, base[2] * k], i * 3);
    sizes[i] = 0.14 + 0.36 * bright + (mag > 0.985 ? 0.32 : 0);
    phases[i] = Math.random() * Math.PI * 2;
  }
  const geometry = new THREE.BufferGeometry();
  geometry.setAttribute("position", new THREE.BufferAttribute(positions, 3));
  geometry.setAttribute("aColor", new THREE.BufferAttribute(colors, 3));
  geometry.setAttribute("aSize", new THREE.BufferAttribute(sizes, 1));
  geometry.setAttribute("aPhase", new THREE.BufferAttribute(phases, 1));
  const material = new THREE.ShaderMaterial({
    uniforms: { uScale: { value: 400 }, uTime: { value: 0 } },
    vertexShader: starVertex,
    fragmentShader: starFragment,
    transparent: true,
    depthWrite: false,
    blending: THREE.AdditiveBlending,
  });
  const points = new THREE.Points(geometry, material);
  points.frustumCulled = false;
  return points;
}

export function createGlobe(container, { bounds, onEnter, onToast }) {
  const reduced = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  const renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true });
  const pixelRatio = Math.min(window.devicePixelRatio || 1, 2);
  renderer.setPixelRatio(pixelRatio);
  renderer.setClearColor(0x000000, 0);
  renderer.outputColorSpace = THREE.SRGBColorSpace;
  renderer.toneMapping = THREE.ACESFilmicToneMapping;
  renderer.toneMappingExposure = 1.1;
  container.appendChild(renderer.domElement);

  const label = document.createElement("div");
  label.className = "place-label";
  label.hidden = true;
  container.appendChild(label);

  const scene = new THREE.Scene();
  const camera = new THREE.PerspectiveCamera(42, 1, 0.05, 100);
  camera.position.copy(HOME);

  const controls = new OrbitControls(camera, renderer.domElement);
  controls.enableDamping = true;
  controls.enablePan = false;
  controls.minDistance = 1.6;
  controls.maxDistance = 6;
  controls.autoRotate = false;
  controls.target.set(0, 0, 0);

  // The slow spin pauses on any drag, wheel or touch and eases back in six seconds after the last one.
  let dragging = false;
  let idleSince = -SPIN_RESUME_AFTER;
  controls.addEventListener("start", () => {
    dragging = true;
  });
  controls.addEventListener("end", () => {
    dragging = false;
    idleSince = elapsed;
  });

  const sun = new THREE.DirectionalLight(0xffffff, 2.4);
  scene.add(sun);
  scene.add(new THREE.AmbientLight(0x8fa6c8, 0.22));

  const earth = new THREE.Group();
  scene.add(earth);

  const loader = new THREE.TextureLoader();
  const colorMap = loader.load(colorUrl);
  const normalMap = loader.load(normalUrl);
  const specMap = loader.load(specUrl);
  const lightsMap = loader.load(lightsUrl);
  colorMap.colorSpace = THREE.SRGBColorSpace;
  lightsMap.colorSpace = THREE.SRGBColorSpace;
  colorMap.anisotropy = Math.min(8, renderer.capabilities.getMaxAnisotropy());
  const textures = [colorMap, normalMap, specMap, lightsMap];

  const earthGeo = new THREE.SphereGeometry(RADIUS, 96, 96);
  const earthMat = new THREE.MeshPhongMaterial({
    map: colorMap,
    normalMap,
    normalScale: new THREE.Vector2(0.6, 0.6),
    specularMap: specMap,
    specular: new THREE.Color(0x2b2b2b),
    shininess: 16,
  });
  const earthMesh = new THREE.Mesh(earthGeo, earthMat);
  earth.add(earthMesh);

  const lightsMat = new THREE.ShaderMaterial({
    uniforms: {
      map: { value: lightsMap },
      uSun: { value: new THREE.Vector3(1, 0.4, 0.6) },
    },
    vertexShader: lightsVertex,
    fragmentShader: lightsFragment,
    blending: THREE.AdditiveBlending,
    transparent: true,
    depthWrite: false,
  });
  const lights = new THREE.Mesh(earthGeo, lightsMat);
  lights.scale.setScalar(1.001);
  earth.add(lights);

  const atmosphere = makeAtmosphere(RADIUS);
  earth.add(atmosphere.group);

  const stars = makeStars();
  scene.add(stars);

  const story = FEATURED.find((place) => place.id === "story");
  const aoiLocal = latLngToVector3(story.lat, story.lng, 1.002);
  const aoiDir = aoiLocal.clone().normalize();

  const pickMeshes = [];
  const pulses = [];
  for (const place of FEATURED) {
    const local = latLngToVector3(place.lat, place.lng, 1.012);
    const group = new THREE.Group();
    group.position.copy(local);
    group.quaternion.setFromUnitVectors(new THREE.Vector3(0, 0, 1), local.clone().normalize());
    const live = place.live === true;
    const featured = place.featured === true;
    const color = live ? 0x3bb273 : featured ? 0xf4a259 : 0xffffff;
    const radius = live ? 0.012 : featured ? 0.016 : 0.007;
    const core = new THREE.Mesh(
      new THREE.SphereGeometry(radius, 16, 16),
      new THREE.MeshBasicMaterial({
        color,
        transparent: !live && !featured,
        opacity: live || featured ? 1 : 0.9,
      }),
    );
    group.add(core);
    if (live || featured) {
      const ring = new THREE.Mesh(
        new THREE.RingGeometry(radius * 1.3, radius * 1.85, 48),
        new THREE.MeshBasicMaterial({
          color,
          transparent: true,
          opacity: 0.85,
          side: THREE.DoubleSide,
          depthWrite: false,
        }),
      );
      group.add(ring);
      pulses.push(ring);
    }
    const pick = new THREE.Mesh(
      new THREE.SphereGeometry(live || featured ? 0.045 : 0.028, 10, 10),
      new THREE.MeshBasicMaterial({ visible: false }),
    );
    pick.userData.place = place;
    group.add(pick);
    pickMeshes.push(pick);
    earth.add(group);
  }

  // Real satellites from the CelesTrak snapshot; the visit arc below fires when a Sentinel-2 nears the farms.
  const sats = createSatellites({
    earth,
    overlay: container,
    legendHost: container.parentElement || container,
    reduced,
  });

  const arcPositions = new Float32Array(49 * 3);
  const arcGeom = new THREE.BufferGeometry();
  arcGeom.setAttribute("position", new THREE.BufferAttribute(arcPositions, 3));
  const arcMat = new THREE.LineBasicMaterial({ color: 0xb7ffd0, transparent: true, opacity: 0 });
  const arcLine = new THREE.Line(arcGeom, arcMat);
  arcLine.frustumCulled = false;
  arcLine.visible = false;
  const glow = new THREE.Mesh(
    new THREE.SphereGeometry(0.018, 12, 12),
    new THREE.MeshBasicMaterial({ color: 0xffffff, transparent: true, opacity: 0 }),
  );
  glow.visible = false;
  earth.add(arcLine, glow);

  let specCanvas = null;
  let specCtx = null;
  const specImage = new Image();
  specImage.onload = () => {
    specCanvas = document.createElement("canvas");
    specCanvas.width = specImage.width;
    specCanvas.height = specImage.height;
    specCtx = specCanvas.getContext("2d", { willReadFrequently: true });
    specCtx.drawImage(specImage, 0, 0);
  };
  specImage.src = specUrl;

  function isWater(uv) {
    if (!specCtx || !uv) return false;
    const x = Math.min(specCanvas.width - 1, Math.max(0, Math.floor(uv.x * specCanvas.width)));
    const y = Math.min(specCanvas.height - 1, Math.max(0, Math.floor((1 - uv.y) * specCanvas.height)));
    const pixel = specCtx.getImageData(x, y, 1, 1).data;
    return pixel[0] > 150;
  }

  const raycaster = new THREE.Raycaster();
  const pointer = new THREE.Vector2();
  let hovered = null;
  let pointerDown = null;

  function setRay(event) {
    const rect = renderer.domElement.getBoundingClientRect();
    pointer.x = ((event.clientX - rect.left) / rect.width) * 2 - 1;
    pointer.y = -((event.clientY - rect.top) / rect.height) * 2 + 1;
    raycaster.setFromCamera(pointer, camera);
  }

  function beginAim(dir, duration, done) {
    controls.enabled = false;
    fly.active = true;
    fly.mode = "in";
    fly.start = elapsed;
    fly.dur = reduced ? 0 : duration;
    fly.yaw0 = earth.rotation.y;
    fly.cam0.copy(camera.position);
    fly.onDone = done;
    const targetYaw = shortestYaw(earth.rotation.y, yawFacing(dir));
    fly.yaw1 = targetYaw;
    const aimed = dir.clone().applyAxisAngle(Y_AXIS, targetYaw);
    fly.cam1.copy(aimed.normalize()).multiplyScalar(1.25);
    if (reduced) finishFly();
  }

  function facingCamera(object) {
    const pos = object.getWorldPosition(new THREE.Vector3());
    return pos.normalize().dot(camera.position.clone().normalize());
  }

  renderer.domElement.addEventListener("pointerdown", (event) => {
    pointerDown = { x: event.clientX, y: event.clientY };
  });
  renderer.domElement.addEventListener("pointermove", (event) => {
    if (fly.active) return;
    setRay(event);
    const hits = raycaster.intersectObjects(pickMeshes, false);
    hovered = hits.find((hit) => facingCamera(hit.object) > 0.08)?.object || null;
    renderer.domElement.style.cursor = hovered ? "pointer" : "";
  });
  renderer.domElement.addEventListener("click", (event) => {
    if (fly.active || !pointerDown) return;
    if (Math.hypot(event.clientX - pointerDown.x, event.clientY - pointerDown.y) > 5) return;
    setRay(event);
    const markerHit = raycaster.intersectObjects(pickMeshes, false).find((hit) => facingCamera(hit.object) > 0.08);
    if (markerHit) {
      const place = markerHit.object.userData.place;
      const live = place.live === true || insideBounds(place.lat, place.lng, bounds);
      beginAim(latLngToVector3(place.lat, place.lng, 1).normalize(), 1.6, () => {
        if (onEnter) onEnter({ live, lat: place.lat, lng: place.lng, name: place.name });
      });
      return;
    }
    const earthHit = raycaster.intersectObject(earthMesh, false)[0];
    if (!earthHit) return;
    if (isWater(earthHit.uv)) {
      if (onToast) onToast("Open water: no farms here");
      return;
    }
    const local = earth.worldToLocal(earthHit.point.clone());
    const { lat, lng } = latLngFromVector3(local);
    const live = insideBounds(lat, lng, bounds);
    beginAim(local.normalize(), 1.6, () => {
      if (onEnter) onEnter({ live, lat, lng, name: null });
    });
  });

  let viewWidth = 1;
  let viewHeight = 1;
  const resize = () => {
    viewWidth = Math.max(1, container.clientWidth);
    viewHeight = Math.max(1, container.clientHeight);
    camera.aspect = viewWidth / viewHeight;
    // Portrait screens: keep the horizontal field of view, so the whole globe stays in frame on a phone.
    const baseFov = 42;
    camera.fov = camera.aspect < 1 ? (2 * Math.atan(Math.tan((baseFov * Math.PI) / 360) / camera.aspect) * 180) / Math.PI : baseFov;
    camera.updateProjectionMatrix();
    renderer.setSize(viewWidth, viewHeight, false);
    const scale = viewHeight * 0.5 * pixelRatio;
    stars.material.uniforms.uScale.value = scale;
    sats.setViewport(viewWidth * pixelRatio, viewHeight * pixelRatio);
  };
  const observer = new ResizeObserver(resize);
  observer.observe(container);
  resize();

  let raf = 0;
  let running = false;
  let last = performance.now();
  let elapsed = 0;
  let passLatched = false;
  const arc = { active: false, start: 0 };
  const fly = {
    active: false,
    mode: "in",
    start: 0,
    dur: 1.8,
    yaw0: 0,
    yaw1: 0,
    cam0: new THREE.Vector3(),
    cam1: new THREE.Vector3(),
    onDone: null,
  };
  const labelPos = new THREE.Vector3();
  const sunLocal = new THREE.Vector3();
  const sunWorld = new THREE.Vector3();

  function finishFly() {
    earth.rotation.y = fly.yaw1;
    camera.position.copy(fly.cam1);
    camera.lookAt(0, 0, 0);
    controls.target.set(0, 0, 0);
    controls.update();
    fly.active = false;
    controls.enabled = fly.mode !== "in";
    idleSince = elapsed;
    const done = fly.onDone;
    fly.onDone = null;
    if (done) done();
  }

  function stepFly() {
    const t = fly.dur === 0 ? 1 : Math.min(1, (elapsed - fly.start) / fly.dur);
    const k = easeInOut(t);
    const spinEnd = fly.mode === "in" ? 0.38 : 1;
    const yawK = fly.mode === "in" ? Math.min(1, k / spinEnd) : k;
    earth.rotation.y = fly.yaw0 + (fly.yaw1 - fly.yaw0) * yawK;
    const dollyK = fly.mode === "in" ? Math.max(0, (k - 0.28) / 0.72) : k;
    camera.position.lerpVectors(fly.cam0, fly.cam1, easeInOut(Math.min(1, dollyK)));
    camera.lookAt(0, 0, 0);
    if (t >= 1) finishFly();
  }

  /** When a Sentinel-2 comes within 25° of the farms, an arc from it reaches down to them. */
  function updateArc(nearest) {
    const separation = nearest ? nearest.angle : Infinity;
    if (separation > ARC_ANGLE) passLatched = false;
    if (separation < ARC_ANGLE && !passLatched && !arc.active) {
      passLatched = true;
      arc.active = true;
      arc.start = elapsed;
    }
    if (!arc.active || !nearest) {
      arc.active = false;
      arcLine.visible = false;
      glow.visible = false;
      return;
    }
    const age = elapsed - arc.start;
    let reveal = 1;
    let opacity = 1;
    if (age < ARC_REVEAL) reveal = age / ARC_REVEAL;
    else if (age < ARC_REVEAL + ARC_FADE) opacity = 1 - (age - ARC_REVEAL) / ARC_FADE;
    else {
      arc.active = false;
      arcLine.visible = false;
      glow.visible = false;
      return;
    }
    const satPos = nearest.position;
    const mid = satPos.clone().lerp(aoiLocal, 0.5);
    const control = mid.clone().normalize().multiplyScalar(mid.length() + 0.2);
    const curve = new THREE.QuadraticBezierCurve3(satPos, control, aoiLocal);
    const count = Math.max(2, Math.round(48 * reveal));
    for (let i = 0; i <= count; i += 1) {
      const point = curve.getPoint(i / 48);
      arcPositions.set([point.x, point.y, point.z], i * 3);
    }
    arcGeom.attributes.position.needsUpdate = true;
    arcGeom.setDrawRange(0, count + 1);
    arcMat.opacity = opacity;
    arcLine.visible = true;
    glow.position.copy(curve.getPoint(reveal));
    glow.material.opacity = opacity;
    glow.visible = true;
  }

  /** The sun sits where it really is for the satellite clock's UTC time, so the terminator is honest. */
  function placeSun(date) {
    const { lat, lng } = subsolarPoint(date);
    sunLocal.copy(latLngToVector3(lat, lng, 1));
    sunWorld.copy(sunLocal).applyQuaternion(earth.quaternion);
    sun.position.copy(sunWorld).multiplyScalar(10);
    lightsMat.uniforms.uSun.value.copy(sunWorld);
    atmosphere.update(sunWorld, camera.position);
  }

  function updateLabel() {
    if (!hovered) {
      label.hidden = true;
      return;
    }
    hovered.getWorldPosition(labelPos);
    const facing = labelPos.clone().normalize().dot(camera.position.clone().normalize());
    const ndc = labelPos.clone().project(camera);
    if (facing < 0.05 || ndc.z > 1) {
      label.hidden = true;
      return;
    }
    label.hidden = false;
    label.textContent = hovered.userData.place.name;
    label.style.left = `${(ndc.x * 0.5 + 0.5) * viewWidth}px`;
    label.style.top = `${(-ndc.y * 0.5 + 0.5) * viewHeight}px`;
  }

  function frame(now) {
    if (!running) return;
    raf = requestAnimationFrame(frame);
    const delta = Math.min(0.05, (now - last) / 1000);
    last = now;
    elapsed += delta;
    if (fly.active) stepFly();
    else if (!dragging) {
      const idle = elapsed - idleSince;
      if (idle > SPIN_RESUME_AFTER) {
        const ramp = Math.min(1, (idle - SPIN_RESUME_AFTER) / SPIN_RAMP);
        earth.rotation.y += delta * (reduced ? 0.004 : SPIN_RATE) * easeInOut(ramp);
      }
    }
    if (!fly.active) controls.update();
    camera.updateMatrixWorld();
    earth.updateMatrixWorld();
    sats.update(delta, camera, viewWidth, viewHeight);
    updateArc(sats.nearestSentinel(aoiDir));
    const pulse = (elapsed % 2) / 2;
    for (const ring of pulses) {
      ring.scale.setScalar(1 + pulse * 2.4);
      ring.material.opacity = 0.85 * (1 - pulse);
    }
    stars.material.uniforms.uTime.value = elapsed;
    placeSun(sats.now());
    updateLabel();
    renderer.render(scene, camera);
  }

  function start() {
    if (running) return;
    running = true;
    last = performance.now();
    raf = requestAnimationFrame(frame);
  }

  function stop() {
    running = false;
    cancelAnimationFrame(raf);
  }

  function disposeMaterial(material) {
    if (Array.isArray(material)) material.forEach(disposeMaterial);
    else material?.dispose();
  }

  return {
    start,
    stop,
    flyIn() {
      beginAim(aoiDir, 1.8, () => {
        if (onEnter) onEnter({ live: true, lat: story.lat, lng: story.lng, name: story.name });
      });
    },
    flyOut() {
      controls.enabled = false;
      fly.active = true;
      fly.mode = "out";
      fly.start = elapsed;
      fly.dur = reduced ? 0 : 1.2;
      fly.yaw0 = earth.rotation.y;
      fly.cam0.copy(camera.position);
      fly.yaw1 = shortestYaw(earth.rotation.y, 0);
      fly.cam1.copy(HOME);
      fly.onDone = null;
      if (reduced) finishFly();
    },
    dispose() {
      stop();
      observer.disconnect();
      controls.dispose();
      sats.dispose();
      atmosphere.dispose();
      earth.traverse((obj) => {
        obj.geometry?.dispose();
        if (obj.material) disposeMaterial(obj.material);
      });
      stars.geometry.dispose();
      stars.material.dispose();
      textures.forEach((texture) => texture.dispose());
      renderer.dispose();
      renderer.domElement.remove();
      label.remove();
    },
  };
}
