/**
 * The 2-column inbox tools grid at the foot of the list: Saved replies, Away mode
 * (an inline switch, its own tile), Spam & blocked, and Notifications. Each tile
 * shows a live count or summary when one is known and simply omits it when it is
 * not — no fabricated "0 templates" or invented channel list.
 *
 * The Away tile is the one interactive tile; the other three navigate to their
 * existing managers. When away mode is flag-off the tile renders disabled with an
 * honest subtitle rather than being hidden, so the seller can see the capability
 * exists but is not wired in this build.
 */

import { Pressable, StyleSheet, Text, View } from "react-native";
import { Ionicons } from "@expo/vector-icons";
import { messagesLight } from "../../theme/messagesLight";
import { InboxTools } from "../../api/commerceInbox";
import { AwayModeSwitchTile } from "./AwayModeSwitchTile";

function Tile({
  icon,
  title,
  subtitle,
  onPress,
  a11y
}: {
  icon: keyof typeof Ionicons.glyphMap;
  title: string;
  subtitle?: string;
  onPress?: () => void;
  a11y: string;
}) {
  return (
    <Pressable
      onPress={onPress}
      style={styles.tile}
      accessibilityRole="button"
      accessibilityLabel={a11y}
    >
      <View style={styles.head}>
        <Ionicons name={icon} size={18} color={messagesLight.text.primary} />
        <Text style={styles.title}>{title}</Text>
      </View>
      {subtitle ? (
        <Text style={styles.subtitle} numberOfLines={2}>
          {subtitle}
        </Text>
      ) : null}
    </Pressable>
  );
}

export function InboxToolsGrid({
  tools,
  awayEnabled,
  onSavedReplies,
  onToggleAway,
  onSpamBlocked,
  onNotifications
}: {
  tools: InboxTools;
  awayEnabled: boolean;
  onSavedReplies: () => void;
  onToggleAway: (next: boolean) => void;
  onSpamBlocked: () => void;
  onNotifications: () => void;
}) {
  const savedSub =
    tools.savedRepliesCount != null ? `${tools.savedRepliesCount} templates` : "Manage templates";
  const spamSub =
    tools.spamBlockedCount != null ? `${tools.spamBlockedCount} filtered` : "Review filtered";
  const notifSub = tools.notificationsSummary || "Manage alerts";

  return (
    <View style={styles.grid} accessibilityRole="menu">
      <View style={styles.rowPair}>
        <Tile
          icon="chatbox-ellipses-outline"
          title="Saved replies"
          subtitle={savedSub}
          onPress={onSavedReplies}
          a11y={`Saved replies. ${savedSub}`}
        />
        <AwayModeSwitchTile on={tools.awayOn} onToggle={onToggleAway} disabled={!awayEnabled} />
      </View>
      <View style={styles.rowPair}>
        <Tile
          icon="shield-outline"
          title="Spam & blocked"
          subtitle={spamSub}
          onPress={onSpamBlocked}
          a11y={`Spam and blocked. ${spamSub}`}
        />
        <Tile
          icon="notifications-outline"
          title="Notifications"
          subtitle={notifSub}
          onPress={onNotifications}
          a11y={`Notifications. ${notifSub}`}
        />
      </View>
    </View>
  );
}

const styles = StyleSheet.create({
  grid: { gap: 12, paddingHorizontal: messagesLight.space.card, paddingTop: 6 },
  rowPair: { flexDirection: "row", gap: 12 },
  tile: {
    flex: 1,
    minHeight: 84,
    gap: 8,
    padding: 12,
    borderRadius: messagesLight.radius.card,
    backgroundColor: messagesLight.bg.card,
    borderWidth: StyleSheet.hairlineWidth,
    borderColor: messagesLight.border.hairline
  },
  head: { flexDirection: "row", alignItems: "center", gap: 8 },
  title: { fontSize: 14, fontWeight: "800", color: messagesLight.text.primary },
  subtitle: { fontSize: 12, color: messagesLight.text.muted }
});
