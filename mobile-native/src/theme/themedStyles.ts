/**
 * Live-rebuilding replacement for module-scope `StyleSheet.create`.
 *
 * Problem: ~100 legacy files build their StyleSheet at module scope from the
 * shared mutable `colors` object. Module scope runs once — before the
 * ThemeProvider publishes the user's palette — so those sheets freeze the
 * default dark values and never follow a theme change.
 *
 * `createThemedStyles(factory)` keeps the exact same call-site shape but defers
 * the build until first property access and rebuilds whenever the palette
 * epoch advances (bumped by `applyPaletteToLegacyColors`). Components re-read
 * `styles.foo` on every render, so the next render after a theme change gets
 * the fresh palette without any per-file rewrite.
 *
 * This is a migration bridge, like the mutable `colors` object itself. New
 * code should use `useThemedStyles(theme => ...)` from ThemeContext instead.
 */

import { StyleSheet } from "react-native";

let paletteEpoch = 0;

/** Called by the theme provider after it mutates the legacy `colors` object. */
export function bumpThemedStylesEpoch(): void {
  paletteEpoch += 1;
}

export function createThemedStyles<T extends StyleSheet.NamedStyles<T> | StyleSheet.NamedStyles<any>>(
  factory: () => T & StyleSheet.NamedStyles<any>
): T {
  let built: T | null = null;
  let builtEpoch = -1;

  const resolve = (): T => {
    if (built === null || builtEpoch !== paletteEpoch) {
      built = StyleSheet.create(factory());
      builtEpoch = paletteEpoch;
    }
    return built;
  };

  return new Proxy({} as T, {
    get: (_target, key) => (resolve() as Record<PropertyKey, unknown>)[key],
    has: (_target, key) => key in (resolve() as object),
    ownKeys: () => Reflect.ownKeys(resolve() as object),
    getOwnPropertyDescriptor: (_target, key) => {
      const descriptor = Reflect.getOwnPropertyDescriptor(resolve() as object, key);
      return descriptor ? { ...descriptor, configurable: true } : undefined;
    }
  });
}
