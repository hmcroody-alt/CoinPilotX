import { useCallback, useEffect, useState } from "react";
import { AccessibilityInfo, Platform } from "react-native";
import { SettingsHeader, SettingsSection, SettingsShell } from "../../settings/components/SettingsShell";
import { SettingsBadge, SettingsRow, SettingsSwitch } from "../../settings/components/SettingsControls";
import { usePreferenceGroup } from "../../settings/store";
import { useTheme } from "../../theme/ThemeContext";

/** The OS screen reader is named differently per platform; using the wrong name reads as a bug. */
const SCREEN_READER_NAME = Platform.select({ ios: "VoiceOver", android: "TalkBack", default: "Screen reader" });

/** `null` while the first query is in flight, `"unknown"` when the platform refuses to answer. */
type ScreenReaderState = boolean | null | "unknown";

/**
 * Accessibility.
 *
 * These preferences are consumed by `ThemeProvider` (bold text, high contrast,
 * reduce motion) and by the control layer (haptics, hints), so every switch here
 * changes the app while the user is still looking at this screen.
 *
 * The app keeps its own copies rather than deferring to the OS flags: PulseSoc
 * runs on devices where the system toggles are unavailable or shared with apps
 * the user wants behaving differently, and an in-app value is the only thing we
 * can honour reliably. The OS state is surfaced read-only at the bottom so the
 * two are never confused for each other.
 */
export function AccessibilitySettingsScreen() {
  const theme = useTheme();
  const { value, setGroup, pending } = usePreferenceGroup("accessibility");
  const [screenReader, setScreenReader] = useState<ScreenReaderState>(null);

  useEffect(() => {
    let cancelled = false;

    AccessibilityInfo.isScreenReaderEnabled()
      .then((enabled) => {
        if (!cancelled) setScreenReader(enabled);
      })
      .catch(() => {
        // Web and some Android OEM builds reject instead of resolving false.
        // Reporting "unknown" is honest; reporting "off" would be a lie.
        if (!cancelled) setScreenReader("unknown");
      });

    const subscription = AccessibilityInfo.addEventListener("screenReaderChanged", (enabled) => {
      if (!cancelled) setScreenReader(enabled);
    });

    return () => {
      cancelled = true;
      subscription.remove();
    };
  }, []);

  const set = useCallback(
    (patch: Partial<typeof value>) => {
      void setGroup(patch);
    },
    [setGroup]
  );

  const screenReaderOn = screenReader === true;
  const screenReaderStatus =
    screenReader === null ? "Checking…" : screenReader === "unknown" ? "Unavailable" : screenReaderOn ? "On" : "Off";

  return (
    <SettingsShell bottomDock={false}>
      <SettingsHeader
        title="Accessibility"
        subtitle="PulseSoc's own accessibility settings. They apply immediately and stay in sync across your devices."
      />

      <SettingsSection
        title="Vision"
        description="Make text and edges easier to pick out."
        busy={pending}
      >
        <SettingsSwitch
          testID="accessibility-bold-text"
          title="Bold text"
          subtitle="Thicker strokes on names, titles, and body copy. Helps when thin type disappears against a busy feed."
          icon="text-outline"
          value={value.boldText}
          onValueChange={(next) => set({ boldText: next })}
        />
        <SettingsSwitch
          testID="accessibility-high-contrast"
          title="Increase contrast"
          subtitle="Pushes text to pure black or white, darkens separators, and removes translucent panels."
          icon="contrast-outline"
          value={value.highContrast}
          onValueChange={(next) => set({ highContrast: next })}
        />
      </SettingsSection>

      <SettingsSection
        title="Motion"
        footnote="Reduce motion also shortens screen transitions to an instant cut, so nothing slides while you read."
      >
        <SettingsSwitch
          testID="accessibility-reduce-motion"
          title="Reduce motion"
          subtitle="Removes parallax, auto-playing transitions, and spring animations that can trigger nausea or dizziness."
          icon="pause-circle-outline"
          value={value.reduceMotion}
          onValueChange={(next) => set({ reduceMotion: next })}
        />
      </SettingsSection>

      <SettingsSection title="Media">
        <SettingsSwitch
          testID="accessibility-captions"
          title="Captions"
          subtitle="Shows subtitles on reels, live video, and voice notes whenever the creator supplied them."
          icon="chatbox-ellipses-outline"
          value={value.captionsEnabled}
          onValueChange={(next) => set({ captionsEnabled: next })}
        />
      </SettingsSection>

      <SettingsSection title="Interaction">
        <SettingsSwitch
          testID="accessibility-haptics"
          title="Haptic feedback"
          subtitle="A short tap confirms toggles, selections, and sends. Turn off to save battery or if vibration is uncomfortable."
          icon="phone-portrait-outline"
          value={value.hapticFeedback}
          onValueChange={(next) => set({ hapticFeedback: next })}
        />
        <SettingsSwitch
          testID="accessibility-screen-reader-hints"
          title="Extra spoken hints"
          subtitle={`Adds a sentence explaining what each control does before ${SCREEN_READER_NAME} reads it. Slower, but far less guessing.`}
          icon="megaphone-outline"
          value={value.screenReaderHints}
          onValueChange={(next) => set({ screenReaderHints: next })}
        />
      </SettingsSection>

      <SettingsSection
        title="System"
        description="Read from your device. Change it in your phone's own accessibility settings."
        footnote={
          screenReaderOn && !value.screenReaderHints
            ? `${SCREEN_READER_NAME} is running but extra spoken hints are off. Turning them on above gives you more context on each control.`
            : `Updates automatically if you switch ${SCREEN_READER_NAME} on or off while PulseSoc is open.`
        }
      >
        <SettingsRow
          testID="accessibility-screen-reader-status"
          title={SCREEN_READER_NAME}
          // The badge is decorative for assistive tech, so the state is repeated
          // in the subtitle — that is what actually gets read out.
          subtitle={
            screenReader === null
              ? "Checking whether it's running…"
              : screenReader === "unknown"
                ? "Unavailable — this device didn't report its screen reader state."
                : screenReaderOn
                  ? "On — PulseSoc is announcing screens and controls as you move through them."
                  : "Off — PulseSoc still ships full labels, so turning it on works immediately."
          }
          icon="accessibility-outline"
          iconColor={screenReaderOn ? theme.colors.accent : theme.colors.muted}
          accessibilityRole="none"
          accessory={
            <SettingsBadge
              label={screenReaderStatus}
              tone={screenReaderOn ? "accent" : screenReader === "unknown" ? "warning" : "muted"}
            />
          }
        />
      </SettingsSection>
    </SettingsShell>
  );
}
