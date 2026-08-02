/**
 * One row of the commerce inbox. It carries, left to right: the buyer's avatar
 * (with a presence dot when the product exposes presence), their name, a
 * timestamp, a one-line snippet (or a typing indicator), the single context chip
 * that says which money object this thread is about, and a right column that is
 * either an unread-count badge or a gold star.
 *
 * Unread rows get a barely-there cool tint and a 3px blue left edge — the same
 * blue as the Store/orders family — and their name + timestamp read blue and
 * bold. Every one of those signals is backed by text (the badge shows the number,
 * the row's accessibility label states "unread"), so unread is never conveyed by
 * colour alone.
 *
 * Tap opens the thread; long-press surfaces the existing row actions. The chip is
 * a *separate* control: tapping it deep-links to the object, not the thread.
 */

import { Pressable, StyleSheet, Text, View } from "react-native";
import { Animated } from "react-native";
import { Ionicons } from "@expo/vector-icons";
import { messagesLight } from "../../theme/messagesLight";
import { useStoreBadgePop } from "../../theme/storeMotion";
import { formatRelative } from "../../i18n/format";
import { ContextChipData, InboxRow } from "../../api/commerceInbox";
import { ContextChip } from "./ContextChip";
import { InboxAvatar } from "./InboxAvatar";
import { PresenceDot } from "./PresenceDot";
import { TypingIndicator } from "./TypingIndicator";

function isOnline(presence?: string): boolean {
  if (!presence) return false;
  const p = presence.toLowerCase();
  return p === "online" || p === "active" || p === "1" || p === "true";
}

/** Full spoken summary so a screen reader hears the whole row as one utterance. */
function rowA11yLabel(row: InboxRow, timestamp: string, online: boolean): string {
  const parts = [row.title];
  if (online) parts.push("online");
  if (row.unreadCount > 0) {
    parts.push(`${row.unreadCount} unread message${row.unreadCount === 1 ? "" : "s"}`);
  }
  if (row.starred) parts.push("starred");
  if (row.typing) parts.push("typing");
  else if (row.snippet) parts.push(`${row.ownLast ? "you said " : ""}${row.snippet}`);
  if (row.chip) parts.push(row.chip.a11yLabel);
  if (timestamp) parts.push(timestamp);
  return parts.join(". ");
}

export function ConversationRow({
  row,
  reducedMotion,
  presenceEnabled,
  typingEnabled,
  onPress,
  onLongPress,
  onChipPress
}: {
  row: InboxRow;
  reducedMotion: boolean;
  presenceEnabled: boolean;
  typingEnabled: boolean;
  onPress: (row: InboxRow) => void;
  onLongPress?: (row: InboxRow) => void;
  onChipPress?: (chip: ContextChipData, row: InboxRow) => void;
}) {
  const unread = row.unreadCount > 0;
  const online = presenceEnabled && isOnline(row.presence);
  const showTyping = typingEnabled && row.typing;
  const badgeScale = useStoreBadgePop(reducedMotion, unread);
  const timestamp = row.timestamp ? formatRelative(row.timestamp) : "";

  return (
    <Pressable
      onPress={() => onPress(row)}
      onLongPress={onLongPress ? () => onLongPress(row) : undefined}
      accessibilityRole="button"
      accessibilityLabel={rowA11yLabel(row, timestamp, online)}
      style={({ pressed }) => [
        styles.row,
        unread && styles.rowUnread,
        pressed && styles.rowPressed
      ]}
    >
      {unread ? <View style={styles.unreadEdge} /> : null}

      <View style={styles.avatarWrap}>
        <InboxAvatar name={row.title} colorKey={row.colorKey} avatarUrl={row.avatarUrl} />
        {online ? (
          <View style={styles.presence}>
            <PresenceDot reducedMotion={reducedMotion} />
          </View>
        ) : null}
      </View>

      <View style={styles.body}>
        <View style={styles.topLine}>
          <Text
            style={[styles.name, unread && styles.nameUnread]}
            numberOfLines={1}
            ellipsizeMode="tail"
          >
            {row.title}
          </Text>
          {timestamp ? (
            <Text style={[styles.time, unread && styles.timeUnread]}>{timestamp}</Text>
          ) : null}
        </View>

        <View style={styles.snippetLine}>
          {showTyping ? (
            <TypingIndicator reducedMotion={reducedMotion} />
          ) : (
            <Text style={[styles.snippet, unread && styles.snippetUnread]} numberOfLines={1} ellipsizeMode="tail">
              {row.ownLast && row.snippet ? <Text style={styles.youPrefix}>You: </Text> : null}
              {row.snippet}
            </Text>
          )}
        </View>

        {row.chip ? (
          <View style={styles.chipLine}>
            <ContextChip chip={row.chip} onPress={onChipPress ? (c) => onChipPress(c, row) : undefined} />
          </View>
        ) : null}
      </View>

      <View style={styles.right}>
        {unread ? (
          <Animated.View
            style={[styles.badge, { transform: [{ scale: badgeScale }] }]}
            accessibilityElementsHidden
            importantForAccessibility="no"
          >
            <Text style={styles.badgeText}>{row.unreadCount > 99 ? "99+" : row.unreadCount}</Text>
          </Animated.View>
        ) : row.starred ? (
          <Ionicons name="star" size={16} color={messagesLight.status.warning} />
        ) : null}
      </View>
    </Pressable>
  );
}

const styles = StyleSheet.create({
  row: {
    flexDirection: "row",
    alignItems: "flex-start",
    gap: 12,
    paddingVertical: 12,
    paddingHorizontal: messagesLight.space.card,
    backgroundColor: messagesLight.bg.card,
    borderBottomWidth: StyleSheet.hairlineWidth,
    borderBottomColor: messagesLight.border.hairline
  },
  rowUnread: { backgroundColor: messagesLight.bg.unread },
  rowPressed: { opacity: 0.9 },
  unreadEdge: {
    position: "absolute",
    left: 0,
    top: 0,
    bottom: 0,
    width: 3,
    backgroundColor: messagesLight.border.unreadEdge
  },
  avatarWrap: { width: messagesLight.size.avatar, height: messagesLight.size.avatar },
  presence: { position: "absolute", right: -1, bottom: -1 },
  body: { flex: 1, gap: 3, minWidth: 0 },
  topLine: { flexDirection: "row", alignItems: "center", gap: 8 },
  name: { flex: 1, fontSize: 15, fontWeight: "600", color: messagesLight.text.primary },
  nameUnread: { fontWeight: "800", color: messagesLight.text.unread },
  time: { fontSize: 12, color: messagesLight.text.muted },
  timeUnread: { color: messagesLight.text.unread, fontWeight: "700" },
  snippetLine: { minHeight: 18, justifyContent: "center" },
  snippet: { fontSize: 13, color: messagesLight.text.muted },
  snippetUnread: { color: messagesLight.text.primary },
  youPrefix: { color: messagesLight.text.muted },
  chipLine: { marginTop: 2 },
  right: { minWidth: 26, alignItems: "flex-end", justifyContent: "center", paddingTop: 2 },
  badge: {
    minWidth: 20,
    height: 20,
    paddingHorizontal: 6,
    borderRadius: 10,
    backgroundColor: messagesLight.unreadBadge.bg,
    alignItems: "center",
    justifyContent: "center"
  },
  badgeText: { color: messagesLight.unreadBadge.text, fontSize: 11, fontWeight: "800" }
});
