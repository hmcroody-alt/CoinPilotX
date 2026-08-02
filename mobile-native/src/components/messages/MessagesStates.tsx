/**
 * The non-content states of the commerce inbox: loading skeleton, empty inbox,
 * empty filter (per-filter copy), an inline error with retry, and an honest
 * offline banner. Kept in one file so every state reads as the same surface.
 *
 * The offline banner never claims freshness — it says the list may be stale and
 * offers a refresh, matching the Orders surface.
 */

import { StyleSheet, Text, View } from "react-native";
import { Pressable } from "react-native";
import { messagesLight } from "../../theme/messagesLight";
import { InboxFilter } from "../../api/commerceInbox";

/** Six ghost rows that echo the real row layout (avatar + two text lines). */
export function MessagesSkeleton({ rows = 6 }: { rows?: number }) {
  return (
    <View accessibilityLabel="Loading conversations">
      {Array.from({ length: rows }).map((_, i) => (
        <View key={i} style={styles.skelRow}>
          <View style={styles.skelAvatar} />
          <View style={styles.skelBody}>
            <View style={[styles.skelBar, { width: "45%" }]} />
            <View style={[styles.skelBar, { width: "80%" }]} />
            <View style={[styles.skelChip, { width: "38%" }]} />
          </View>
        </View>
      ))}
    </View>
  );
}

export function MessagesEmpty() {
  return (
    <View style={styles.center} accessibilityLabel="No conversations yet. When a buyer messages you about an offer, order or listing, it shows up here.">
      <Text style={styles.title}>No conversations yet</Text>
      <Text style={styles.body}>
        When a buyer messages you about an offer, order or listing, it shows up here — with the item
        it's about right on the row.
      </Text>
    </View>
  );
}

const FILTER_EMPTY: Record<InboxFilter, { title: string; body: string }> = {
  all: { title: "No conversations yet", body: "Buyer messages will show up here." },
  unread: { title: "You're all caught up", body: "No unread conversations right now." },
  offers: { title: "No offer threads", body: "Conversations tied to an active offer will appear here." },
  orders: { title: "No order threads", body: "Conversations tied to an order or pickup will appear here." },
  starred: { title: "No starred conversations", body: "Star a thread to keep it here." },
  archived: { title: "Nothing archived", body: "Archived conversations will appear here." }
};

export function MessagesFilterEmpty({ filter }: { filter: InboxFilter }) {
  const copy = FILTER_EMPTY[filter];
  return (
    <View style={styles.center} accessibilityLabel={`${copy.title}. ${copy.body}`}>
      <Text style={styles.title}>{copy.title}</Text>
      <Text style={styles.body}>{copy.body}</Text>
    </View>
  );
}

export function MessagesError({ message, onRetry }: { message?: string; onRetry: () => void }) {
  return (
    <View style={styles.center} accessibilityLabel={message || "Messages could not load."}>
      <Text style={styles.title}>Couldn't load messages</Text>
      <Text style={styles.body}>{message || "Something went wrong reaching your inbox."}</Text>
      <Pressable onPress={onRetry} style={styles.retry} accessibilityRole="button" accessibilityLabel="Retry">
        <Text style={styles.retryText}>Retry</Text>
      </Pressable>
    </View>
  );
}

export function MessagesOffline({ message }: { message?: string }) {
  return (
    <View style={styles.offline} accessibilityLabel={message || "Showing saved conversations. Pull to refresh."}>
      <Text style={styles.offlineText}>
        {message || "Showing saved conversations — this list may be out of date. Pull to refresh."}
      </Text>
    </View>
  );
}

const styles = StyleSheet.create({
  center: { alignItems: "center", justifyContent: "center", gap: 8, paddingVertical: 48, paddingHorizontal: 24 },
  title: { fontSize: 17, fontWeight: "800", color: messagesLight.text.primary },
  body: { fontSize: 13, lineHeight: 19, color: messagesLight.text.muted, textAlign: "center" },
  retry: {
    marginTop: 10,
    paddingHorizontal: 18,
    height: 40,
    borderRadius: messagesLight.radius.control,
    alignItems: "center",
    justifyContent: "center",
    borderWidth: StyleSheet.hairlineWidth,
    borderColor: messagesLight.border.secondaryButton,
    backgroundColor: messagesLight.bg.card
  },
  retryText: { fontSize: 14, fontWeight: "800", color: messagesLight.text.link },
  offline: {
    backgroundColor: messagesLight.bg.warning,
    borderColor: messagesLight.border.warning,
    borderWidth: StyleSheet.hairlineWidth,
    borderRadius: messagesLight.radius.control,
    paddingHorizontal: 12,
    paddingVertical: 9,
    marginHorizontal: messagesLight.space.gutter
  },
  offlineText: { fontSize: 12, color: messagesLight.text.primary },
  skelRow: {
    flexDirection: "row",
    gap: 12,
    paddingVertical: 12,
    paddingHorizontal: messagesLight.space.card,
    borderBottomWidth: StyleSheet.hairlineWidth,
    borderBottomColor: messagesLight.border.hairline
  },
  skelAvatar: {
    width: messagesLight.size.avatar,
    height: messagesLight.size.avatar,
    borderRadius: messagesLight.size.avatar / 2,
    backgroundColor: messagesLight.bg.skeleton
  },
  skelBody: { flex: 1, gap: 8, paddingTop: 4 },
  skelBar: { height: 11, borderRadius: 6, backgroundColor: messagesLight.bg.skeleton },
  skelChip: { height: 18, borderRadius: 8, backgroundColor: messagesLight.bg.skeleton }
});
