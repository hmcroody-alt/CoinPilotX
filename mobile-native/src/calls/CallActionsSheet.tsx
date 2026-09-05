/**
 * Native call actions menu — the top-right ••• on an active call.
 *
 * This replaces a dead end. The ••• button used to call `openCallWebFallback()`,
 * which answers `native_provider_boundary`: a hand-off to the web client that
 * cannot possibly serve a live Agora call the native app is already holding. The
 * capability the menu should have been offering already existed — it was just
 * only reachable from the "Add" button in the bottom control dock.
 *
 * This sheet is an ENTRYPOINT ONLY. It owns no call state, holds no media, and
 * never speaks to Agora:
 *
 *   - "Add people" defers to the AddParticipantsSheet the Call screen already
 *     owns, by asking the screen to open it. It does not invite anyone itself.
 *   - The roster is rendered from the canonical participant registry
 *     (`calls/callParticipants`), so there is no second participant state model.
 *   - "Call details" reads values the screen already computed and displays.
 *
 * The only network call it can make is a governed Trust & Safety report, and
 * only of a *user*: `/api/pulse/report` accepts `{post, comment, media, user}`
 * and has no `call` target, so reporting "this call" is not a real action and is
 * not offered. The row therefore appears only when there is exactly one remote
 * participant to name — in a group call "Report" without a target picker would
 * be a guess about who the user meant.
 *
 * NOT a protected realtime-audio path: pure UI plus one optional backend POST.
 */

import { Ionicons } from "@expo/vector-icons";
import { useCallback, useEffect, useMemo, useState } from "react";
import {
  ActivityIndicator,
  Modal,
  Pressable,
  ScrollView,
  StyleSheet,
  Text,
  View
} from "react-native";
import type { PulseCallType } from "../api/calls";
import { reportPulseTarget } from "../api/support";
import { colors } from "../theme/colors";
import type { CallParticipantView } from "./callParticipants";
import { isTerminalParticipantStatus } from "./callParticipants";

type SheetView = "menu" | "participants" | "details";

type CallActionsSheetProps = {
  visible: boolean;
  callType: PulseCallType;
  participants: CallParticipantView[];
  /** Already-formatted elapsed time from the screen; empty until connected. */
  durationLabel: string;
  connected: boolean;
  canAddParticipants: boolean;
  /** Ask the Call screen to open its existing AddParticipantsSheet. */
  onAddPeople: () => void;
  onClose: () => void;
};

export function CallActionsSheet({
  visible,
  callType,
  participants,
  durationLabel,
  connected,
  canAddParticipants,
  onAddPeople,
  onClose
}: CallActionsSheetProps) {
  const [view, setView] = useState<SheetView>("menu");
  const [reportState, setReportState] = useState<"idle" | "sending" | "sent" | "failed">("idle");

  // Every open starts at the menu; a sheet that reopens on the roster because
  // that is where it was last closed reads as a bug.
  useEffect(() => {
    if (visible) {
      setView("menu");
      setReportState("idle");
    }
  }, [visible]);

  const roster = useMemo(
    () => participants.filter((view_) => !isTerminalParticipantStatus(view_.backendStatus)),
    [participants]
  );
  const activeCount = useMemo(
    () => roster.filter((view_) => view_.backendStatus === "joined" || view_.isLocal).length,
    [roster]
  );

  // Exactly one remote participant means "Report" has an unambiguous subject.
  const reportTarget = useMemo(() => {
    const remotes = roster.filter((view_) => !view_.isLocal);
    return remotes.length === 1 ? remotes[0] : null;
  }, [roster]);

  const addPeople = useCallback(() => {
    onClose();
    onAddPeople();
  }, [onClose, onAddPeople]);

  const report = useCallback(async () => {
    if (!reportTarget || reportState === "sending") return;
    setReportState("sending");
    try {
      await reportPulseTarget("user", reportTarget.userId, "Reported from an active PulseSoc call.");
      setReportState("sent");
    } catch {
      setReportState("failed");
    }
  }, [reportTarget, reportState]);

  return (
    <Modal visible={visible} transparent animationType="slide" onRequestClose={onClose}>
      <View style={styles.backdrop}>
        <View style={styles.sheet}>
          <View style={styles.header}>
            {view === "menu" ? (
              <Text style={styles.title}>Call options</Text>
            ) : (
              <Pressable
                accessibilityRole="button"
                accessibilityLabel="Back to call options"
                onPress={() => setView("menu")}
                style={({ pressed }) => [styles.backButton, pressed && styles.pressed]}
              >
                <Ionicons name="chevron-back" size={20} color={colors.text} />
                <Text style={styles.backText}>{view === "participants" ? "Participants" : "Call details"}</Text>
              </Pressable>
            )}
            <Pressable
              accessibilityRole="button"
              accessibilityLabel="Close call options"
              onPress={onClose}
              style={({ pressed }) => [styles.closeButton, pressed && styles.pressed]}
            >
              <Ionicons name="close" size={22} color={colors.text} />
            </Pressable>
          </View>

          {view === "menu" ? (
            <View>
              {canAddParticipants ? (
                <ActionRow icon="person-add" label="Add people" onPress={addPeople} />
              ) : null}
              <ActionRow icon="people" label="Participants" onPress={() => setView("participants")} />
              <ActionRow icon="information-circle" label="Call details" onPress={() => setView("details")} />
              {reportTarget ? (
                <ActionRow
                  icon="flag"
                  label={reportState === "sent" ? "Report sent" : `Report ${reportTarget.displayName}`}
                  destructive
                  busy={reportState === "sending"}
                  disabled={reportState === "sent"}
                  onPress={report}
                />
              ) : null}
              {reportState === "failed" ? (
                <Text style={styles.errorText}>Couldn’t send that report. Please try again.</Text>
              ) : null}
              <Pressable
                accessibilityRole="button"
                accessibilityLabel="Cancel call options"
                onPress={onClose}
                style={({ pressed }) => [styles.cancelButton, pressed && styles.pressed]}
              >
                <Text style={styles.cancelText}>Cancel</Text>
              </Pressable>
            </View>
          ) : null}

          {view === "participants" ? (
            <ScrollView style={styles.list}>
              {roster.length === 0 ? (
                <Text style={styles.emptyText}>No one is on this call yet.</Text>
              ) : (
                roster.map((person) => (
                  <View key={person.userId} style={styles.row} accessibilityLabel={`Participant ${person.displayName}`}>
                    <View style={styles.avatar}>
                      <Text style={styles.avatarInitials}>
                        {person.displayName.trim().slice(0, 1).toUpperCase() || "?"}
                      </Text>
                    </View>
                    <View style={styles.rowText}>
                      <Text style={styles.rowName} numberOfLines={1}>
                        {person.displayName}
                        {person.isLocal ? " (You)" : ""}
                      </Text>
                      <Text style={styles.rowStatus}>{participantStatusLabel(person)}</Text>
                    </View>
                    {person.audioMuted ? <Ionicons name="mic-off" size={18} color="#99a8be" /> : null}
                    {callType === "video" && person.videoMuted ? (
                      <Ionicons name="videocam-off" size={18} color="#99a8be" />
                    ) : null}
                  </View>
                ))
              )}
            </ScrollView>
          ) : null}

          {view === "details" ? (
            <View style={styles.list}>
              <DetailRow label="Type" value={callType === "video" ? "Video call" : "Voice call"} />
              <DetailRow label="In call" value={String(activeCount)} />
              <DetailRow label="Duration" value={connected && durationLabel ? durationLabel : "Not connected"} />
              <DetailRow label="Connection" value={connected ? "Secure link" : "Connecting"} />
            </View>
          ) : null}
        </View>
      </View>
    </Modal>
  );
}

function participantStatusLabel(person: CallParticipantView): string {
  if (person.backendStatus === "ringing") return "Ringing…";
  if (person.rtcConnected) return person.speaking ? "Speaking" : "In call";
  return "Connecting…";
}

function ActionRow({
  icon,
  label,
  onPress,
  destructive,
  busy,
  disabled
}: {
  icon: keyof typeof Ionicons.glyphMap;
  label: string;
  onPress: () => void;
  destructive?: boolean;
  busy?: boolean;
  disabled?: boolean;
}) {
  return (
    <Pressable
      accessibilityRole="button"
      accessibilityLabel={label}
      disabled={disabled || busy}
      onPress={onPress}
      style={({ pressed }) => [styles.actionRow, pressed && styles.pressed, (disabled || busy) && styles.disabled]}
    >
      {busy ? (
        <ActivityIndicator color={colors.accent} />
      ) : (
        <Ionicons name={icon} size={22} color={destructive ? "#ff92aa" : colors.accent} />
      )}
      <Text style={[styles.actionLabel, destructive && styles.destructiveLabel]}>{label}</Text>
    </Pressable>
  );
}

function DetailRow({ label, value }: { label: string; value: string }) {
  return (
    <View style={styles.detailRow}>
      <Text style={styles.detailLabel}>{label}</Text>
      <Text style={styles.detailValue}>{value}</Text>
    </View>
  );
}

const styles = StyleSheet.create({
  backdrop: { flex: 1, justifyContent: "flex-end", backgroundColor: "rgba(1,4,10,0.66)" },
  sheet: { maxHeight: "72%", backgroundColor: "#071120", borderTopLeftRadius: 26, borderTopRightRadius: 26, borderWidth: 1, borderColor: "rgba(97,216,255,0.18)", paddingHorizontal: 18, paddingTop: 16, paddingBottom: 28 },
  header: { flexDirection: "row", alignItems: "center", justifyContent: "space-between", marginBottom: 6 },
  title: { color: colors.text, fontSize: 19, fontWeight: "900" },
  backButton: { flexDirection: "row", alignItems: "center", gap: 4 },
  backText: { color: colors.text, fontSize: 17, fontWeight: "800" },
  closeButton: { width: 38, height: 38, borderRadius: 14, alignItems: "center", justifyContent: "center", backgroundColor: "rgba(17,29,45,0.94)", borderWidth: 1, borderColor: "rgba(97,216,255,0.2)" },
  actionRow: { flexDirection: "row", alignItems: "center", gap: 14, paddingVertical: 14 },
  actionLabel: { color: colors.text, fontSize: 16, fontWeight: "700" },
  destructiveLabel: { color: "#ff92aa" },
  cancelButton: { marginTop: 10, borderRadius: 22, backgroundColor: "rgba(17,29,45,0.94)", borderWidth: 1, borderColor: "rgba(97,216,255,0.2)", alignItems: "center", justifyContent: "center", minHeight: 50 },
  cancelText: { color: colors.text, fontWeight: "900", fontSize: 15 },
  list: { marginTop: 4 },
  row: { flexDirection: "row", alignItems: "center", gap: 12, paddingVertical: 10 },
  avatar: { width: 40, height: 40, borderRadius: 20, alignItems: "center", justifyContent: "center", backgroundColor: "#122239", borderWidth: 1, borderColor: "rgba(97,216,255,0.28)" },
  avatarInitials: { color: colors.text, fontWeight: "900", fontSize: 16 },
  rowText: { flex: 1 },
  rowName: { color: colors.text, fontSize: 15, fontWeight: "700" },
  rowStatus: { color: "#99a8be", fontSize: 12, fontWeight: "600", marginTop: 2 },
  detailRow: { flexDirection: "row", alignItems: "center", justifyContent: "space-between", paddingVertical: 12 },
  detailLabel: { color: "#99a8be", fontSize: 14, fontWeight: "700" },
  detailValue: { color: colors.text, fontSize: 15, fontWeight: "800" },
  emptyText: { color: "#99a8be", fontSize: 14, textAlign: "center", paddingVertical: 24 },
  errorText: { color: "#ff92aa", fontWeight: "700", fontSize: 13, marginTop: 4 },
  disabled: { opacity: 0.5 },
  pressed: { opacity: 0.72 }
});
