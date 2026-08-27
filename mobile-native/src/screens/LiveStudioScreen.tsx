/**
 * The Live Studio dashboard — the creator control center, and NOT a camera.
 *
 * WHY THERE IS NO CAMERA PREVIEW HERE ANY MORE
 *
 * This screen used to open a live `CameraView` above the setup form, which put
 * two different mental models on one page: "I am framing a shot" and "I am
 * filling in a form". The result was that creators could not tell whether they
 * were already broadcasting, and the real capture UI — the full-screen host
 * screen you land on after Start Live — looked like a *third* live screen
 * rather than the obvious next step.
 *
 * So the split is now explicit:
 *
 *   Live Studio (this file)   management: status, readiness, setup, tools
 *   Live host session         capture: full-screen camera, on-air controls
 *
 * Nothing about the broadcast itself changed. `startLive()` and the handoff to
 * `NativeLiveHost` are byte-for-byte what they were; the transport, the audio
 * path and the host screen are untouched.
 */

import * as Battery from "expo-battery";
import * as Device from "expo-device";
import { useCameraPermissions, useMicrophonePermissions } from "expo-camera";
import { useNavigation } from "@react-navigation/native";
import { NativeStackNavigationProp } from "@react-navigation/native-stack";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { ActivityIndicator, Linking, Pressable, ScrollView, Switch, Text, TextInput, View } from "react-native";
import { useSafeAreaInsets } from "react-native-safe-area-context";
import { PULSE_API_BASE_URL } from "../api/config";
import { startLive } from "../api/live";
import { LogiNexusBadge, LogiNexusButton, LogiNexusPanel, LogiNexusSignalIndicator } from "../components/LogiNexus";
import { getActiveMediaPlayback, subscribeMediaPlayback } from "../core/mediaPlaybackCoordinator";
import { RootStackParamList } from "../navigation/types";
import { useAuth } from "../session/auth";
import { colors } from "../theme/colors";
import { logiNexus, LogiNexusTone } from "../theme/logiNexus";
import { createThemedStyles } from "../theme/themedStyles";
import {
  AUDIENCE_OPTIONS,
  computeOverallReadiness,
  deriveLiveStudioStatus,
  emptyLiveStudioDraft,
  isLiveHostPlaybackId,
  LIVE_STUDIO_UPCOMING,
  LIVE_TYPE_OPTIONS,
  LiveStudioDraft,
  LiveStudioStatus,
  loadLiveStudioDraft,
  mapAccountToReadiness,
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
  const { authState, requestReauthentication } = useAuth();
  const [cameraPermission, requestCameraPermission] = useCameraPermissions();
  const [microphonePermission, requestMicrophonePermission] = useMicrophonePermissions();
  const [network, setNetwork] = useState<NetworkState>({ latencyMs: null, probing: true });
  const [battery, setBattery] = useState<BatteryState>({ level: null, lowPower: false });
  const [draft, setDraft] = useState<LiveStudioDraft>(emptyLiveStudioDraft());
  const [hydrated, setHydrated] = useState(false);
  const [handingOff, setHandingOff] = useState(false);
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");
  const saveTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  /*
   * "Am I on air right now?" — read from the media-playback coordinator rather
   * than from any state this screen owns, so the dashboard cannot disagree with
   * the host session about whether a broadcast is running. Only the *host*
   * scope counts: watching someone else's Live claims the coordinator with the
   * same kind, and that must not read as "you are broadcasting".
   */
  const [hostingLive, setHostingLive] = useState(() => isLiveHostPlaybackId(getActiveMediaPlayback()?.id));

  useEffect(() => {
    // The coordinator emits the current owner synchronously on subscribe, so a
    // broadcast already running when this screen mounts is picked up.
    const unsubscribe = subscribeMediaPlayback((owner) => {
      setHostingLive(isLiveHostPlaybackId(owner?.id));
    });
    return () => {
      unsubscribe();
    };
  }, []);

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

  /*
   * Order matters: this list is rendered top to bottom, and the four the brief
   * calls out — camera, microphone, network, account — are the ones that can
   * actually stop a broadcast, so they lead. Device and battery are advisory
   * and sit underneath.
   */
  const checks: ReadinessCheck[] = useMemo(() => {
    const cameraCheck = mapPermissionToReadiness("camera", Boolean(cameraPermission?.granted), cameraPermission?.canAskAgain !== false);
    const micCheck = mapPermissionToReadiness("microphone", Boolean(microphonePermission?.granted), microphonePermission?.canAskAgain !== false);
    const accountCheck = mapAccountToReadiness(authState.status, authState.user?.account_status);
    const batteryCheck = mapBatteryToReadiness(battery.level, battery.lowPower);
    const deviceCheck = mapDeviceToReadiness(Device.isDevice);
    const networkCheck: ReadinessCheck = network.probing
      ? { key: "network", label: "Network", level: "recommend", detail: "Checking connection…", action: "retry-network" }
      : network_.check;
    return [cameraCheck, micCheck, networkCheck, accountCheck, deviceCheck, batteryCheck];
  }, [
    authState.status,
    authState.user?.account_status,
    battery.level,
    battery.lowPower,
    cameraPermission,
    microphonePermission,
    network.probing,
    network_.check
  ]);

  const overall: ReadinessLevel = useMemo(() => computeOverallReadiness(checks), [checks]);
  const summary = readinessSummary(overall);
  const status: LiveStudioStatus = deriveLiveStudioStatus(overall, hostingLive);
  const statusTone: LogiNexusTone = status === "BLOCKED" ? "danger" : status === "LIVE" ? "creator" : "safety";
  const blockers = useMemo(() => checks.filter((check) => check.level === "blocked"), [checks]);

  /*
   * The headline the creator reads first. It answers "can I press the button",
   * which the readiness list below then explains in detail.
   */
  const headline =
    status === "LIVE"
      ? "You're on air right now"
      : status === "BLOCKED"
        ? "Complete setup before going live"
        : summary.label === "Ready"
          ? "Ready when you are"
          : summary.label;

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
    } else if (action === "sign-in") {
      requestReauthentication();
    }
  }

  function updateDraft(patch: Partial<LiveStudioDraft>) {
    setDraft((current) => ({ ...current, ...patch }));
  }

  /**
   * The one handoff out of this screen: create the broadcast, then hand the
   * creator straight to the full-screen camera experience. Unchanged from
   * before the studio/camera split — the button that calls it moved and was
   * renamed, but what it does did not.
   */
  async function startLiveBroadcast() {
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
      <LogiNexusPanel tone={statusTone} style={styles.overviewPanel}>
        <View style={styles.overviewHeader}>
          <View style={styles.overviewHeadings}>
            <Text style={styles.overline}>Live Studio</Text>
            <Text style={styles.overviewTitle} testID="live-studio-headline">
              {headline}
            </Text>
          </View>
        </View>
        <Text style={styles.overviewDetail}>Your live broadcasts, setup, and creator tools.</Text>
        <View
          style={styles.statusRow}
          accessible
          accessibilityRole="text"
          accessibilityLabel={`Current status: ${status}`}
          testID="live-studio-status"
        >
          <Text style={styles.statusLabel}>Current status</Text>
          <LogiNexusBadge label={status} tone={statusTone} />
        </View>
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

      <View style={styles.section}>
        <Text style={styles.sectionTitle}>Live readiness</Text>
        {/*
          Blocked is a line above the list, not a page instead of it. The old
          behaviour hid the whole studio behind an error state; here the same
          information sits one line above the rows that say what to fix, and
          everything else on the screen stays usable.

          The headline above already says "Complete setup before going live", so
          this line does not repeat it — it names the specific checks, and the
          rows underneath say how to fix each one.
        */}
        {blockers.length ? (
          <Text style={styles.blockedNotice} testID="live-studio-blocked-notice">
            Still needed: {blockers.map((check) => check.label).join(", ")}.
          </Text>
        ) : (
          <Text style={styles.sectionCaption}>{summary.detail}</Text>
        )}
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

      {/*
        The single door out of the dashboard and into the camera. It keeps the
        `live-studio-go-live` testID it has always had: renaming the label is a
        copy change, but renaming the hook other tests and QA scripts reach for
        would be a breaking one for no benefit.
      */}
      <LogiNexusButton
        label={hostingLive ? "You're already live" : handingOff ? "Opening camera…" : "Start Live"}
        onPress={startLiveBroadcast}
        tone="danger"
        disabled={overall === "blocked" || handingOff || hostingLive}
        accessibilityLabel="Start live and open the camera"
        testID="live-studio-go-live"
      />
      {handingOff ? <ActivityIndicator style={styles.spinner} color={colors.accent} /> : null}

      <View style={styles.section}>
        <Text style={styles.sectionTitle}>Upcoming</Text>
        <Text style={styles.sectionCaption}>Creator tools we're building next. None of these are available yet.</Text>
        {/*
          Not `Pressable`. These do nothing, and a row that responds to touch is
          a promise of a destination — the exact "looks tappable, lands nowhere"
          failure this restructure exists to remove.
        */}
        {LIVE_STUDIO_UPCOMING.map((item) => (
          <View
            key={item.key}
            style={styles.upcomingRow}
            accessible
            accessibilityLabel={`${item.label}. Coming soon. ${item.blurb}`}
            testID={`live-studio-upcoming-${item.key}`}
          >
            <View style={styles.upcomingBody}>
              <Text style={styles.upcomingLabel}>{item.label}</Text>
              <Text style={styles.upcomingBlurb}>{item.blurb}</Text>
            </View>
            <LogiNexusBadge label="Soon" tone="intelligence" />
          </View>
        ))}
      </View>

      <LogiNexusPanel tone="intelligence" style={styles.notePanel}>
        <Text style={styles.noteTitle}>How broadcasting works</Text>
        <Text style={styles.noteBody}>
          This screen manages your studio; it never opens the camera. Tapping Start Live creates the broadcast and hands
          you to the full-screen camera, where your camera and microphone publish through PulseSoc's Agora rooms and you
          can accept guest requests. No web Studio and no browser handoff.
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
  if (action === "sign-in") return "Sign in";
  return "Retry";
}

const styles = createThemedStyles(() => ({
  blockedNotice: {
    ...logiNexus.typography.metadata,
    color: colors.danger,
    fontWeight: "800"
  },
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
  root: {
    backgroundColor: colors.background,
    flex: 1
  },
  section: {
    gap: logiNexus.spacing.md
  },
  sectionCaption: {
    ...logiNexus.typography.metadata,
    color: colors.muted
  },
  sectionTitle: {
    ...logiNexus.typography.sectionTitle,
    color: colors.text
  },
  spinner: {
    marginTop: -logiNexus.spacing.sm
  },
  statusLabel: {
    ...logiNexus.typography.label,
    color: colors.muted,
    textTransform: "uppercase"
  },
  statusRow: {
    alignItems: "center",
    flexDirection: "row",
    gap: logiNexus.spacing.sm,
    marginTop: logiNexus.spacing.sm
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
  },
  upcomingBlurb: {
    ...logiNexus.typography.metadata,
    color: colors.muted,
    lineHeight: 17
  },
  upcomingBody: {
    flex: 1,
    gap: 2
  },
  /**
   * Dimmed relative to the working sections above it, and never by colour
   * alone: the "Soon" badge spells the state out, so the row still reads
   * correctly in greyscale and under a screen reader.
   */
  upcomingLabel: {
    ...logiNexus.typography.body,
    color: colors.muted,
    fontWeight: "900"
  },
  upcomingRow: {
    alignItems: "center",
    borderColor: colors.border,
    borderRadius: logiNexus.radius.medium,
    borderWidth: 1,
    flexDirection: "row",
    gap: logiNexus.spacing.md,
    opacity: 0.75,
    paddingHorizontal: logiNexus.spacing.md,
    paddingVertical: logiNexus.spacing.md
  }
}));
