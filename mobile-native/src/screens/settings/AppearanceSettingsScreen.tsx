import { useCallback } from "react";
import { StyleSheet, Text, View } from "react-native";
import { SettingsHeader, SettingsSection, SettingsShell } from "../../settings/components/SettingsShell";
import {
  SettingsRow,
  SettingsSlider,
  SettingsSwitch
} from "../../settings/components/SettingsControls";
import { usePreferenceGroup } from "../../settings/store";
import { FONT_SCALE_MAX, FONT_SCALE_MIN, FONT_SCALE_STEP, ThemeMode } from "../../settings/schema";
import { useTheme } from "../../theme/ThemeContext";

const THEME_OPTIONS = [
  {
    value: "system" as ThemeMode,
    label: "Match device",
    description: "Follow your phone's light or dark setting.",
    icon: "phone-portrait-outline" as const,
    disabled: true
  },
  {
    value: "dark" as ThemeMode,
    label: "Dark",
    description: "PulseSoc's signature dark surface with the full galactic background.",
    icon: "moon-outline" as const,
    disabled: false
  },
  {
    value: "light_futuristic" as ThemeMode,
    label: "Light Futuristic",
    description: "Bright glassy surfaces with a faint atmosphere.",
    icon: "sunny-outline" as const,
    disabled: true
  },
  {
    value: "black" as ThemeMode,
    label: "Black",
    description: "True black for OLED screens. Dimmed atmosphere, deeper contrast.",
    icon: "contrast-outline" as const,
    disabled: true
  },
  {
    value: "white" as ThemeMode,
    label: "White",
    description: "Plain white surfaces, no background effects. Maximum clarity.",
    icon: "square-outline" as const,
    disabled: true
  }
];

const THEME_LABELS: Record<ThemeMode, string> = {
  system: "Match device",
  dark: "Dark",
  light_futuristic: "Light Futuristic",
  black: "Black",
  white: "White"
};

/**
 * Appearance.
 *
 * Every control here is applied by `ThemeProvider` the moment it changes — the
 * preview card below re-renders live, so the user sees the result before they
 * leave the screen rather than after an app restart.
 */
export function AppearanceSettingsScreen() {
  const theme = useTheme();
  const { value, setGroup, pending } = usePreferenceGroup("appearance");

  const setTheme = useCallback((next: ThemeMode) => setGroup({ theme: next }), [setGroup]);
  const setFontScale = useCallback((next: number) => setGroup({ fontScale: next }), [setGroup]);

  return (
    <SettingsShell bottomDock={false}>
      <SettingsHeader title="Appearance" subtitle="Changes apply instantly across PulseSoc." />

      <SettingsSection title="Background & Theme" busy={pending}>
        {THEME_OPTIONS.map((option) => {
          const selected = option.value === "dark";

          return (
            <SettingsRow
              key={option.value}
              testID={`appearance-theme-${option.value}`}
              title={option.label}
              subtitle={option.description}
              icon={option.icon}
              disabled={option.disabled}
              accessibilityRole="button"
              accessibilityState={{
                selected,
                disabled: option.disabled
              }}
              onPress={option.disabled ? undefined : () => setTheme("dark")}
              accessory={
                option.disabled ? (
                  <Text
                    style={{
                      color: theme.colors.muted,
                      fontSize: theme.scaleFont(10),
                      fontWeight: "800"
                    }}
                  >
                    COMING SOON
                  </Text>
                ) : (
                  <Text
                    style={{
                      color: theme.colors.accent,
                      fontSize: theme.scaleFont(12),
                      fontWeight: "800"
                    }}
                  >
                    ACTIVE
                  </Text>
                )
              }
            />
          );
        })}
      </SettingsSection>

      <SettingsSection
        title="Text size"
        footnote="PulseSoc also respects the text size set in your device's accessibility settings."
      >
        <SettingsSlider
          testID="appearance-font-scale"
          title="Scale"
          subtitle="Affects body text, labels, and controls throughout the app."
          value={value.fontScale}
          minimumValue={FONT_SCALE_MIN}
          maximumValue={FONT_SCALE_MAX}
          step={FONT_SCALE_STEP}
          onChange={setFontScale}
          formatValue={(scale) => `${Math.round(scale * 100)}%`}
        />
      </SettingsSection>

      {/* Live preview — the fastest way to communicate what the sliders do. */}
      <SettingsSection title="Preview">
        <View style={[styles.preview, { padding: theme.metrics.rowPaddingHorizontal }]}>
          <Text style={{ color: theme.colors.text, fontSize: theme.scaleFont(17), fontWeight: theme.metrics.titleWeight }}>
            Aurora posted a new reel
          </Text>
          <Text
            style={{
              color: theme.colors.muted,
              fontSize: theme.scaleFont(14),
              lineHeight: theme.scaleFont(20),
              marginTop: 4
            }}
          >
            This is how post text, captions, and descriptions will look with your current settings.
          </Text>
          <View style={[styles.previewChip, { backgroundColor: theme.colors.signalDim, borderColor: theme.colors.accent }]}>
            <Text style={{ color: theme.colors.accent, fontSize: theme.scaleFont(12), fontWeight: "800" }}>
              {THEME_LABELS[theme.mode]} · {Math.round(value.fontScale * 100)}%
            </Text>
          </View>
        </View>
      </SettingsSection>

      <SettingsSection title="Display">
        <SettingsSwitch
          testID="appearance-compact"
          title="Compact density"
          subtitle="Tighter rows and spacing. Fits more on screen."
          icon="contract-outline"
          value={value.compactDensity}
          onValueChange={(next) => setGroup({ compactDensity: next })}
        />
        <SettingsSwitch
          testID="appearance-reduce-transparency"
          title="Reduce transparency"
          subtitle="Replace translucent panels with solid backgrounds. Can improve readability and battery life."
          icon="layers-outline"
          value={value.reduceTransparency}
          onValueChange={(next) => setGroup({ reduceTransparency: next })}
        />
      </SettingsSection>
    </SettingsShell>
  );
}

const styles = StyleSheet.create({
  preview: { width: "100%" },
  previewChip: {
    alignSelf: "flex-start",
    borderRadius: 6,
    borderWidth: StyleSheet.hairlineWidth,
    marginTop: 12,
    paddingHorizontal: 8,
    paddingVertical: 4
  }
});
