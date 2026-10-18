import * as THREE from "three";

const FALLBACK_BOUNDS = [-93.52, 41.98, -93.42, 42.07];

export function latLngToVector3(lat, lng, radius) {
  const phi = (90 - lat) * (Math.PI / 180);
  const theta = (lng + 180) * (Math.PI / 180);
  return new THREE.Vector3(
    -radius * Math.sin(phi) * Math.cos(theta),
    radius * Math.cos(phi),
    radius * Math.sin(phi) * Math.sin(theta),
  );
}

export function aoiCenter(bounds) {
  const box = bounds && bounds.length === 4 ? bounds : FALLBACK_BOUNDS;
  const [west, south, east, north] = box;
  return { lat: (south + north) / 2, lng: (west + east) / 2 };
}
