import { useCallback, useMemo, useRef, useState } from "react";
import { Alert, Platform, StyleSheet, Text, View } from "react-native";
import Constants from "expo-constants";
import * as Device from "expo-device";
import { SettingsHeader, SettingsSection, SettingsShell } from "../../settings/components/SettingsShell";
import { SettingsBadge, SettingsRow, SettingsValue } from "../../settings/components/SettingsControls";
import { usePreferenceGroup } from "../../settings/store";
import { APP_VERSION, PULSE_API_BASE_URL } from "../../api/config";
import { useTheme } from "../../theme/ThemeContext";
import { OPEN_SOURCE_DEPENDENCIES } from "./legalContent";

/** Taps on the version row needed to unlock Developer options. */
const UNLOCK_TAPS = 7;
/** Taps reset if the user pauses — a slow triple-tap should not accumulate. */
const UNLOCK_WINDOW_MS = 2500;
/** Start counting down out loud with this many taps left (Android convention). */
const COUNTDOWN_FROM = 3;

/* -------------------------------------------------------------------------- */
/*                              Build identity                                 */
/* -------------------------------------------------------------------------- */

function resolveVersion(): string {
  // APP_VERSION is the configured marketing version; expoConfig is the same
  // value at runtime and is the one that survives an OTA update.
  return APP_VERSION || Constants.expoConfig?.version || "unknown";
}

function resolveBuildNumber(): string {
  const config = Constants.expoConfig;
  if (Platform.OS === "ios") {
    const build = config?.ios?.buildNumber;
    return build ? String(build) : "—";
  }
  if (Platform.OS === "android") {
    const code = config?.android?.versionCode;
    return typeof code === "number" ? String(code) : "—";
  }
  return "—";
}

/**
 * Classify the API host so QA can tell at a glance which backend a build is
 * pointed at — the single most common cause of "it works on my device".
 */
function describeEnvironment(baseUrl: string): { label: string; host: string; tone: "accent" | "warning" | "muted" } {
  const host = baseUrl.replace(/^https?:\/\//i, "");
  if (/^(127\.0\.0\.1|localhost)(:\d+)?$/i.test(host)) return { label: "Local", host, tone: "warning" };
  if (/^pulsesoc\.com$/i.test(host)) return { label: "Production", host, tone: "accent" };
  if (/staging|qa|dev/i.test(host)) return { label: "Staging", host, tone: "warning" };
  return { label: "Custom", host, tone: "muted" };
}

function describeDeviceType(): string {
  switch (Device.deviceType) {
    case Device.DeviceType.PHONE:
      return "Phone";
    case Device.DeviceType.TABLET:
      return "Tablet";
    case Device.DeviceType.DESKTOP:
      return "Desktop";
    case Device.DeviceType.TV:
      return "TV";
    default:
      return "Unknown";
  }
}

/* -------------------------------------------------------------------------- */
/*                                   Screen                                    */
/* -------------------------------------------------------------------------- */

/**
 * About.
 *
 * Two jobs: tell the user (and a support agent reading over their shoulder)
 * exactly which build they are running, and credit the open-source work the app
 * is built on. It also holds the version-tap gesture that unlocks Developer
 * options — the only way the Developer screen becomes reachable.
 */
export function AboutSettingsScreen() {
  const theme = useTheme();
  const { value: developer, setGroup } = usePreferenceGroup("developer");

  const tapCount = useRef(0);
  const lastTapAt = useRef(0);
  const [hint, setHint] = useState("");

  const version = useMemo(resolveVersion, []);
  const buildNumber = useMemo(resolveBuildNumber, []);
  const environment = useMemo(() => describeEnvironment(PULSE_API_BASE_URL), []);

  const handleVersionTap = useCallback(() => {
    if (developer.enabled) {
      Alert.alert("Developer options", "Developer options are already on. You'll find them at the bottom of Settings.");
      return;
    }

    const now = Date.now();
    tapCount.current = now - lastTapAt.current > UNLOCK_WINDOW_MS ? 1 : tapCount.current + 1;
    lastTapAt.current = now;

    const remaining = UNLOCK_TAPS - tapCount.current;

    if (remaining <= 0) {
      tapCount.current = 0;
      setHint("");
      // Writing through the store is what actually unlocks the screen: the
      // Settings index and the navigator both gate the Developer entry on
      // `developer.enabled`, and the store persists and syncs it.
      void setGroup({ enabled: true });
      Alert.alert(
        "You are now a developer",
        "Developer options have been added to the bottom of Settings. You can turn them off again from there at any time."
      );
      return;
    }

    setHint(remaining <= COUNTDOWN_FROM ? `${remaining} more ${remaining === 1 ? "tap" : "taps"} to enable Developer options.` : "");
  }, [developer.enabled, setGroup]);

  return (
    <SettingsShell bottomDock={false}>
      <SettingsHeader title="About" subtitle="Build details, device information, and the software PulseSoc is built on." />

      <SettingsSection title="Application" footnote={hint || undefined}>
        <SettingsRow
          testID="about-version"
          title="Version"
          subtitle={hint || "Tap to see build details."}
          icon="information-circle-outline"
          accessibilityRole="button"
          accessibilityHint="Tapping repeatedly enables Developer options."
          onPress={handleVersionTap}
          accessory={
            <SettingsValue>
              {version} ({buildNumber})
            </SettingsValue>
          }
        />
        <SettingsRow
          testID="about-runtime"
          title="Runtime"
          subtitle="How this build was launched."
          icon="cube-outline"
          accessory={<SettingsValue>{Constants.executionEnvironment}</SettingsValue>}
        />
        <SettingsRow
          testID="about-environment"
          title="API environment"
          subtitle={environment.host}
          icon="server-outline"
          accessory={<SettingsBadge label={environment.label} tone={environment.tone} />}
        />
        {developer.enabled ? (
          <SettingsRow
            testID="about-developer-enabled"
            title="Developer options"
            subtitle="Enabled on this device. Turn them off from Settings › Developer."
            icon="construct-outline"
            accessory={<SettingsBadge label="On" tone="warning" />}
          />
        ) : null}
      </SettingsSection>

      <SettingsSection title="Device" description="Attached automatically to anything you report from Settings › Help.">
        <SettingsRow
          testID="about-device-model"
          title="Model"
          icon="phone-portrait-outline"
          accessory={<SettingsValue>{Device.modelName || "Unknown"}</SettingsValue>}
        />
        <SettingsRow
          testID="about-device-os"
          title="Operating system"
          icon="hardware-chip-outline"
          accessory={
            <SettingsValue>
              {Device.osName || Platform.OS} {Device.osVersion || String(Platform.Version)}
            </SettingsValue>
          }
        />
        <SettingsRow
          testID="about-device-type"
          title="Device type"
          subtitle={Device.isDevice ? undefined : "Simulator or emulator"}
          icon="tablet-landscape-outline"
          accessory={<SettingsValue>{describeDeviceType()}</SettingsValue>}
        />
      </SettingsSection>

      <SettingsSection
        title="Acknowledgements"
        description="PulseSoc is built on open-source software. Copyright remains with each project's authors."
        footnote="Full licence texts are in Settings › Legal › Open-source licences."
      >
        {OPEN_SOURCE_DEPENDENCIES.map((dependency) => (
          <View
            key={dependency.name}
            style={[
              styles.credit,
              { paddingHorizontal: theme.metrics.rowPaddingHorizontal, paddingVertical: theme.metrics.rowPaddingVertical }
            ]}
          >
            <View style={styles.creditBody}>
              <Text style={{ color: theme.colors.text, fontSize: theme.scaleFont(15), fontWeight: "700" }}>
                {dependency.name}
              </Text>
              <Text style={{ color: theme.colors.muted, fontSize: theme.scaleFont(12), lineHeight: theme.scaleFont(17), marginTop: 2 }}>
                {dependency.version} · {dependency.purpose}
              </Text>
            </View>
            <SettingsBadge label={dependency.license} tone="muted" />
          </View>
        ))}
      </SettingsSection>
    </SettingsShell>
  );
}

const styles = StyleSheet.create({
  credit: { alignItems: "center", flexDirection: "row", gap: 12 },
  creditBody: { flex: 1 }
});
