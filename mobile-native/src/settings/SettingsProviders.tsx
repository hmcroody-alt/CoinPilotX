/**
 * The seam between stored preferences and the running app's theme.
 *
 * `ThemeProvider` deliberately takes `appearance` and `accessibility` as plain
 * props rather than reading the preference store itself — that keeps the theme
 * layer testable in isolation and free of any dependency on persistence or
 * network sync. The cost of that decision is that something has to carry the
 * values across, and this file is that something. It is the only place in the
 * app where the two systems know about each other.
 *
 * Mount it once, high in the tree. Every settings screen calls both
 * `usePreferenceGroup` and `useTheme`, so both providers have to be above the
 * navigator, not inside it.
 */

import { ReactNode } from "react";
import { PreferencesProvider, usePreferences } from "./store";
import { ThemeProvider } from "../theme/ThemeContext";

/**
 * Reads the two theme-relevant groups and hands them to `ThemeProvider`.
 *
 * Split into its own component rather than inlined into `SettingsProviders`
 * because a hook that reads the preference context cannot run in the same
 * component that renders the provider supplying it.
 */
function ThemeFromPreferences({ children }: { children: ReactNode }) {
  const { preferences } = usePreferences();
  return (
    <ThemeProvider appearance={preferences.appearance} accessibility={preferences.accessibility}>
      {children}
    </ThemeProvider>
  );
}

/**
 * `enabled` gates only the *network* half of the store: when false the provider
 * still hydrates from disk and still applies local changes, it just doesn't try
 * to sync. That matters for the signed-out shell — a user reading the legal
 * documents before they have an account should still get their chosen theme and
 * text size, and should not generate 401s doing it.
 */
export function SettingsProviders({ children, syncEnabled = true }: { children: ReactNode; syncEnabled?: boolean }) {
  return (
    <PreferencesProvider enabled={syncEnabled}>
      <ThemeFromPreferences>{children}</ThemeFromPreferences>
    </PreferencesProvider>
  );
}
