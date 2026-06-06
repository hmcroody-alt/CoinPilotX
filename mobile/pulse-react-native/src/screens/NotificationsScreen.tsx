import React, { useCallback, useEffect, useMemo, useState } from "react";
import { ActivityIndicator, FlatList, Linking, RefreshControl, Text, TextInput, TouchableOpacity, View } from "react-native";
import { pulseApi } from "../api/client";
import { colors, screenStyles } from "../styles/theme";

type PulseNotification = {
  id: number;
  type?: string;
  title?: string;
  body?: string;
  actor_name?: string;
  actor_avatar?: string;
  category?: string;
  content_type?: string;
  preview_text?: string;
  original_preview?: string;
  created_at?: string;
  read?: boolean;
  status?: string;
  deep_link?: string;
  target_url?: string;
  mobile_deep_link?: string;
  deepLink?: string;
  postId?: number | string;
  statusId?: number | string;
  commentId?: number | string;
  replyId?: number | string;
  conversationId?: number | string;
  metadata?: Record<string, unknown>;
};

type NotificationsResponse = {
  ok: boolean;
  notifications?: PulseNotification[];
  items?: PulseNotification[];
};

export function NotificationsScreen() {
  const [items, setItems] = useState<PulseNotification[]>([]);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState("");

  const load = useCallback(async () => {
    setError("");
    const data = await pulseApi<NotificationsResponse>("/api/pulse/notifications?limit=80");
    setItems(data.notifications || data.items || []);
  }, []);

  useEffect(() => {
    load()
      .catch(value => setError(value instanceof Error ? value.message : "Notifications could not load."))
      .finally(() => setLoading(false));
  }, [load]);

  async function refresh() {
    setRefreshing(true);
    load()
      .catch(value => setError(value instanceof Error ? value.message : "Notifications could not refresh."))
      .finally(() => setRefreshing(false));
  }

  async function markRead(noteId: number) {
    await pulseApi(`/api/pulse/notifications/${noteId}/read`, { method: "POST" });
    setItems(current => current.map(item => (item.id === noteId ? { ...item, read: true, status: "read" } : item)));
  }

  async function remove(noteId: number) {
    await pulseApi(`/api/pulse/notifications/${noteId}`, { method: "DELETE" });
    setItems(current => current.filter(item => item.id !== noteId));
  }

  if (loading) {
    return (
      <View style={screenStyles.centered}>
        <ActivityIndicator color={colors.accent} />
      </View>
    );
  }

  return (
    <FlatList
      style={screenStyles.screen}
      data={items}
      keyExtractor={(item, index) => String(item.id || index)}
      refreshControl={<RefreshControl refreshing={refreshing} onRefresh={refresh} tintColor={colors.accent} />}
      ListHeaderComponent={
        <View>
          <Text style={screenStyles.title}>Notifications</Text>
          <Text style={screenStyles.subtitle}>Replies, comments, messages, and Pulse activity with quick actions.</Text>
          {error ? <Text style={screenStyles.error}>{error}</Text> : null}
        </View>
      }
      ListEmptyComponent={<Text style={screenStyles.muted}>No notifications yet.</Text>}
      renderItem={({ item }) => <NotificationCard note={item} onRead={markRead} onDelete={remove} />}
      contentContainerStyle={{ paddingBottom: 28 }}
    />
  );
}

function NotificationCard({ note, onRead, onDelete }: { note: PulseNotification; onRead: (noteId: number) => Promise<void>; onDelete: (noteId: number) => Promise<void> }) {
  const [reply, setReply] = useState("");
  const [busy, setBusy] = useState("");
  const [message, setMessage] = useState("");
  const unread = !note.read && note.status !== "read";
  const canReply = useMemo(() => Boolean(readNumber(note.statusId) || readNumber(note.postId) || readNumber(note.conversationId)), [note]);
  const preview = note.preview_text || note.body || "Reply hidden or unavailable.";
  const actor = note.actor_name || "Pulse member";
  const label = note.content_type || note.category || note.type || "Pulse";

  async function open() {
    setBusy("open");
    try {
      if (note.id) await onRead(note.id);
      const url = String(note.mobile_deep_link || note.deepLink || note.deep_link || note.target_url || "");
      if (url) await Linking.openURL(url.startsWith("pulse://") ? webFallback(note, url) : url);
    } catch (value) {
      setMessage(value instanceof Error ? value.message : "Could not open this notification.");
    } finally {
      setBusy("");
    }
  }

  async function sendReply() {
    const body = reply.trim();
    if (!body) return;
    setBusy("reply");
    setMessage("");
    try {
      await sendQuickReply(note, body);
      setReply("");
      if (note.id) await onRead(note.id);
      setMessage("Reply sent.");
    } catch (value) {
      setMessage(value instanceof Error ? value.message : "Reply could not be sent.");
    } finally {
      setBusy("");
    }
  }

  return (
    <View style={[screenStyles.card, unread ? { borderColor: colors.accent } : null]}>
      <View style={{ flexDirection: "row", justifyContent: "space-between", gap: 10 }}>
        <View style={{ flex: 1 }}>
          <Text style={screenStyles.cardTitle}>{note.title || "Pulse update"}</Text>
          <Text style={screenStyles.muted}>{actor} · {label} · {formatTime(note.created_at)}</Text>
        </View>
        {unread ? <Text style={{ color: colors.accent, fontWeight: "800" }}>Unread</Text> : null}
      </View>
      <Text style={{ color: colors.text, fontSize: 15, lineHeight: 21, marginTop: 10 }}>{preview}</Text>
      {note.original_preview ? <Text style={[screenStyles.muted, { marginTop: 8 }]}>Original: {note.original_preview}</Text> : null}
      {canReply ? (
        <View style={{ marginTop: 12 }}>
          <TextInput
            style={[screenStyles.input, { marginBottom: 8 }]}
            value={reply}
            onChangeText={setReply}
            placeholder="Reply..."
            placeholderTextColor="#7890a8"
            multiline
          />
          <View style={{ flexDirection: "row", gap: 8 }}>
            <SmallButton label="😊" onPress={() => setReply(current => `${current}😊`)} disabled={busy !== ""} />
            <SmallButton label={busy === "reply" ? "Sending" : "Send"} onPress={sendReply} disabled={busy !== "" || !reply.trim()} primary />
          </View>
        </View>
      ) : null}
      {message ? <Text style={[screenStyles.muted, { marginTop: 8 }]}>{message}</Text> : null}
      <View style={{ flexDirection: "row", flexWrap: "wrap", gap: 8, marginTop: 12 }}>
        <SmallButton label={busy === "open" ? "Opening" : "Open"} onPress={open} disabled={busy !== ""} primary />
        <SmallButton label="Mark Read" onPress={() => onRead(note.id)} disabled={busy !== "" || !unread} />
        <SmallButton label="Delete" onPress={() => onDelete(note.id)} disabled={busy !== ""} danger />
      </View>
    </View>
  );
}

function SmallButton({ label, onPress, disabled, primary, danger }: { label: string; onPress: () => void; disabled?: boolean; primary?: boolean; danger?: boolean }) {
  return (
    <TouchableOpacity
      onPress={onPress}
      disabled={disabled}
      style={[
        primary ? screenStyles.button : screenStyles.secondaryButton,
        { marginTop: 0, minHeight: 40, opacity: disabled ? 0.55 : 1, paddingHorizontal: 12 },
        danger ? { borderColor: colors.danger } : null
      ]}
    >
      <Text style={primary ? screenStyles.buttonText : [screenStyles.secondaryButtonText, danger ? { color: colors.danger } : null]}>{label}</Text>
    </TouchableOpacity>
  );
}

async function sendQuickReply(note: PulseNotification, body: string) {
  const statusId = readNumber(note.statusId);
  if (statusId) {
    return pulseApi(`/api/pulse/status/${statusId}/reply`, { method: "POST", body: JSON.stringify({ body }) });
  }
  const postId = readNumber(note.postId);
  if (postId) {
    const parentCommentId = readNumber(note.commentId || note.replyId);
    return pulseApi(`/api/pulse/posts/${postId}/comments`, {
      method: "POST",
      body: JSON.stringify({ body, parent_comment_id: parentCommentId || null })
    });
  }
  const conversationId = readNumber(note.conversationId);
  if (conversationId) {
    return pulseApi("/api/pulse/messages/send", {
      method: "POST",
      body: JSON.stringify({ conversation_id: conversationId, body, message_type: "text" })
    });
  }
  throw new Error("This notification no longer supports quick reply.");
}

function readNumber(value: unknown) {
  const number = Number(value || 0);
  return Number.isFinite(number) && number > 0 ? number : 0;
}

function webFallback(note: PulseNotification, url: string) {
  const postId = readNumber(note.postId);
  const statusId = readNumber(note.statusId);
  const conversationId = readNumber(note.conversationId);
  if (postId) return `https://pulsesoc.com/pulse/post/${postId}`;
  if (statusId) return `https://pulsesoc.com/pulse/status?status_id=${statusId}`;
  if (conversationId) return `https://pulsesoc.com/pulse/messages/${conversationId}`;
  return url.replace("pulse://", "https://pulsesoc.com/");
}

function formatTime(value?: string) {
  if (!value) return "now";
  const time = Date.parse(value);
  if (!Number.isFinite(time)) return value;
  const diff = Math.max(0, Date.now() - time);
  const minutes = Math.floor(diff / 60000);
  if (minutes < 1) return "now";
  if (minutes < 60) return `${minutes}m ago`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours}h ago`;
  return `${Math.floor(hours / 24)}d ago`;
}
