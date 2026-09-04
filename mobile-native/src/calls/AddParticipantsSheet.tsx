/**
 * Mid-call "add participants" picker.
 *
 * Lists the CONVERSATION members (the backend validates invitees against
 * conversation membership, so the picker offers exactly the set the server
 * will accept), excluding the local user and anyone already on the call
 * (joined or still ringing). Selection is capped at the server-provided
 * participant limit. Inviting posts to the backend, which owns all invite
 * state — this sheet never touches media and never creates a new channel.
 *
 * NOT a protected realtime-audio path: pure UI + one backend POST.
 */

import { Ionicons } from "@expo/vector-icons";
import { useCallback, useEffect, useMemo, useState } from "react";
import {
  ActivityIndicator,
  FlatList,
  Image,
  Modal,
  Pressable,
  StyleSheet,
  Text,
  View
} from "react-native";
import { inviteToCall } from "../api/calls";
import { ConversationControlMember, listConversationMembers } from "../api/messenger";
import { colors } from "../theme/colors";
import { CallParticipantView } from "./callParticipants";
import { refreshCallSessionStatus } from "./callSessionStore";

type AddParticipantsSheetProps = {
  visible: boolean;
  callId: string;
  conversationId?: number;
  participants: CallParticipantView[];
  maxParticipants: number;
  onClose: () => void;
};

export function AddParticipantsSheet({
  visible,
  callId,
  conversationId,
  participants,
  maxParticipants,
  onClose
}: AddParticipantsSheetProps) {
  const [members, setMembers] = useState<ConversationControlMember[]>([]);
  const [loading, setLoading] = useState(false);
  const [inviting, setInviting] = useState(false);
  const [error, setError] = useState("");
  const [selected, setSelected] = useState<number[]>([]);

  const onCallUserIds = useMemo(
    () =>
      new Set(
        participants
          .filter((view) => view.backendStatus === "joined" || view.backendStatus === "ringing")
          .map((view) => view.userId)
      ),
    [participants]
  );
  const activeCount = onCallUserIds.size;
  const remainingSlots = Math.max(0, maxParticipants - activeCount);

  useEffect(() => {
    if (!visible || !conversationId) return;
    let mounted = true;
    setLoading(true);
    setError("");
    setSelected([]);
    listConversationMembers(conversationId)
      .then((list) => {
        if (mounted) setMembers(list || []);
      })
      .catch(() => {
        if (mounted) setError("Couldn’t load people from this conversation.");
      })
      .finally(() => {
        if (mounted) setLoading(false);
      });
    return () => {
      mounted = false;
    };
  }, [visible, conversationId]);

  const candidates = useMemo(
    () =>
      (members || []).filter((member) => {
        const userId = Number(member.user_id || 0);
        return userId > 0 && !onCallUserIds.has(userId);
      }),
    [members, onCallUserIds]
  );

  const toggle = useCallback(
    (userId: number) => {
      setSelected((current) => {
        if (current.includes(userId)) return current.filter((id) => id !== userId);
        if (current.length >= remainingSlots) return current;
        return [...current, userId];
      });
    },
    [remainingSlots]
  );

  const invite = useCallback(async () => {
    if (!callId || !selected.length || inviting) return;
    setInviting(true);
    setError("");
    try {
      await inviteToCall(callId, selected);
      // Pull the authoritative participant list right away so the ringing
      // chips appear without waiting for the next poll tick.
      await refreshCallSessionStatus().catch(() => undefined);
      onClose();
    } catch (inviteError) {
      const raw = inviteError instanceof Error ? inviteError.message.toLowerCase() : "";
      if (raw.includes("limit")) setError("This call is at its participant limit.");
      else if (raw.includes("enabled")) setError("Group calls are not available yet.");
      else setError("Couldn’t send the invites. Please try again.");
    } finally {
      setInviting(false);
    }
  }, [callId, selected, inviting, onClose]);

  return (
    <Modal visible={visible} transparent animationType="slide" onRequestClose={onClose}>
      <View style={styles.backdrop}>
        <View style={styles.sheet}>
          <View style={styles.header}>
            <Text style={styles.title}>Add to call</Text>
            <Pressable accessibilityRole="button" accessibilityLabel="Close add participants" onPress={onClose} style={({ pressed }) => [styles.closeButton, pressed && styles.pressed]}>
              <Ionicons name="close" size={22} color={colors.text} />
            </Pressable>
          </View>
          <Text style={styles.subtitle}>
            {remainingSlots > 0
              ? `${remainingSlots} more ${remainingSlots === 1 ? "person" : "people"} can join this call.`
              : "This call is at its participant limit."}
          </Text>

          {loading ? (
            <View style={styles.centered}>
              <ActivityIndicator color={colors.accent} />
            </View>
          ) : candidates.length === 0 ? (
            <View style={styles.centered}>
              <Text style={styles.emptyText}>Everyone in this conversation is already on the call.</Text>
            </View>
          ) : (
            <FlatList
              data={candidates}
              keyExtractor={(member) => String(member.user_id)}
              style={styles.list}
              renderItem={({ item }) => {
                const userId = Number(item.user_id || 0);
                const isSelected = selected.includes(userId);
                const name = item.display_name || `User ${userId}`;
                return (
                  <Pressable
                    accessibilityRole="button"
                    accessibilityLabel={`${isSelected ? "Remove" : "Add"} ${name}`}
                    style={({ pressed }) => [styles.row, pressed && styles.pressed]}
                    onPress={() => toggle(userId)}
                  >
                    <View style={styles.rowAvatar}>
                      {item.avatar_url ? (
                        <Image source={{ uri: item.avatar_url }} style={styles.rowAvatarImage} />
                      ) : (
                        <Text style={styles.rowAvatarInitials}>{name.trim().slice(0, 1).toUpperCase() || "?"}</Text>
                      )}
                    </View>
                    <Text style={styles.rowName} numberOfLines={1}>{name}</Text>
                    <Ionicons
                      name={isSelected ? "checkmark-circle" : "ellipse-outline"}
                      size={24}
                      color={isSelected ? colors.accent : "rgba(174,187,208,0.6)"}
                    />
                  </Pressable>
                );
              }}
            />
          )}

          {error ? <Text style={styles.errorText}>{error}</Text> : null}

          <Pressable
            accessibilityRole="button"
            accessibilityLabel="Send call invites"
            disabled={!selected.length || inviting || remainingSlots === 0}
            style={({ pressed }) => [
              styles.inviteButton,
              (!selected.length || remainingSlots === 0) && styles.disabled,
              pressed && styles.pressed
            ]}
            onPress={invite}
          >
            {inviting ? (
              <ActivityIndicator color="#03100d" />
            ) : (
              <Text style={styles.inviteText}>
                {selected.length ? `Invite ${selected.length} ${selected.length === 1 ? "person" : "people"}` : "Select people to invite"}
              </Text>
            )}
          </Pressable>
        </View>
      </View>
    </Modal>
  );
}

const styles = StyleSheet.create({
  backdrop: { flex: 1, justifyContent: "flex-end", backgroundColor: "rgba(1,4,10,0.66)" },
  sheet: { maxHeight: "72%", backgroundColor: "#071120", borderTopLeftRadius: 26, borderTopRightRadius: 26, borderWidth: 1, borderColor: "rgba(97,216,255,0.18)", paddingHorizontal: 18, paddingTop: 16, paddingBottom: 28 },
  header: { flexDirection: "row", alignItems: "center", justifyContent: "space-between" },
  title: { color: colors.text, fontSize: 19, fontWeight: "900" },
  closeButton: { width: 38, height: 38, borderRadius: 14, alignItems: "center", justifyContent: "center", backgroundColor: "rgba(17,29,45,0.94)", borderWidth: 1, borderColor: "rgba(97,216,255,0.2)" },
  subtitle: { color: "#99a8be", fontSize: 13, fontWeight: "600", marginTop: 6, marginBottom: 10 },
  list: { flexGrow: 0 },
  centered: { alignItems: "center", justifyContent: "center", paddingVertical: 28 },
  emptyText: { color: "#99a8be", fontSize: 14, textAlign: "center" },
  row: { flexDirection: "row", alignItems: "center", gap: 12, paddingVertical: 10 },
  rowAvatar: { width: 44, height: 44, borderRadius: 22, alignItems: "center", justifyContent: "center", backgroundColor: "#122239", borderWidth: 1, borderColor: "rgba(97,216,255,0.28)", overflow: "hidden" },
  rowAvatarImage: { width: "100%", height: "100%" },
  rowAvatarInitials: { color: colors.text, fontWeight: "900", fontSize: 17 },
  rowName: { color: colors.text, fontSize: 15, fontWeight: "700", flex: 1 },
  errorText: { color: "#ff92aa", fontWeight: "700", fontSize: 13, marginTop: 8, textAlign: "center" },
  inviteButton: { marginTop: 14, borderRadius: 22, backgroundColor: colors.accent, alignItems: "center", justifyContent: "center", minHeight: 50 },
  inviteText: { color: "#03100d", fontWeight: "900", fontSize: 15 },
  disabled: { opacity: 0.4 },
  pressed: { opacity: 0.72 }
});
