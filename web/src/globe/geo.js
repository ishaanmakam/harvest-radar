import * as THREE from "three";
import { gstime } from "satellite.js";

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

export function latLngFromVector3(vector) {
  const radius = vector.length() || 1;
  const phi = Math.acos(THREE.MathUtils.clamp(vector.y / radius, -1, 1));
  const lat = 90 - (phi * 180) / Math.PI;
  const lng = ((Math.atan2(vector.z, -vector.x) * 180) / Math.PI - 180 + 540) % 360 - 180;
  return { lat, lng };
}

/**
 * Earth-centred, Earth-fixed (x toward 0°, y toward 90° E, z north, in Earth radii) to the frame
 * latLngToVector3 uses: the same mapping, written out so satellite positions skip the trig.
 */
export function localFromEcf(x, y, z, out = new THREE.Vector3()) {
  return out.set(x, z, -y);
}

/**
 * The point on Earth where the sun is overhead at a UTC date: declination and right ascension
 * from the Astronomical Almanac low-precision formulae (about 0.01° over this century), and
 * Greenwich sidereal time from satellite.js. Good to a few km, which is all the terminator needs.
 */
export function subsolarPoint(date) {
  const n = date.getTime() / 86400000 + 2440587.5 - 2451545.0; // days since J2000.0
  const rad = Math.PI / 180;
  const meanLongitude = (280.46 + 0.9856474 * n) % 360;
  const meanAnomaly = ((357.528 + 0.9856003 * n) % 360) * rad;
  const eclipticLongitude = (meanLongitude + 1.915 * Math.sin(meanAnomaly) + 0.02 * Math.sin(2 * meanAnomaly)) * rad;
  const obliquity = (23.439 - 0.0000004 * n) * rad;
  const rightAscension = Math.atan2(Math.cos(obliquity) * Math.sin(eclipticLongitude), Math.cos(eclipticLongitude));
  const declination = Math.asin(Math.sin(obliquity) * Math.sin(eclipticLongitude));
  let lng = ((rightAscension - gstime(date)) / rad) % 360;
  if (lng > 180) lng -= 360;
  if (lng < -180) lng += 360;
  return { lat: declination / rad, lng };
}

export function aoiCenter(bounds) {
  const box = bounds && bounds.length === 4 ? bounds : FALLBACK_BOUNDS;
  const [west, south, east, north] = box;
  return { lat: (south + north) / 2, lng: (west + east) / 2 };
}
