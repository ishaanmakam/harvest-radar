/**
 * satellite.js can also run SGP4 in WebAssembly (Emscripten builds behind the package's
 * "#wasm-single-thread" and "#wasm-multi-thread" imports). The globe uses the plain JavaScript
 * propagate(), which handles 800 Starlink objects in about a millisecond, so vite.config.js aliases
 * those imports to this stub and the Emscripten code stays out of the bundle.
 */
export default async function createWasmModule() {
  throw new Error("satellite.js WebAssembly build is not bundled here; use propagate() instead");
}
