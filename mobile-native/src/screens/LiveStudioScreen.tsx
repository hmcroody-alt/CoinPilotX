import { CameraType, CameraView, useCameraPermissions, useMicrophonePermissions } from "expo-camera";
import * as Battery from "expo-battery";
import * as Device from "expo-device";
import { useNavigation } from "@react-navigation/native";
import { NativeStackNavigationProp } from "@react-navigation/native-stack";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { ActivityIndicator, Linking, Pressable, ScrollView, StyleSheet, Switch, Text, TextInput, View } from "react-native";
import { useSafeAreaInsets } from "react-native-safe-area-context";
import { PULSE_API_BASE_URL } from "../api/config";
import { startLive } from "../api/live";
import { LogiNexusBadge, LogiNexusButton, LogiNexusPanel, LogiNexusSignalIndicator } from "../components/LogiNexus";
import { RootStackParamList } from "../navigation/types";
import { colors } from "../theme/colors";
import { logiNexus, LogiNexusTone } from "../theme/logiNexus";
import {
  AUDIENCE_OPTIONS,
  computeOverallReadiness,
  emptyLiveStudioDraft,
  LIVE_TYPE_OPTIONS,
  LiveStudioDraft,
  loadLiveStudioDraft,
  mapBatteryToReadiness,
  mapDeviceToReadiness,
  mapLatencyToNetwork,
  mapPermissionToReadiness,
  ReadinessAction,
  ReadinessCheck,
  ReadinessLevel,
  readinessSummary,
  saveLiveStudioDraft
} from "../live/liveStudioReadiness";

type NetworkState = { latencyMs: number | null; probing: boolean };
type BatteryState = { level: number | null; lowPower: boolean };

const NETWORK_PROBE_TIMEOUT_MS = 6000;

async function probeNetworkLatency(): Promise<number | null> {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), NETWORK_PROBE_TIMEOUT_MS);
  const started = Date.now();
  try {
    await fetch(`${PULSE_API_BASE_URL}/`, { method: "GET", signal: controller.signal });
    return Date.now() - started;
  } catch {
    return null;
  } finally {
    clearTimeout(timeout);
  }
}

export function LiveStudioScreen() {
  const insets = useSafeAreaInsets();
  const navigation = useNavigation<NativeStackNavigationProp<RootStackParamList>>();
  const [cameraPermission, requestCameraPermission] = useCameraPermissions();
  const [microphonePermission, requestMicrophonePermission] = useMicrophonePermissions();
  const [cameraFacing, setCameraFacing] = useState<CameraType>("front");
  const [network, setNetwork] = useState<NetworkState>({ latencyMs: null, probing: true });
  const [battery, setBattery] = useState<BatteryState>({ level: null, lowPower: false });
  const [draft, setDraft] = useState<LiveStudioDraft>(emptyLiveStudioDraft());
  const [hydrated, setHydrated] = useState(false);
  const [handingOff, setHandingOff] = useState(false);
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");
  const saveTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  const runNetworkProbe = useCallback(async () => {
    setNetwork((current) => ({ ...current, probing: true }));
    const latencyMs = await probeNetworkLatency();
    setNetwork({ latencyMs, probing: false });
  }, []);

  const refreshBattery = useCallback(async () => {
    try {
      const [level, lowPower] = await Promise.all([Battery.getBatteryLevelAsync(), Battery.isLowPowerModeEnabledAsync()]);
      setBattery({ level: level >= 0 ? level : null, lowPower: Boolean(lowPower) });
    } catch {
      setBattery({ level: null, lowPower: false });
    }
  }, []);

  useEffect(() => {
    loadLiveStudioDraft()
      .then((stored) => setDraft(stored))
      .catch(() => undefined)
      .finally(() => setHydrated(true));
  }, []);

  useEffect(() => {
    runNetworkProbe().catch(() => undefined);
    refreshBattery().catch(() => undefined);
  }, [runNetworkProbe, refreshBattery]);

  useEffect(() => {
    if (!hydrated) return;
    if (saveTimer.current) clearTimeout(saveTimer.current);
    saveTimer.current = setTimeout(() => {
      saveLiveStudioDraft(draft).catch(() => undefined);
    }, 400);
    return () => {
      if (saveTimer.current) clearTimeout(saveTimer.current);
    };
  }, [draft, hydrated]);

  const network_ = useMemo(() => mapLatencyToNetwork(network.probing ? 0 : network.latencyMs), [network.latencyMs, network.probing]);

  const checks: ReadinessCheck[] = useMemo(() => {
    const cameraCheck = mapPermissionToReadiness("camera", Boolean(cameraPermission?.granted), cameraPermission?.canAskAgain !== false);
    const micCheck = mapPermissionToReadiness("microphone", Boolean(microphonePermission?.granted), microphonePermission?.canAskAgain !== false);
    const batteryCheck = mapBatteryToReadiness(battery.level, battery.lowPower);
    const deviceCheck = mapDeviceToReadiness(Device.isDevice);
    const networkCheck: ReadinessCheck = network.probing
      ? { key: "network", label: "Network", level: "recommend", detail: "Checking connection…", action: "retry-network" }
      : network_.check;
    return [deviceCheck, cameraCheck, micCheck, networkCheck, batteryCheck];
  }, [battery.level, battery.lowPower, cameraPermission, microphonePermission, network.probing, network_.check]);

  const overall: ReadinessLevel = useMemo(() => computeOverallReadiness(checks), [checks]);
  const summary = readinessSummary(overall);
  const overallTone: LogiNexusTone = overall === "blocked" ? "danger" : overall === "recommend" ? "warning" : "safety";

  const cameraReady = Boolean(cameraPermission?.granted) && Device.isDevice;

  async function handleAction(action: ReadinessAction) {
    setError("");
    setMessage("");
    if (action === "request-camera") {
      await requestCameraPermission();
    } else if (action === "request-mic") {
      await requestMicrophonePermission();
    } else if (action === "open-settings") {
      await Linking.openSettings().catch(() => setError("Could not open Settings."));
    } else if (action === "retry-network") {
      await runNetworkProbe();
    }
  }

  function updateDraft(patch: Partial<LiveStudioDraft>) {
    setDraft((current) => ({ ...current, ...patch }));
  }

  async function goLive() {
    setError("");
    setMessage("");
    if (overall === "blocked") {
      setError("Resolve the blocked readiness checks before going live.");
      return;
    }
    setHandingOff(true);
    try {
      const saved = await saveLiveStudioDraft(draft);
      const result = await startLive(saved);
      setMessage("Broadcast created. Connecting your camera…");
      navigation.navigate("NativeLiveHost", {
        liveId: result.liveId,
        room: result.room,
        tokenUrl: result.tokenUrl,
        title: saved.title.trim() || "PulseSoc Live"
      });
    } catch (err) {
      setError(err instanceof Error && err.message ? err.message : "PulseSoc could not start your broadcast.");
    } finally {
      setHandingOff(false);
    }
  }

  return (
    <ScrollView
      style={styles.root}
      contentContainerStyle={[styles.content, { paddingBottom: logiNexus.spacing.giant + insets.bottom }]}
      keyboardShouldPersistTaps="handled"
    >
      <LogiNexusPanel tone={overallTone} style={styles.overviewPanel}>
        <View style={styles.overviewHeader}>
          <View style={styles.overviewHeadings}>
            <Text style={styles.overline}>Live Studio · Pre-flight</Text>
            <Text style={styles.overviewTitle}>{summary.label}</Text>
          </View>
          <LogiNexusBadge label={overall === "ready" ? "Go" : overall === "recommend" ? "Review" : "Hold"} tone={overallTone} />
        </View>
        <Text style={styles.overviewDetail}>{summary.detail}</Text>
        <View style={styles.overviewActions}>
          <LogiNexusButton
            label={network.probing ? "Checking…" : "Re-run checks"}
            onPress={() => {
              runNetworkProbe().catch(() => undefined);
              refreshBattery().catch(() => undefined);
            }}
            tone="intelligence"
            variant="outline"
            disabled={network.probing}
            accessibilityLabel="Re-run studio readiness checks"
          />
        </View>
      </LogiNexusPanel>

      <View style={styles.previewCard}>
        {cameraReady ? (
          <CameraView style={styles.preview} facing={cameraFacing} mode="video" mute mirror={cameraFacing === "front"} />
        ) : (
          <View style={styles.previewFallback}>
            <Text style={styles.previewFallbackTitle}>{Device.isDevice ? "Camera preview needs permission" : "Camera preview needs a device"}</Text>
            <Text style={styles.previewFallbackBody}>
              {Device.isDevice
                ? "Allow camera access to check framing and lighting before you go live."
                : "Run on a physical device to preview the camera. Setup below still works on the simulator."}
            </Text>
            {Device.isDevice && !cameraPermission?.granted ? (
              <LogiNexusButton
                label="Allow Camera"
                onPress={() => requestCameraPermission().then(() => undefined)}
                tone="creator"
                accessibilityLabel="Allow camera access"
              />
            ) : null}
          </View>
        )}
        <View style={styles.previewOverlay} pointerEvents="box-none">
          <View style={styles.previewBadgeRow}>
            <LogiNexusBadge label="Preview only" tone="intelligence" />
          </View>
          {cameraReady ? (
            <Pressable
              style={styles.flipButton}
              onPress={() => setCameraFacing((current) => (current === "front" ? "back" : "front"))}
              accessibilityRole="button"
              accessibilityLabel="Flip camera"
            >
              <Text style={styles.flipText}>Flip</Text>
            </Pressable>
          ) : null}
        </View>
      </View>

      <View style={styles.section}>
        <Text style={styles.sectionTitle}>Readiness</Text>
        <View style={styles.checkList}>
          {checks.map((check) => (
            <View key={check.key} style={styles.checkRow}>
              <LogiNexusSignalIndicator active={check.level !== "blocked"} tone={toneForLevel(check.level)} />
              <View style={styles.checkBody}>
                <Text style={styles.checkLabel}>{check.label}</Text>
                <Text style={[styles.checkDetail, check.level === "blocked" ? styles.checkDetailBlocked : undefined]}>{check.detail}</Text>
              </View>
              {check.action ? (
                <Pressable
                  style={styles.checkAction}
                  onPress={() => handleAction(check.action as ReadinessAction)}
                  accessibilityRole="button"
                  accessibilityLabel={`${actionLabel(check.action)} for ${check.label}`}
                >
                  <Text style={styles.checkActionText}>{actionLabel(check.action)}</Text>
                </Pressable>
              ) : null}
            </View>
          ))}
        </View>
      </View>

      <View style={styles.section}>
        <Text style={styles.sectionTitle}>Set up your broadcast</Text>
        <TextInput
          style={styles.input}
          value={draft.title}
          onChangeText={(title) => updateDraft({ title })}
          placeholder="Broadcast title"
          placeholderTextColor={colors.muted}
          maxLength={120}
          accessibilityLabel="Broadcast title"
        />
        <TextInput
          style={[styles.input, styles.multiline]}
          value={draft.description}
          onChangeText={(description) => updateDraft({ description })}
          placeholder="What's this live about? (optional)"
          placeholderTextColor={colors.muted}
          maxLength={500}
          multiline
          textAlignVertical="top"
          accessibilityLabel="Broadcast description"
        />

        <Text style={styles.fieldLabel}>Live type</Text>
        <View style={styles.chipWrap}>
          {LIVE_TYPE_OPTIONS.map((option) => {
            const active = draft.liveType === option.key;
            return (
              <Pressable
                key={option.key}
                style={[styles.chip, active && styles.chipActive]}
                onPress={() => updateDraft({ liveType: option.key })}
                accessibilityRole="button"
                accessibilityState={{ selected: active }}
                accessibilityLabel={`${option.label}: ${option.helper}`}
              >
                <Text style={[styles.chipText, active && styles.chipTextActive]}>{option.label}</Text>
              </Pressable>
            );
          })}
        </View>

        <Text style={styles.fieldLabel}>Audience</Text>
        <View style={styles.chipWrap}>
          {AUDIENCE_OPTIONS.map((option) => {
            const active = draft.audience === option.key;
            return (
              <Pressable
                key={option.key}
                style={[styles.chip, active && styles.chipActive]}
                onPress={() => updateDraft({ audience: option.key })}
                accessibilityRole="button"
                accessibilityState={{ selected: active }}
                accessibilityLabel={`${option.label}: ${option.helper}`}
              >
                <Text style={[styles.chipText, active && styles.chipTextActive]}>{option.label}</Text>
              </Pressable>
            );
          })}
        </View>

        <View style={styles.toggleRow}>
          <View style={styles.toggleBody}>
            <Text style={styles.toggleLabel}>Allow comments</Text>
            <Text style={styles.toggleHelper}>Viewers can chat during the broadcast.</Text>
          </View>
          <Switch
            value={draft.allowComments}
            onValueChange={(allowComments) => updateDraft({ allowComments })}
            trackColor={{ true: colors.accent, false: colors.border }}
            thumbColor={colors.text}
            accessibilityLabel="Allow comments"
          />
        </View>
        <View style={styles.toggleRow}>
          <View style={styles.toggleBody}>
            <Text style={styles.toggleLabel}>Record replay</Text>
            <Text style={styles.toggleHelper}>Save a replay after the broadcast ends.</Text>
          </View>
          <Switch
            value={draft.recordReplay}
            onValueChange={(recordReplay) => updateDraft({ recordReplay })}
            trackColor={{ true: colors.accent, false: colors.border }}
            thumbColor={colors.text}
            accessibilityLabel="Record replay"
          />
        </View>
      </View>

      {error ? <Text style={styles.error}>{error}</Text> : message ? <Text style={styles.message}>{message}</Text> : null}

      <LogiNexusButton
        label={handingOff ? "Going live…" : "Go Live"}
        onPress={goLive}
        tone="danger"
        disabled={overall === "blocked" || handingOff}
        accessibilityLabel="Go live"
        testID="live-studio-go-live"
      />
      {handingOff ? <ActivityIndicator style={styles.spinner} color={colors.accent} /> : null}

      <LogiNexusPanel tone="intelligence" style={styles.notePanel}>
        <Text style={styles.noteTitle}>How broadcasting works</Text>
        <Text style={styles.noteBody}>
          This native pre-flight checks your device and captures your setup. Tapping Go Live starts a real native
          broadcast on this device — your camera and microphone publish through PulseSoc's LiveKit rooms, and you can
          accept guest requests from the host screen. No web Studio and no browser handoff.
        </Text>
      </LogiNexusPanel>
    </ScrollView>
  );
}

function toneForLevel(level: ReadinessLevel): LogiNexusTone {
  if (level === "blocked") return "danger";
  if (level === "recommend") return "warning";
  return "safety";
}

function actionLabel(action: ReadinessAction): string {
  if (action === "request-camera") return "Allow";
  if (action === "request-mic") return "Allow";
  if (action === "open-settings") return "Settings";
  return "Retry";
}

const styles = StyleSheet.create({
  chip: {
    borderColor: colors.border,
    borderRadius: logiNexus.radius.capsule,
    borderWidth: 1,
    paddingHorizontal: logiNexus.spacing.md,
    paddingVertical: logiNexus.spacing.sm
  },
  chipActive: {
    backgroundColor: colors.accent,
    borderColor: colors.accent
  },
  chipText: {
    ...logiNexus.typography.label,
    color: colors.text
  },
  chipTextActive: {
    color: colors.background
  },
  chipWrap: {
    flexDirection: "row",
    flexWrap: "wrap",
    gap: logiNexus.spacing.sm
  },
  checkAction: {
    borderColor: colors.accent,
    borderRadius: logiNexus.radius.small,
    borderWidth: 1,
    paddingHorizontal: logiNexus.spacing.md,
    paddingVertical: logiNexus.spacing.xs
  },
  checkActionText: {
    ...logiNexus.typography.label,
    color: colors.accent
  },
  checkBody: {
    flex: 1,
    gap: 2
  },
  checkDetail: {
    ...logiNexus.typography.metadata,
    color: colors.muted
  },
  checkDetailBlocked: {
    color: colors.danger
  },
  checkLabel: {
    ...logiNexus.typography.body,
    color: colors.text,
    fontWeight: "900"
  },
  checkList: {
    gap: logiNexus.spacing.md
  },
  checkRow: {
    alignItems: "center",
    flexDirection: "row",
    gap: logiNexus.spacing.md
  },
  content: {
    gap: logiNexus.spacing.lg,
    padding: logiNexus.spacing.lg
  },
  error: {
    ...logiNexus.typography.body,
    color: colors.danger,
    fontWeight: "800"
  },
  fieldLabel: {
    ...logiNexus.typography.label,
    color: colors.muted,
    marginTop: logiNexus.spacing.sm,
    textTransform: "uppercase"
  },
  flipButton: {
    alignSelf: "flex-end",
    backgroundColor: "rgba(5, 9, 16, 0.7)",
    borderColor: colors.border,
    borderRadius: logiNexus.radius.small,
    borderWidth: 1,
    paddingHorizontal: logiNexus.spacing.md,
    paddingVertical: logiNexus.spacing.sm
  },
  flipText: {
    ...logiNexus.typography.label,
    color: colors.text
  },
  input: {
    ...logiNexus.typography.body,
    backgroundColor: colors.surface,
    borderColor: colors.border,
    borderRadius: logiNexus.radius.medium,
    borderWidth: 1,
    color: colors.text,
    paddingHorizontal: logiNexus.spacing.md,
    paddingVertical: logiNexus.spacing.md
  },
  message: {
    ...logiNexus.typography.body,
    color: colors.accent,
    fontWeight: "800"
  },
  multiline: {
    minHeight: 88
  },
  noteBody: {
    ...logiNexus.typography.metadata,
    color: colors.muted,
    lineHeight: 18
  },
  notePanel: {
    gap: logiNexus.spacing.sm
  },
  noteTitle: {
    ...logiNexus.typography.sectionTitle,
    color: colors.text
  },
  overline: {
    ...logiNexus.typography.label,
    color: colors.muted,
    textTransform: "uppercase"
  },
  overviewActions: {
    flexDirection: "row",
    marginTop: logiNexus.spacing.md
  },
  overviewDetail: {
    ...logiNexus.typography.body,
    color: colors.muted,
    marginTop: logiNexus.spacing.xs
  },
  overviewHeadings: {
    flex: 1,
    gap: 2
  },
  overviewHeader: {
    alignItems: "center",
    flexDirection: "row",
    gap: logiNexus.spacing.md,
    justifyContent: "space-between"
  },
  overviewPanel: {
    gap: logiNexus.spacing.xs
  },
  overviewTitle: {
    ...logiNexus.typography.title,
    color: colors.text
  },
  preview: {
    ...StyleSheet.absoluteFillObject
  },
  previewBadgeRow: {
    flexDirection: "row"
  },
  previewCard: {
    aspectRatio: 3 / 4,
    backgroundColor: "#02050b",
    borderColor: colors.border,
    borderRadius: logiNexus.radius.panel,
    borderWidth: 1,
    maxHeight: 420,
    overflow: "hidden"
  },
  previewFallback: {
    alignItems: "center",
    flex: 1,
    gap: logiNexus.spacing.md,
    justifyContent: "center",
    padding: logiNexus.spacing.xl
  },
  previewFallbackBody: {
    ...logiNexus.typography.body,
    color: colors.muted,
    textAlign: "center"
  },
  previewFallbackTitle: {
    ...logiNexus.typography.sectionTitle,
    color: colors.text,
    textAlign: "center"
  },
  previewOverlay: {
    bottom: logiNexus.spacing.md,
    justifyContent: "space-between",
    left: logiNexus.spacing.md,
    position: "absolute",
    right: logiNexus.spacing.md,
    top: logiNexus.spacing.md
  },
  root: {
    backgroundColor: colors.background,
    flex: 1
  },
  section: {
    gap: logiNexus.spacing.md
  },
  sectionTitle: {
    ...logiNexus.typography.sectionTitle,
    color: colors.text
  },
  spinner: {
    marginTop: -logiNexus.spacing.sm
  },
  toggleBody: {
    flex: 1,
    gap: 2
  },
  toggleHelper: {
    ...logiNexus.typography.metadata,
    color: colors.muted
  },
  toggleLabel: {
    ...logiNexus.typography.body,
    color: colors.text,
    fontWeight: "900"
  },
  toggleRow: {
    alignItems: "center",
    borderColor: colors.border,
    borderRadius: logiNexus.radius.medium,
    borderWidth: 1,
    flexDirection: "row",
    gap: logiNexus.spacing.md,
    paddingHorizontal: logiNexus.spacing.md,
    paddingVertical: logiNexus.spacing.md
  }
});
