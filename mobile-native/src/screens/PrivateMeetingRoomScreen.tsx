/**
 * Private Meeting Room — one screen for every phase of a meeting session.
 *
 * Renders whatever `meetingSession` says the phase is: joining → spinner,
 * waiting_room → parked view (withdraw; host gets Start), connecting →
 * spinner, in_meeting → the grid + controls + participants panel, ended →
 * summary. The screen owns NO meeting state and NO media state: the meeting
 * store owns admission/roster/lifecycle, and the canonical call session store
 * owns the Agora engine (mission §3/§59 — this file adds no engine owner; it
 * renders `RtcSurfaceView`s exactly the way CallScreen does, via the same
 * lazy import, and drives media through the store's own setters).
 *
 * Leave vs end-for-everyone (§42): two distinct store entry points, chosen in
 * an explicit dialog for moderators. `hangupCallSession` is deliberately
 * never imported here — the engine's end endpoint has direct-call semantics
 * that would end the meeting for everyone on any hangup.
 */

import { NativeStackScreenProps } from "@react-navigation/native-stack";
import { ComponentType, ReactNode, useCallback, useEffect, useMemo, useState } from "react";
import {
  ActivityIndicator,
  Alert,
  Modal,
  Platform,
  Pressable,
  ScrollView,
  StyleSheet,
  Text,
  View
} from "react-native";
import { Ionicons } from "@expo/vector-icons";
import { useSafeAreaInsets } from "react-native-safe-area-context";
import {
  setCallCameraEnabled,
  setCallMicrophoneEnabled,
  setCallSpeakerEnabled,
  switchCallCamera,
  useCallSession
} from "../calls/callSessionStore";
import { useTranslation } from "../i18n";
import { RootStackParamList } from "../navigation/types";
import { PrivateOfficeLockGate } from "../privateOffice/PrivateOfficeLockGate";
import {
  admitParticipant,
  denyParticipant,
  removeParticipant,
  setMeetingLocked,
  setParticipantRole,
  startMeeting
} from "../privateOffice/meetings/api";
import {
  buildMeetingTiles,
  meetingGridColumns,
  MeetingTile,
  waitingRoomOccupants
} from "../privateOffice/meetings/meetingTiles";
import {
  adoptMeetingProjection,
  endCurrentMeetingForEveryone,
  enterMeeting,
  getMeetingSession,
  leaveCurrentMeeting,
  refreshMeetingProjection,
  resetMeetingSession,
  useMeetingSession,
  withdrawFromWaitingRoom
} from "../privateOffice/meetings/meetingSession";
import {
  MeetingParticipant,
  MODERATOR_ROLES,
  PrivateMeeting
} from "../privateOffice/meetings/types";
import { colors } from "../theme/colors";

type Props = NativeStackScreenProps<RootStackParamList, "PrivateMeetingRoom">;

type AgoraVideoViewProps = {
  canvas: { uid: number; sourceType?: number };
  style?: any;
  zOrderMediaOverlay?: boolean;
};

export function PrivateMeetingRoomScreen(props: Props) {
  return (
    <PrivateOfficeLockGate
      onDismiss={() => props.navigation.goBack()}
      onRenew={() => props.navigation.navigate("Premium")}
    >
      <PrivateMeetingRoomBody {...props} />
    </PrivateOfficeLockGate>
  );
}

function PrivateMeetingRoomBody({ route, navigation }: Props) {
  const { t } = useTranslation();
  const insets = useSafeAreaInsets();
  const params = route.params || {};
  const session = useMeetingSession();
  const call = useCallSession();
  const [panelVisible, setPanelVisible] = useState(false);
  const [busy, setBusy] = useState("");
  const [AgoraVideoViewComponent, setAgoraVideoViewComponent] =
    useState<ComponentType<AgoraVideoViewProps> | null>(null);

  // Attach to the live meeting session, or enter the one this screen was
  // opened for. Mount-only, like CallScreen: identity comes from params.
  useEffect(() => {
    const current = getMeetingSession();
    const ref = String(params.ref || "");
    if (ref && current.meetingRef !== ref) {
      enterMeeting(ref).catch(() => undefined);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Same lazy view import as CallScreen — rendering only, no engine access.
  useEffect(() => {
    if (Platform.OS !== "web") {
      import("react-native-agora")
        .then((module) =>
          setAgoraVideoViewComponent(
            () => module.RtcSurfaceView as ComponentType<AgoraVideoViewProps>
          )
        )
        .catch(() => undefined);
    }
  }, []);

  const meeting = session.meeting;
  const amModerator = Boolean(meeting?.me && MODERATOR_ROLES.has(meeting.me.role));

  const tiles = useMemo(
    () =>
      buildMeetingTiles({
        meeting,
        remoteUids: call.remoteUids,
        localUid: call.localUid
      }),
    [meeting, call.remoteUids, call.localUid]
  );
  const columns = meetingGridColumns(tiles.length);
  const waiting = useMemo(() => waitingRoomOccupants(meeting), [meeting]);

  const leaveOnly = useCallback(async () => {
    await leaveCurrentMeeting();
  }, []);

  const confirmLeave = useCallback(() => {
    if (amModerator) {
      Alert.alert(
        t("premium:privateOffice.meetings.room.leaveTitle"),
        t("premium:privateOffice.meetings.room.leaveBodyModerator"),
        [
          { text: t("common:actions.cancel"), style: "cancel" },
          {
            text: t("premium:privateOffice.meetings.room.leaveMeeting"),
            onPress: () => {
              leaveOnly().catch(() => undefined);
            }
          },
          {
            text: t("premium:privateOffice.meetings.room.endForEveryone"),
            style: "destructive",
            onPress: () => {
              endCurrentMeetingForEveryone().catch(() =>
                Alert.alert(
                  t("premium:privateOffice.meetings.room.endFailed"),
                  t("premium:privateOffice.feature.error.body")
                )
              );
            }
          }
        ]
      );
      return;
    }
    leaveOnly().catch(() => undefined);
  }, [amModerator, leaveOnly, t]);

  const startNow = useCallback(async () => {
    if (!meeting) return;
    setBusy("start");
    try {
      const started = await startMeeting(meeting.public_id);
      adoptMeetingProjection(started);
    } catch {
      Alert.alert(
        t("premium:privateOffice.meetings.startFailed"),
        t("premium:privateOffice.feature.error.body")
      );
    } finally {
      setBusy("");
    }
  }, [meeting, t]);

  const withdraw = useCallback(async () => {
    setBusy("withdraw");
    try {
      await withdrawFromWaitingRoom();
    } finally {
      setBusy("");
    }
  }, []);

  const closeSummary = useCallback(() => {
    resetMeetingSession();
    navigation.goBack();
  }, [navigation]);

  const moderate = useCallback(
    async (action: string, userId: number) => {
      if (!meeting) return;
      setBusy(`${action}:${userId}`);
      try {
        if (action === "admit") await admitParticipant(meeting.public_id, userId);
        else if (action === "deny") await denyParticipant(meeting.public_id, userId);
        else if (action === "remove") await removeParticipant(meeting.public_id, userId);
        else if (action === "promote")
          await setParticipantRole(meeting.public_id, userId, "CO_HOST");
        else if (action === "demote")
          await setParticipantRole(meeting.public_id, userId, "PARTICIPANT");
        await refreshMeetingProjection();
      } catch {
        Alert.alert(
          t("premium:privateOffice.meetings.room.actionFailed"),
          t("premium:privateOffice.feature.error.body")
        );
      } finally {
        setBusy("");
      }
    },
    [meeting, t]
  );

  const toggleLock = useCallback(async () => {
    if (!meeting) return;
    setBusy("lock");
    try {
      await setMeetingLocked(meeting.public_id, !meeting.locked);
      await refreshMeetingProjection();
    } catch {
      Alert.alert(
        t("premium:privateOffice.meetings.room.actionFailed"),
        t("premium:privateOffice.feature.error.body")
      );
    } finally {
      setBusy("");
    }
  }, [meeting, t]);

  // --- phase rendering -------------------------------------------------------

  if (session.phase === "idle") {
    return (
      <CenteredPanel>
        {session.refusalCode ? (
          <>
            <Ionicons name="alert-circle-outline" size={26} color={colors.warning} />
            <Text style={styles.centeredTitle}>
              {t("premium:privateOffice.meetings.joinFailed")}
            </Text>
            <Text style={styles.centeredBody}>
              {refusalBody(session.refusalCode, t)}
            </Text>
            <Pressable style={styles.secondary} onPress={closeSummary} accessibilityRole="button">
              <Text style={styles.secondaryText}>
                {t("premium:privateOffice.meetings.summary.back")}
              </Text>
            </Pressable>
          </>
        ) : (
          <ActivityIndicator color={colors.accent} />
        )}
      </CenteredPanel>
    );
  }

  if (session.phase === "joining" || session.phase === "connecting") {
    return (
      <CenteredPanel>
        <ActivityIndicator color={colors.accent} />
        <Text style={styles.centeredBody}>
          {t(
            session.phase === "joining"
              ? "premium:privateOffice.meetings.room.joining"
              : "premium:privateOffice.meetings.room.connecting"
          )}
        </Text>
      </CenteredPanel>
    );
  }

  if (session.phase === "waiting_room") {
    const parkedAsHost = amModerator && meeting?.status !== "LIVE";
    return (
      <CenteredPanel>
        <Ionicons name="time-outline" size={26} color={colors.accent} />
        <Text style={styles.centeredTitle}>
          {meeting?.title || t("premium:privateOffice.meetings.untitled")}
        </Text>
        <Text style={styles.centeredBody}>
          {t(
            parkedAsHost
              ? "premium:privateOffice.meetings.room.notStartedBody"
              : "premium:privateOffice.meetings.room.waitingBody"
          )}
        </Text>
        {parkedAsHost ? (
          <Pressable
            style={styles.primary}
            onPress={startNow}
            disabled={Boolean(busy)}
            accessibilityRole="button"
            accessibilityLabel={t("premium:privateOffice.meetings.start")}
          >
            {busy === "start" ? (
              <ActivityIndicator color={colors.accentStrong} />
            ) : (
              <Text style={styles.primaryText}>{t("premium:privateOffice.meetings.start")}</Text>
            )}
          </Pressable>
        ) : null}
        <Pressable
          style={styles.secondary}
          onPress={withdraw}
          disabled={Boolean(busy)}
          accessibilityRole="button"
          accessibilityLabel={t("premium:privateOffice.meetings.room.withdraw")}
        >
          <Text style={styles.secondaryText}>
            {t("premium:privateOffice.meetings.room.withdraw")}
          </Text>
        </Pressable>
      </CenteredPanel>
    );
  }

  if (session.phase === "ended") {
    const reasonKey = SUMMARY_REASONS.has(session.endedReason)
      ? session.endedReason
      : "generic";
    return (
      <CenteredPanel>
        <Ionicons name="checkmark-circle-outline" size={26} color={colors.accent} />
        <Text style={styles.centeredTitle}>
          {t("premium:privateOffice.meetings.summary.title")}
        </Text>
        <Text style={styles.centeredBody}>
          {t(`premium:privateOffice.meetings.summary.reasons.${reasonKey}`)}
        </Text>
        <Pressable
          style={styles.primary}
          onPress={closeSummary}
          accessibilityRole="button"
          accessibilityLabel={t("premium:privateOffice.meetings.summary.back")}
        >
          <Text style={styles.primaryText}>
            {t("premium:privateOffice.meetings.summary.back")}
          </Text>
        </Pressable>
      </CenteredPanel>
    );
  }

  // phase === "in_meeting"
  return (
    <View style={[styles.room, { paddingTop: Math.max(insets.top, 10) }]}>
      <View style={styles.roomHeader}>
        <View style={styles.roomHeaderInfo}>
          <Text style={styles.roomTitle} numberOfLines={1}>
            {meeting?.title || t("premium:privateOffice.meetings.untitled")}
          </Text>
          <View style={styles.badgeRow}>
            {meeting?.recording_active ? (
              <View style={styles.recordingBadge}>
                <View style={styles.recordingDot} />
                <Text style={styles.recordingText}>
                  {t("premium:privateOffice.meetings.room.recording")}
                </Text>
              </View>
            ) : null}
            {meeting?.locked ? (
              <View style={styles.lockBadge}>
                <Ionicons name="lock-closed" size={11} color={colors.muted} />
                <Text style={styles.lockBadgeText}>
                  {t("premium:privateOffice.meetings.room.locked")}
                </Text>
              </View>
            ) : null}
            {call.reconnecting ? (
              <Text style={styles.reconnectingText}>
                {t("premium:privateOffice.meetings.room.reconnecting")}
              </Text>
            ) : null}
          </View>
        </View>
        <Pressable
          style={styles.headerButton}
          onPress={() => setPanelVisible(true)}
          accessibilityRole="button"
          accessibilityLabel={t("premium:privateOffice.meetings.room.participants")}
        >
          <Ionicons name="people-outline" size={20} color={colors.text} />
          <Text style={styles.headerButtonText}>{tiles.length}</Text>
          {amModerator && waiting.length ? (
            <View style={styles.waitingBadge}>
              <Text style={styles.waitingBadgeText}>{waiting.length}</Text>
            </View>
          ) : null}
        </Pressable>
      </View>

      <ScrollView style={styles.gridScroll} contentContainerStyle={styles.grid}>
        {tiles.map((tile) => (
          <MeetingTileView
            key={tile.userId}
            tile={tile}
            width={columns === 1 ? "100%" : "48.5%"}
            videoEnabled={tile.isSelf ? call.videoEnabled : true}
            VideoView={AgoraVideoViewComponent}
          />
        ))}
      </ScrollView>

      <View style={[styles.dock, { paddingBottom: Math.max(insets.bottom, 12) }]}>
        <DockButton
          icon={call.audioEnabled ? "mic-outline" : "mic-off-outline"}
          active={call.audioEnabled}
          label={t(
            call.audioEnabled
              ? "premium:privateOffice.meetings.room.mute"
              : "premium:privateOffice.meetings.room.unmute"
          )}
          onPress={() => setCallMicrophoneEnabled(!call.audioEnabled).catch(() => undefined)}
        />
        <DockButton
          icon={call.videoEnabled ? "videocam-outline" : "videocam-off-outline"}
          active={call.videoEnabled}
          label={t(
            call.videoEnabled
              ? "premium:privateOffice.meetings.room.cameraOff"
              : "premium:privateOffice.meetings.room.cameraOn"
          )}
          onPress={() => setCallCameraEnabled(!call.videoEnabled).catch(() => undefined)}
        />
        <DockButton
          icon={call.speakerEnabled ? "volume-high-outline" : "volume-low-outline"}
          active={call.speakerEnabled}
          label={t("premium:privateOffice.meetings.room.speaker")}
          onPress={() => setCallSpeakerEnabled(!call.speakerEnabled).catch(() => undefined)}
        />
        <DockButton
          icon="camera-reverse-outline"
          active
          label={t("premium:privateOffice.meetings.room.flip")}
          onPress={() => switchCallCamera().catch(() => undefined)}
        />
        <Pressable
          style={styles.leaveButton}
          onPress={confirmLeave}
          accessibilityRole="button"
          accessibilityLabel={t("premium:privateOffice.meetings.room.leave")}
        >
          <Ionicons name="call-outline" size={20} color="#fff" style={styles.leaveIcon} />
        </Pressable>
      </View>

      <ParticipantsPanel
        visible={panelVisible}
        onClose={() => setPanelVisible(false)}
        meeting={meeting}
        tiles={tiles}
        waiting={waiting}
        amModerator={amModerator}
        busy={busy}
        onModerate={moderate}
        onToggleLock={toggleLock}
      />
    </View>
  );
}

/** Refusal code → body copy, falling back to the generic error line. */
function refusalBody(code: string, t: (key: string) => string): string {
  const key = `premium:privateOffice.meetings.refusals.${code}`;
  const text = t(key);
  return text && text !== key ? text : t("premium:privateOffice.feature.error.body");
}

const SUMMARY_REASONS = new Set([
  "left",
  "removed",
  "ended",
  "ended_by_host",
  "call_ended",
  "feature_disabled"
]);

function CenteredPanel({ children }: { children: ReactNode }) {
  return (
    <View style={styles.centered}>
      <View style={styles.centeredCard}>{children}</View>
    </View>
  );
}

function MeetingTileView({
  tile,
  width,
  videoEnabled,
  VideoView
}: {
  tile: MeetingTile;
  width: string;
  videoEnabled: boolean;
  VideoView: ComponentType<AgoraVideoViewProps> | null;
}) {
  const { t } = useTranslation();
  const name = tile.isSelf
    ? t("premium:privateOffice.meetings.room.you")
    : t("premium:privateOffice.meetings.room.member", { id: tile.userId });
  const roleKey =
    tile.participant.role === "HOST"
      ? "host"
      : tile.participant.role === "CO_HOST"
        ? "coHost"
        : "";
  const showVideo = Boolean(VideoView && tile.mediaLive && videoEnabled);
  return (
    <View style={[styles.tile, { width } as object, tile.isActiveSpeaker && styles.tileSpeaking]}>
      {showVideo && VideoView ? (
        tile.isSelf ? (
          <VideoView canvas={{ uid: 0 }} style={styles.tileVideo} zOrderMediaOverlay />
        ) : (
          <VideoView canvas={{ uid: tile.userId }} style={styles.tileVideo} />
        )
      ) : (
        <View style={styles.tilePlaceholder}>
          <Ionicons name="person-circle-outline" size={42} color={colors.muted} />
        </View>
      )}
      <View style={styles.tileFooter}>
        <Text style={styles.tileName} numberOfLines={1}>
          {name}
        </Text>
        {roleKey ? (
          <Text style={styles.tileRole}>
            {t(`premium:privateOffice.meetings.room.${roleKey}`)}
          </Text>
        ) : null}
        {tile.participant.raised_hand ? (
          <Ionicons name="hand-left-outline" size={13} color={colors.warning} />
        ) : null}
        {!tile.mediaLive ? (
          <Ionicons name="cloud-offline-outline" size={13} color={colors.muted} />
        ) : null}
      </View>
    </View>
  );
}

function DockButton({
  icon,
  active,
  label,
  onPress
}: {
  icon: keyof typeof Ionicons.glyphMap;
  active: boolean;
  label: string;
  onPress: () => void;
}) {
  return (
    <Pressable
      style={[styles.dockButton, !active && styles.dockButtonMuted]}
      onPress={onPress}
      accessibilityRole="button"
      accessibilityLabel={label}
    >
      <Ionicons name={icon} size={20} color={active ? colors.text : colors.warning} />
    </Pressable>
  );
}

function ParticipantsPanel({
  visible,
  onClose,
  meeting,
  tiles,
  waiting,
  amModerator,
  busy,
  onModerate,
  onToggleLock
}: {
  visible: boolean;
  onClose: () => void;
  meeting: PrivateMeeting | null;
  tiles: MeetingTile[];
  waiting: MeetingParticipant[];
  amModerator: boolean;
  busy: string;
  onModerate: (action: string, userId: number) => void;
  onToggleLock: () => void;
}) {
  const { t } = useTranslation();
  const insets = useSafeAreaInsets();
  return (
    <Modal visible={visible} transparent animationType="slide" onRequestClose={onClose}>
      <View style={styles.panelBackdrop}>
        <Pressable style={styles.panelDismiss} onPress={onClose} accessibilityRole="button" />
        <View style={[styles.panel, { paddingBottom: Math.max(insets.bottom, 14) }]}>
          <View style={styles.panelHead}>
            <Text style={styles.panelTitle}>
              {t("premium:privateOffice.meetings.room.participants")}
            </Text>
            <Pressable onPress={onClose} accessibilityRole="button" accessibilityLabel={t("common:actions.close")}>
              <Ionicons name="close" size={20} color={colors.muted} />
            </Pressable>
          </View>

          {amModerator && meeting ? (
            <View style={styles.panelTools}>
              <Pressable
                style={styles.panelTool}
                onPress={onToggleLock}
                disabled={busy === "lock"}
                accessibilityRole="button"
                accessibilityLabel={t(
                  meeting.locked
                    ? "premium:privateOffice.meetings.room.unlock"
                    : "premium:privateOffice.meetings.room.lock"
                )}
              >
                <Ionicons
                  name={meeting.locked ? "lock-open-outline" : "lock-closed-outline"}
                  size={15}
                  color={colors.accentStrong}
                />
                <Text style={styles.panelToolText}>
                  {t(
                    meeting.locked
                      ? "premium:privateOffice.meetings.room.unlock"
                      : "premium:privateOffice.meetings.room.lock"
                  )}
                </Text>
              </Pressable>
              {meeting.meeting_code ? (
                <View style={styles.panelTool}>
                  <Ionicons name="key-outline" size={15} color={colors.muted} />
                  <Text style={styles.panelToolText}>
                    {t("premium:privateOffice.meetings.room.inviteCode", {
                      code: meeting.meeting_code
                    })}
                  </Text>
                </View>
              ) : null}
            </View>
          ) : null}

          <ScrollView style={styles.panelScroll}>
            {amModerator && waiting.length ? (
              <>
                <Text style={styles.panelSection}>
                  {t("premium:privateOffice.meetings.room.waitingSection")}
                </Text>
                {waiting.map((occupant) => (
                  <View key={occupant.user_id} style={styles.panelRow}>
                    <Text style={styles.panelRowName}>
                      {t("premium:privateOffice.meetings.room.member", { id: occupant.user_id })}
                    </Text>
                    <View style={styles.panelRowActions}>
                      <Pressable
                        style={styles.panelAction}
                        onPress={() => onModerate("admit", occupant.user_id)}
                        disabled={Boolean(busy)}
                        accessibilityRole="button"
                        accessibilityLabel={t("premium:privateOffice.meetings.room.admit")}
                      >
                        <Text style={styles.panelActionText}>
                          {t("premium:privateOffice.meetings.room.admit")}
                        </Text>
                      </Pressable>
                      <Pressable
                        style={styles.panelActionGhost}
                        onPress={() => onModerate("deny", occupant.user_id)}
                        disabled={Boolean(busy)}
                        accessibilityRole="button"
                        accessibilityLabel={t("premium:privateOffice.meetings.room.deny")}
                      >
                        <Text style={styles.panelActionGhostText}>
                          {t("premium:privateOffice.meetings.room.deny")}
                        </Text>
                      </Pressable>
                    </View>
                  </View>
                ))}
              </>
            ) : null}

            <Text style={styles.panelSection}>
              {t("premium:privateOffice.meetings.room.inRoomSection")}
            </Text>
            {tiles.map((tile) => (
              <View key={tile.userId} style={styles.panelRow}>
                <View style={styles.panelRowInfo}>
                  <Text style={styles.panelRowName}>
                    {tile.isSelf
                      ? t("premium:privateOffice.meetings.room.you")
                      : t("premium:privateOffice.meetings.room.member", { id: tile.userId })}
                  </Text>
                  <View style={styles.panelRowMeta}>
                    {tile.participant.role !== "PARTICIPANT" ? (
                      <Text style={styles.panelRowRole}>
                        {t(
                          `premium:privateOffice.meetings.room.${
                            tile.participant.role === "HOST" ? "host" : "coHost"
                          }`
                        )}
                      </Text>
                    ) : null}
                    {tile.participant.raised_hand ? (
                      <Ionicons name="hand-left-outline" size={13} color={colors.warning} />
                    ) : null}
                  </View>
                </View>
                {amModerator && !tile.isSelf && tile.participant.role !== "HOST" ? (
                  <View style={styles.panelRowActions}>
                    <Pressable
                      style={styles.panelActionGhost}
                      onPress={() =>
                        onModerate(
                          tile.participant.role === "CO_HOST" ? "demote" : "promote",
                          tile.userId
                        )
                      }
                      disabled={Boolean(busy)}
                      accessibilityRole="button"
                      accessibilityLabel={t(
                        tile.participant.role === "CO_HOST"
                          ? "premium:privateOffice.meetings.room.removeCoHost"
                          : "premium:privateOffice.meetings.room.makeCoHost"
                      )}
                    >
                      <Ionicons
                        name={tile.participant.role === "CO_HOST" ? "star" : "star-outline"}
                        size={15}
                        color={colors.accentStrong}
                      />
                    </Pressable>
                    <Pressable
                      style={styles.panelActionGhost}
                      onPress={() => onModerate("remove", tile.userId)}
                      disabled={Boolean(busy)}
                      accessibilityRole="button"
                      accessibilityLabel={t("premium:privateOffice.meetings.room.remove")}
                    >
                      <Ionicons name="person-remove-outline" size={15} color={colors.warning} />
                    </Pressable>
                  </View>
                ) : null}
              </View>
            ))}
          </ScrollView>
        </View>
      </View>
    </Modal>
  );
}

const styles = StyleSheet.create({
  centered: { flex: 1, backgroundColor: colors.background, alignItems: "center", justifyContent: "center", padding: 20 },
  centeredCard: {
    backgroundColor: colors.surface,
    borderColor: colors.border,
    borderWidth: 1,
    borderRadius: 18,
    padding: 22,
    gap: 10,
    alignItems: "center",
    alignSelf: "stretch"
  },
  centeredTitle: { color: colors.text, fontSize: 17, fontWeight: "700", textAlign: "center" },
  centeredBody: { color: colors.muted, fontSize: 13, lineHeight: 19, textAlign: "center" },
  primary: {
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "center",
    gap: 8,
    backgroundColor: colors.surfaceRaised,
    borderColor: colors.border,
    borderWidth: 1,
    borderRadius: 999,
    paddingHorizontal: 22,
    paddingVertical: 12,
    marginTop: 6
  },
  primaryText: { color: colors.accentStrong, fontSize: 14, fontWeight: "700" },
  secondary: { paddingHorizontal: 18, paddingVertical: 10, marginTop: 2 },
  secondaryText: { color: colors.muted, fontSize: 13, fontWeight: "600" },
  room: { flex: 1, backgroundColor: colors.background },
  roomHeader: {
    flexDirection: "row",
    alignItems: "center",
    paddingHorizontal: 14,
    paddingVertical: 8,
    gap: 10
  },
  roomHeaderInfo: { flex: 1, gap: 2 },
  roomTitle: { color: colors.text, fontSize: 16, fontWeight: "700" },
  badgeRow: { flexDirection: "row", alignItems: "center", gap: 8 },
  recordingBadge: { flexDirection: "row", alignItems: "center", gap: 4 },
  recordingDot: { width: 7, height: 7, borderRadius: 4, backgroundColor: "#e5484d" },
  recordingText: { color: "#e5484d", fontSize: 11, fontWeight: "700" },
  lockBadge: { flexDirection: "row", alignItems: "center", gap: 3 },
  lockBadgeText: { color: colors.muted, fontSize: 11 },
  reconnectingText: { color: colors.warning, fontSize: 11, fontWeight: "600" },
  headerButton: {
    flexDirection: "row",
    alignItems: "center",
    gap: 5,
    backgroundColor: colors.surfaceRaised,
    borderColor: colors.border,
    borderWidth: 1,
    borderRadius: 999,
    paddingHorizontal: 12,
    paddingVertical: 7
  },
  headerButtonText: { color: colors.text, fontSize: 13, fontWeight: "700" },
  waitingBadge: {
    backgroundColor: colors.warning,
    borderRadius: 999,
    minWidth: 16,
    height: 16,
    alignItems: "center",
    justifyContent: "center",
    paddingHorizontal: 3
  },
  waitingBadgeText: { color: "#000", fontSize: 10, fontWeight: "800" },
  gridScroll: { flex: 1 },
  grid: {
    flexDirection: "row",
    flexWrap: "wrap",
    gap: 8,
    padding: 10,
    justifyContent: "center"
  },
  tile: {
    aspectRatio: 3 / 4,
    borderRadius: 14,
    overflow: "hidden",
    backgroundColor: colors.surface,
    borderColor: colors.border,
    borderWidth: 1
  },
  tileSpeaking: { borderColor: colors.accent, borderWidth: 2 },
  tileVideo: { flex: 1 },
  tilePlaceholder: { flex: 1, alignItems: "center", justifyContent: "center" },
  tileFooter: {
    position: "absolute",
    left: 0,
    right: 0,
    bottom: 0,
    flexDirection: "row",
    alignItems: "center",
    gap: 5,
    paddingHorizontal: 8,
    paddingVertical: 5,
    backgroundColor: "rgba(0,0,0,0.45)"
  },
  tileName: { color: "#fff", fontSize: 12, fontWeight: "600", flexShrink: 1 },
  tileRole: { color: colors.accent, fontSize: 10, fontWeight: "700" },
  dock: {
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "center",
    gap: 12,
    paddingTop: 10,
    paddingHorizontal: 14
  },
  dockButton: {
    width: 46,
    height: 46,
    borderRadius: 999,
    alignItems: "center",
    justifyContent: "center",
    backgroundColor: colors.surfaceRaised,
    borderColor: colors.border,
    borderWidth: 1
  },
  dockButtonMuted: { borderColor: colors.warning },
  leaveButton: {
    width: 52,
    height: 46,
    borderRadius: 999,
    alignItems: "center",
    justifyContent: "center",
    backgroundColor: "#e5484d"
  },
  leaveIcon: { transform: [{ rotate: "135deg" }] },
  panelBackdrop: { flex: 1, backgroundColor: "rgba(0,0,0,0.5)", justifyContent: "flex-end" },
  panelDismiss: { flex: 1 },
  panel: {
    backgroundColor: colors.surface,
    borderTopLeftRadius: 20,
    borderTopRightRadius: 20,
    paddingHorizontal: 16,
    paddingTop: 14,
    maxHeight: "70%",
    gap: 10
  },
  panelHead: { flexDirection: "row", alignItems: "center", justifyContent: "space-between" },
  panelTitle: { color: colors.text, fontSize: 16, fontWeight: "700" },
  panelTools: { flexDirection: "row", flexWrap: "wrap", gap: 10 },
  panelTool: {
    flexDirection: "row",
    alignItems: "center",
    gap: 5,
    backgroundColor: colors.surfaceRaised,
    borderColor: colors.border,
    borderWidth: 1,
    borderRadius: 999,
    paddingHorizontal: 11,
    paddingVertical: 7
  },
  panelToolText: { color: colors.text, fontSize: 12, fontWeight: "600" },
  panelScroll: { flexGrow: 0 },
  panelSection: {
    color: colors.muted,
    fontSize: 12,
    fontWeight: "700",
    textTransform: "uppercase",
    marginTop: 8,
    marginBottom: 4
  },
  panelRow: {
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "space-between",
    paddingVertical: 9,
    gap: 8
  },
  panelRowInfo: { flex: 1, gap: 2 },
  panelRowMeta: { flexDirection: "row", alignItems: "center", gap: 6 },
  panelRowName: { color: colors.text, fontSize: 14, fontWeight: "600" },
  panelRowRole: { color: colors.accent, fontSize: 11, fontWeight: "700" },
  panelRowActions: { flexDirection: "row", alignItems: "center", gap: 8 },
  panelAction: {
    backgroundColor: colors.surfaceRaised,
    borderColor: colors.border,
    borderWidth: 1,
    borderRadius: 999,
    paddingHorizontal: 13,
    paddingVertical: 7
  },
  panelActionText: { color: colors.accentStrong, fontSize: 12, fontWeight: "700" },
  panelActionGhost: { paddingHorizontal: 8, paddingVertical: 7 },
  panelActionGhostText: { color: colors.muted, fontSize: 12, fontWeight: "600" }
});
