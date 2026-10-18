import * as THREE from "three";
import { OrbitControls } from "three/examples/jsm/controls/OrbitControls.js";
import { makeAtmosphere } from "./atmosphere.js";
import { latLngToVector3 } from "./geo.js";
import colorUrl from "../assets/earth/earth_atmos_2048.jpg";
import normalUrl from "../assets/earth/earth_normal_2048.jpg";
import specUrl from "../assets/earth/earth_specular_2048.jpg";
import lightsUrl from "../assets/earth/earth_lights_2048.png";

const RADIUS = 1;
const ORBIT_RADIUS = 1.18;
const ORBIT_PERIOD = 12;
const ARC_ANGLE = THREE.MathUtils.degToRad(25);
const ARC_REVEAL = 0.6;
const ARC_FADE = 0.45;
const HOME = new THREE.Vector3(0.22, 0.62, 3.12);

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

export function createGlobe(container, { aoi, stats, onEnter }) {
  void stats;
  const reduced = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  const renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true });
  renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
  renderer.setClearColor(0x000000, 0);
  renderer.outputColorSpace = THREE.SRGBColorSpace;
  container.appendChild(renderer.domElement);

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

  let dragging = false;
  controls.addEventListener("start", () => {
    dragging = true;
  });
  controls.addEventListener("end", () => {
    dragging = false;
  });

  const sun = new THREE.DirectionalLight(0xffffff, 2.1);
  sun.position.set(-4, 2.2, 1.4);
  scene.add(sun);
  scene.add(new THREE.AmbientLight(0x8ea4c8, 0.28));

  const earth = new THREE.Group();
  scene.add(earth);

  const loader = new THREE.TextureLoader();
  const colorMap = loader.load(colorUrl);
  const normalMap = loader.load(normalUrl);
  const specMap = loader.load(specUrl);
  const lightsMap = loader.load(lightsUrl);
  colorMap.colorSpace = THREE.SRGBColorSpace;
  lightsMap.colorSpace = THREE.SRGBColorSpace;
  const textures = [colorMap, normalMap, specMap, lightsMap];

  const earthGeo = new THREE.SphereGeometry(RADIUS, 96, 96);
  const earthMat = new THREE.MeshPhongMaterial({
    map: colorMap,
    normalMap,
    specularMap: specMap,
    shininess: 12,
  });
  earth.add(new THREE.Mesh(earthGeo, earthMat));

  const lightsMat = new THREE.MeshBasicMaterial({
    map: lightsMap,
    blending: THREE.AdditiveBlending,
    transparent: true,
    opacity: 0.35,
    depthWrite: false,
  });
  const lights = new THREE.Mesh(earthGeo, lightsMat);
  lights.scale.setScalar(1.001);
  earth.add(lights);

  const atmosphere = makeAtmosphere(RADIUS);
  earth.add(atmosphere);

  const aoiLocal = latLngToVector3(aoi.lat, aoi.lng, 1.002);
  const aoiDir = aoiLocal.clone().normalize();
  const marker = new THREE.Group();
  marker.position.copy(aoiLocal);
  marker.quaternion.setFromUnitVectors(new THREE.Vector3(0, 0, 1), aoiDir);
  const core = new THREE.Mesh(
    new THREE.SphereGeometry(0.012, 16, 16),
    new THREE.MeshBasicMaterial({ color: 0xf4a259 }),
  );
  const ring = new THREE.Mesh(
    new THREE.RingGeometry(0.016, 0.022, 48),
    new THREE.MeshBasicMaterial({
      color: 0x3bb273,
      transparent: true,
      opacity: 0.85,
      side: THREE.DoubleSide,
      depthWrite: false,
    }),
  );
  marker.add(core, ring);
  earth.add(marker);

  const north = new THREE.Vector3(0, 1, 0);
  const orbitNormal = new THREE.Vector3().crossVectors(north, aoiDir);
  if (orbitNormal.lengthSq() < 1e-8) orbitNormal.set(1, 0, 0);
  orbitNormal.normalize();
  const cosLat = Math.sqrt(Math.max(0, 1 - north.dot(aoiDir) ** 2));
  const tilt = Math.asin(THREE.MathUtils.clamp(Math.cos(THREE.MathUtils.degToRad(98)) / (cosLat || 1), -1, 1));
  orbitNormal.applyAxisAngle(aoiDir, tilt);
  const orbitSide = new THREE.Vector3().crossVectors(orbitNormal, aoiDir).normalize();

  const orbitPoint = (angle) => aoiDir.clone().multiplyScalar(Math.cos(angle) * ORBIT_RADIUS)
    .addScaledVector(orbitSide, Math.sin(angle) * ORBIT_RADIUS);

  const ringPositions = [];
  const ringSteps = 160;
  for (let i = 0; i <= ringSteps; i += 1) ringPositions.push(...orbitPoint((i / ringSteps) * Math.PI * 2).toArray());
  const orbitGeom = new THREE.BufferGeometry();
  orbitGeom.setAttribute("position", new THREE.Float32BufferAttribute(ringPositions, 3));
  const orbitLine = new THREE.Line(
    orbitGeom,
    new THREE.LineBasicMaterial({ color: 0x9ec9ff, transparent: true, opacity: 0.25 }),
  );
  earth.add(orbitLine);

  const satellite = new THREE.Mesh(
    new THREE.SphereGeometry(0.014, 12, 12),
    new THREE.MeshBasicMaterial({ color: 0xe8f6ff }),
  );
  earth.add(satellite);

  const arcPositions = new Float32Array(49 * 3);
  const arcGeom = new THREE.BufferGeometry();
  arcGeom.setAttribute("position", new THREE.BufferAttribute(arcPositions, 3));
  const arcMat = new THREE.LineBasicMaterial({ color: 0xb7dcff, transparent: true, opacity: 0 });
  const arcLine = new THREE.Line(arcGeom, arcMat);
  arcLine.frustumCulled = false;
  arcLine.visible = false;
  const glow = new THREE.Mesh(
    new THREE.SphereGeometry(0.018, 12, 12),
    new THREE.MeshBasicMaterial({ color: 0xffffff, transparent: true, opacity: 0 }),
  );
  glow.visible = false;
  earth.add(arcLine, glow);

  const starPositions = new Float32Array(2500 * 3);
  for (let i = 0; i < 2500; i += 1) {
    const point = new THREE.Vector3().randomDirection().multiplyScalar(40 + Math.random() * 20);
    starPositions.set([point.x, point.y, point.z], i * 3);
  }
  const starGeom = new THREE.BufferGeometry();
  starGeom.setAttribute("position", new THREE.BufferAttribute(starPositions, 3));
  scene.add(new THREE.Points(starGeom, new THREE.PointsMaterial({
    color: 0xffffff,
    size: 0.045,
    sizeAttenuation: true,
  })));

  const resize = () => {
    const width = Math.max(1, container.clientWidth);
    const height = Math.max(1, container.clientHeight);
    camera.aspect = width / height;
    camera.updateProjectionMatrix();
    renderer.setSize(width, height, false);
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
  };

  function beginFly(mode) {
    controls.enabled = false;
    fly.active = true;
    fly.mode = mode;
    fly.start = elapsed;
    fly.dur = reduced ? 0 : (mode === "in" ? 1.8 : 1.2);
    fly.yaw0 = earth.rotation.y;
    fly.cam0.copy(camera.position);
    if (mode === "in") {
      const targetYaw = shortestYaw(earth.rotation.y, yawFacing(aoiDir));
      fly.yaw1 = targetYaw;
      const aimed = aoiDir.clone().applyAxisAngle(new THREE.Vector3(0, 1, 0), targetYaw);
      fly.cam1.copy(aimed.normalize()).multiplyScalar(1.25);
    } else {
      fly.yaw1 = shortestYaw(earth.rotation.y, 0);
      fly.cam1.copy(HOME);
    }
    if (reduced) finishFly();
  }

  function finishFly() {
    earth.rotation.y = fly.yaw1;
    camera.position.copy(fly.cam1);
    camera.lookAt(0, 0, 0);
    controls.target.set(0, 0, 0);
    controls.update();
    fly.active = false;
    controls.enabled = fly.mode !== "in";
    if (fly.mode === "in" && onEnter) onEnter();
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

  function updateArc(satPos) {
    const separation = satPos.clone().normalize().angleTo(aoiDir);
    if (separation > ARC_ANGLE) passLatched = false;
    if (separation < ARC_ANGLE && !passLatched && !arc.active) {
      passLatched = true;
      arc.active = true;
      arc.start = elapsed;
    }
    if (!arc.active) {
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

  function frame(now) {
    if (!running) return;
    raf = requestAnimationFrame(frame);
    const delta = Math.min(0.05, (now - last) / 1000);
    last = now;
    elapsed += delta;
    if (fly.active) stepFly();
    else if (!dragging) earth.rotation.y += delta * (reduced ? 0.004 : 0.05);
    const angle = (elapsed / ORBIT_PERIOD) * Math.PI * 2 - 0.8;
    const satPos = orbitPoint(angle);
    satellite.position.copy(satPos);
    updateArc(satPos);
    const pulse = (elapsed % 2) / 2;
    ring.scale.setScalar(1 + pulse * 2.4);
    ring.material.opacity = 0.85 * (1 - pulse);
    if (!fly.active) controls.update();
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
      beginFly("in");
    },
    flyOut() {
      controls.enabled = true;
      beginFly("out");
    },
    dispose() {
      stop();
      observer.disconnect();
      controls.dispose();
      earth.traverse((obj) => {
        obj.geometry?.dispose();
        if (obj.material) disposeMaterial(obj.material);
      });
      starGeom.dispose();
      textures.forEach((texture) => texture.dispose());
      renderer.dispose();
      renderer.domElement.remove();
    },
  };
}
