/**
 * Safe, lazy access to expo-sensors' DeviceMotion.
 *
 * expo-sensors is declared in package.json but may be absent from a build
 * (or the platform may lack the sensor). Everything degrades to the
 * "unavailable" motion state — swipe navigation is never affected.
 * Loaded via dynamic require inside try/catch so bundlers and jest don't
 * hard-fail when the module is missing.
 */

export interface DeviceMotionMeasurement {
  rotation?: { alpha: number; beta: number; gamma: number } | null;
  accelerationIncludingGravity?: { x: number; y: number; z: number } | null;
}

export interface DeviceMotionModule {
  isAvailableAsync(): Promise<boolean>;
  setUpdateInterval(intervalMs: number): void;
  addListener(listener: (m: DeviceMotionMeasurement) => void): { remove(): void };
  requestPermissionsAsync?: () => Promise<{ granted: boolean }>;
}

let cached: DeviceMotionModule | null | undefined;

export function getDeviceMotionModule(): DeviceMotionModule | null {
  if (cached !== undefined) return cached;
  try {
    // eslint-disable-next-line @typescript-eslint/no-var-requires
    const sensors = require("expo-sensors");
    cached = (sensors?.DeviceMotion as DeviceMotionModule) ?? null;
  } catch {
    cached = null;
  }
  return cached;
}

/** True when the module exists AND the platform reports a live sensor. */
export async function isMotionAvailable(): Promise<boolean> {
  const deviceMotion = getDeviceMotionModule();
  if (!deviceMotion) return false;
  try {
    if (deviceMotion.requestPermissionsAsync) {
      const { granted } = await deviceMotion.requestPermissionsAsync();
      if (!granted) return false;
    }
    return await deviceMotion.isAvailableAsync();
  } catch {
    return false;
  }
}

/** Test-only override. */
export function __setDeviceMotionModuleForTests(module: DeviceMotionModule | null | undefined) {
  cached = module;
}
