import { useCallback, useEffect, useState } from "react";
import { AccessibilityInfo, Platform } from "react-native";
import { SettingsHeader, SettingsSection, SettingsShell } from "../../settings/components/SettingsShell";
import { SettingsBadge, SettingsButton, SettingsRow, SettingsSelect, SettingsSwitch } from "../../settings/components/SettingsControls";
import { usePreferenceGroup } from "../../settings/store";
import { useTheme } from "../../theme/ThemeContext";
import { spatialMotionEnabled } from "../../spatial/flags";
import { isMotionAvailable } from "../../spatial/motion/motionAvailability";
import { MotionOnboarding } from "../../spatial/motion/MotionOnboarding";
import { hydrateMotionSettings, updateMotionSettings, useMotionSettings } from "../../spatial/motion/motionSettings";
import type { MotionMode, MotionSensitivity } from "../../spatial/motion/motionStateMachine";

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

      {spatialMotionEnabled() ? <SpatialMotionSection /> : null}

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

/**
 * Settings → Accessibility → Spatial Motion (mission §21).
 *
 * Rendered only while the motion flags are on, so with flags off this file is
 * behaviorally identical to legacy. Everything here writes to the motion
 * settings store that `useTiltNavigation` reads; nothing touches sensors
 * directly. Enabling a motion mode for the first time routes through the
 * onboarding flow — motion never activates from a bare toggle.
 *
 * Reels is the only destination these settings govern. An earlier build let the
 * user choose between Feed, Reels or both; Home Feed motion was withdrawn as a
 * product decision, so the choice no longer exists and the copy names Reels
 * explicitly rather than saying "pages". Devices still holding a scope value
 * are migrated on read — see `LegacyMotionScope` in motionSettings.
 */
function SpatialMotionSection() {
  const settings = useMotionSettings();
  const [tutorialOpen, setTutorialOpen] = useState(false);
  const [sensorAvailable, setSensorAvailable] = useState<boolean | null>(null);

  useEffect(() => {
    hydrateMotionSettings().catch(() => undefined);
    let cancelled = false;
    isMotionAvailable().then((available) => {
      if (!cancelled) setSensorAvailable(available);
    });
    return () => {
      cancelled = true;
    };
  }, []);

  const motionOn = settings.mode !== "swipe-only";

  return (
    <>
      <SettingsSection
        title="Spatial Motion"
        description="Optional phone-tilt navigation for Reels. Swiping always works — tilt is never required."
        footnote={
          sensorAvailable === false
            ? "This device did not report a motion sensor, so tilt and parallax are unavailable here. Your choices are saved for devices that support them."
            : "Motion data is processed on this device only and is never stored or transmitted."
        }
      >
        {!settings.onboarded ? (
          <SettingsRow
            testID="spatial-motion-setup"
            title="Set up Spatial Motion"
            subtitle="A short tour: what tilt does, your privacy, and which mode fits you. Nothing turns on until you choose."
            icon="compass-outline"
            chevron
            onPress={() => setTutorialOpen(true)}
            accessory={<SettingsBadge label="New" tone="accent" />}
          />
        ) : (
          <>
            <SettingsSelect<MotionMode>
              testID="spatial-motion-mode"
              value={settings.mode}
              onChange={(next) => {
                void updateMotionSettings({
                  mode: next,
                  // Re-run calibration when motion turns (back) on.
                  ...(next === "swipe-only" ? {} : { neutralBaselineRad: null })
                });
              }}
              options={[
                {
                  value: "swipe-only",
                  label: "Swipe only",
                  description: "Motion sensors stay off.",
                  icon: "hand-left-outline"
                },
                {
                  value: "parallax",
                  label: "Swipe + Parallax",
                  description: "Tilt adds a subtle depth preview. Reels never change from tilt.",
                  icon: "layers-outline"
                },
                {
                  value: "tilt",
                  label: "Swipe + Tilt",
                  description: "A sustained tilt moves to the next Reel, with a haptic tick.",
                  icon: "sync-outline"
                }
              ]}
            />
            <SettingsSelect<MotionSensitivity>
              testID="spatial-motion-sensitivity"
              value={settings.sensitivity}
              disabled={!motionOn}
              onChange={(next) => void updateMotionSettings({ sensitivity: next })}
              options={[
                { value: "low", label: "Low sensitivity", description: "Larger, longer tilt required. Steadiest." },
                { value: "medium", label: "Medium sensitivity", description: "Balanced angle and hold time." },
                { value: "high", label: "High sensitivity", description: "Smaller, quicker tilt commits." }
              ]}
            />
            <SettingsSwitch
              testID="spatial-motion-haptics"
              title="Tilt haptics"
              subtitle="A small tick confirms each tilt move between Reels."
              icon="phone-portrait-outline"
              value={settings.hapticsEnabled}
              disabled={!motionOn}
              onValueChange={(next) => void updateMotionSettings({ hapticsEnabled: next })}
            />
            <SettingsRow
              testID="spatial-motion-recalibrate"
              title="Recalibrate neutral angle"
              subtitle={
                settings.neutralBaselineRad === null
                  ? "Not calibrated yet — your holding angle is captured the next time tilt is active."
                  : "Clears the stored holding angle; it is re-captured the next time tilt is active."
              }
              icon="compass-outline"
              disabled={!motionOn}
              onPress={() => void updateMotionSettings({ neutralBaselineRad: null })}
              accessory={
                settings.neutralBaselineRad === null ? <SettingsBadge label="Pending" tone="muted" /> : undefined
              }
            />
            <SettingsButton
              testID="spatial-motion-replay-tutorial"
              label="Replay tutorial"
              variant="secondary"
              icon="play-circle-outline"
              onPress={() => setTutorialOpen(true)}
            />
          </>
        )}
      </SettingsSection>
      <MotionOnboarding visible={tutorialOpen} onClose={() => setTutorialOpen(false)} />
    </>
  );
}
