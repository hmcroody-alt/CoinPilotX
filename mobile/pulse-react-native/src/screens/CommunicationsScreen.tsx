import React, { useCallback, useEffect, useMemo, useState } from "react";
import { ActivityIndicator, FlatList, KeyboardAvoidingView, Platform, RefreshControl, Text, TextInput, TouchableOpacity, View } from "react-native";
import { MaterialCommunityIcons } from "@expo/vector-icons";
import { colors, screenStyles } from "../styles/theme";
import { PulseTopBar } from "../../components/PulseChrome";
import {
  CommunicationMessage,
  CommunicationsBootstrap,
  ConversationSummary,
  flushQueuedMessages,
  loadCommunicationsBootstrap,
  loadConversationMessages,
  markConversationRead,
  sendCommunicationMessage,
  sendTypingHeartbeat
} from "../services/communications";
import { useAuthStore } from "../../store/authStore";

const filters: Array<{ key: keyof CommunicationsBootstrap; label: string }> = [
  { key: "all", label: "All" },
  { key: "direct", label: "DM" },
  { key: "group", label: "Groups" },
  { key: "room", label: "Rooms" },
  { key: "community_channel", label: "Channels" }
];

const emptyState: CommunicationsBootstrap = {
  all: [],
  direct: [],
  group: [],
  room: [],
  community_channel: [],
  queued: []
};

export function CommunicationsScreen() {
  const [data, setData] = useState<CommunicationsBootstrap>(emptyState);
  const [selected, setSelected] = useState<keyof CommunicationsBootstrap>("all");
  const [active, setActive] = useState<ConversationSummary | null>(null);
  const [messages, setMessages] = useState<CommunicationMessage[]>([]);
  const [draft, setDraft] = useState("");
  const [loading, setLoading] = useState(true);
  const [threadLoading, setThreadLoading] = useState(false);
  const [refreshing, setRefreshing] = useState(false);
  const [status, setStatus] = useState("");
  const userId = useAuthStore(state => state.user?.user_id);

  const load = useCallback(async () => {
    setStatus("");
    const next = await loadCommunicationsBootstrap();
    setData(next);
    const first = active || next.all[0] || null;
    if (first && (!active || !conversationId(active))) setActive(first);
    return first;
  }, [active]);

  const loadThread = useCallback(async (conversation: ConversationSummary | null) => {
    const id = conversation ? conversationId(conversation) : "";
    if (!id) {
      setMessages([]);
      return;
    }
    setThreadLoading(true);
    try {
      const result = await loadConversationMessages(id);
      setMessages(result.messages);
      markConversationRead(id).catch(() => undefined);
    } catch (error) {
      setStatus(error instanceof Error ? error.message : "Messages could not load yet. Pull to refresh or choose another chat.");
      setMessages([]);
    } finally {
      setThreadLoading(false);
    }
  }, []);

  useEffect(() => {
    load()
      .then(first => loadThread(first))
      .catch(error => setStatus(error instanceof Error ? error.message : "Communications could not load."))
      .finally(() => setLoading(false));
  }, []);

  async function refresh() {
    setRefreshing(true);
    try {
      const first = await load();
      await loadThread(active || first);
    } finally {
      setRefreshing(false);
    }
  }

  async function selectConversation(conversation: ConversationSummary) {
    setActive(conversation);
    setDraft("");
    await loadThread(conversation);
    const id = conversationId(conversation);
    if (id) {
      setData(current => markLocalRead(current, id));
    }
  }

  async function sendDraft() {
    const body = draft.trim();
    const id = active ? conversationId(active) : "";
    if (!body || !id) return;
    setDraft("");
    const optimistic: CommunicationMessage = {
      id: `local-${Date.now()}`,
      conversation_id: id,
      body,
      message_type: "text",
      created_at: new Date().toISOString(),
      read_at: "",
      delivered_at: ""
    };
    setMessages(current => [...current, optimistic]);
    const result = await sendCommunicationMessage({ conversationId: id, body, kind: active ? conversationKind(active) : "direct" });
    setStatus(result.queued ? "Offline queue saved this message. It will retry when the app is online." : "");
    const next = await loadCommunicationsBootstrap();
    setData(next);
    await loadThread(active);
  }

  async function flushQueue() {
    const result = await flushQueuedMessages();
    setStatus(`Queue sync: ${result.sent}/${result.attempted} sent, ${result.remaining} pending.`);
    setData(await loadCommunicationsBootstrap());
  }

  function typing(value: string) {
    setDraft(value);
    const id = active ? conversationId(active) : "";
    if (id) sendTypingHeartbeat(id).catch(() => undefined);
  }

  const activeFilter = filters.find(filter => filter.key === selected) || filters[0];
  const items = selected === "queued" ? [] : (data[selected] as ConversationSummary[]);
  const visibleMessages = useMemo(() => messages.slice(-80), [messages]);

  if (loading) {
    return (
      <View style={screenStyles.centered}>
        <ActivityIndicator color={colors.accent} />
      </View>
    );
  }

  return (
    <KeyboardAvoidingView style={{ flex: 1, backgroundColor: colors.background }} behavior={Platform.OS === "ios" ? "padding" : undefined}>
      <View style={screenStyles.screen}>
        <FlatList
          data={items}
          keyExtractor={(item, index) => `${conversationId(item) || index}`}
          horizontal
          showsHorizontalScrollIndicator={false}
          refreshControl={<RefreshControl refreshing={refreshing} onRefresh={refresh} tintColor={colors.accent} />}
          ListHeaderComponent={
            <View style={{ width: 260, marginRight: 8 }}>
              <PulseTopBar subtitle="Chats" />
              <Text style={screenStyles.title}>Messages</Text>
              <Text style={screenStyles.subtitle}>Direct messages, groups, rooms, and community channels from PulseSoc.</Text>
              <View style={{ flexDirection: "row", flexWrap: "wrap", gap: 8 }}>
                {filters.map(filter => (
                  <FilterButton
                    key={filter.key}
                    label={`${filter.label} ${(data[filter.key] as ConversationSummary[]).length}`}
                    active={selected === filter.key}
                    onPress={() => setSelected(filter.key)}
                  />
                ))}
              </View>
            </View>
          }
          ListEmptyComponent={<EmptyConversations label={activeFilter.label} queued={data.queued.length} onRetry={flushQueue} />}
          renderItem={({ item }) => (
            <ConversationPill
              conversation={item}
              selected={Boolean(active && conversationId(active) === conversationId(item))}
              onPress={() => selectConversation(item)}
            />
          )}
          style={{ maxHeight: 142, flexGrow: 0, marginBottom: 10 }}
        />

        <View style={[screenStyles.card, { flex: 1, padding: 0, overflow: "hidden" }]}>
          <ThreadHeader conversation={active} />
          {threadLoading ? (
            <View style={{ flex: 1, alignItems: "center", justifyContent: "center" }}>
              <ActivityIndicator color={colors.accent} />
            </View>
          ) : (
            <FlatList
              data={visibleMessages}
              keyExtractor={(item, index) => String(item.id || item.message_id || index)}
              renderItem={({ item }) => <MessageBubble message={item} mine={isMine(item, userId)} />}
              ListEmptyComponent={<Text style={[screenStyles.muted, { padding: 16 }]}>No messages yet. Start with a quick hello or attach media from the web Messages V2 composer.</Text>}
              contentContainerStyle={{ padding: 12, flexGrow: 1, justifyContent: visibleMessages.length ? "flex-end" : "center" }}
            />
          )}
          <Composer value={draft} onChange={typing} onSend={sendDraft} disabled={!active || !draft.trim()} />
        </View>
        {status ? <Text style={[screenStyles.muted, { marginTop: 8 }]}>{status}</Text> : null}
      </View>
    </KeyboardAvoidingView>
  );
}

function ThreadHeader({ conversation }: { conversation: ConversationSummary | null }) {
  const title = conversation ? titleText(conversation) : "Select a conversation";
  return (
    <View style={{ flexDirection: "row", alignItems: "center", gap: 10, padding: 12, borderBottomWidth: 1, borderBottomColor: colors.border }}>
      <Avatar label={title} />
      <View style={{ flex: 1 }}>
        <Text style={screenStyles.cardTitle}>{title}</Text>
        <Text style={screenStyles.muted}>{conversation ? `${presenceText(conversation)} · ${conversationKind(conversation)}` : "Choose a chat to start."}</Text>
      </View>
      <MaterialCommunityIcons name="magnify" color={colors.muted} size={22} />
      <MaterialCommunityIcons name="information-outline" color={colors.muted} size={22} />
    </View>
  );
}

function Composer({ value, onChange, onSend, disabled }: { value: string; onChange: (value: string) => void; onSend: () => void; disabled: boolean }) {
  return (
    <View style={{ flexDirection: "row", alignItems: "center", gap: 8, padding: 10, borderTopWidth: 1, borderTopColor: colors.border }}>
      <TouchableOpacity style={circleButtonStyle}>
        <MaterialCommunityIcons name="plus" color={colors.accent} size={24} />
      </TouchableOpacity>
      <TextInput
        value={value}
        onChangeText={onChange}
        placeholder="Type a message"
        placeholderTextColor={colors.muted}
        style={[screenStyles.input, { flex: 1, marginBottom: 0, borderRadius: 999 }]}
        multiline
      />
      <TouchableOpacity style={circleButtonStyle}>
        <MaterialCommunityIcons name="microphone" color={colors.accent} size={22} />
      </TouchableOpacity>
      <TouchableOpacity onPress={onSend} disabled={disabled} style={[circleButtonStyle, { backgroundColor: colors.accent, opacity: disabled ? 0.55 : 1 }]}>
        <MaterialCommunityIcons name="send" color={colors.background} size={22} />
      </TouchableOpacity>
    </View>
  );
}

function ConversationPill({ conversation, selected, onPress }: { conversation: ConversationSummary; selected: boolean; onPress: () => void }) {
  const unread = Number(conversation.unread_count || 0);
  const title = titleText(conversation);
  return (
    <TouchableOpacity onPress={onPress} style={[screenStyles.card, { width: 190, marginRight: 8, padding: 10 }, selected ? { borderColor: colors.accent } : null]}>
      <View style={{ flexDirection: "row", alignItems: "center", gap: 8 }}>
        <Avatar label={title} />
        <View style={{ flex: 1 }}>
          <Text style={screenStyles.cardTitle} numberOfLines={1}>{title}</Text>
          <Text style={screenStyles.muted} numberOfLines={1}>{previewText(conversation)}</Text>
        </View>
        {unread > 0 ? <Text style={{ color: colors.accent, fontWeight: "900" }}>{unread}</Text> : null}
      </View>
      <Text style={[screenStyles.muted, { marginTop: 8 }]}>{presenceText(conversation)} · {typingText(conversation)} · {readReceiptText(conversation)}</Text>
    </TouchableOpacity>
  );
}

function MessageBubble({ message, mine }: { message: CommunicationMessage; mine: boolean }) {
  const body = String(message.body || message.content || "");
  const attachments = message.attachments || [];
  return (
    <View style={{ alignItems: mine ? "flex-end" : "flex-start", marginVertical: 5 }}>
      <View style={{ maxWidth: "84%", borderRadius: 18, padding: 12, backgroundColor: mine ? "#126f5b" : colors.surfaceSoft }}>
        {body ? <Text style={{ color: colors.text, fontSize: 15, lineHeight: 21 }}>{body}</Text> : null}
        {attachments.length ? <Text style={[screenStyles.muted, { color: mine ? "#d8fff2" : colors.muted, marginTop: body ? 6 : 0 }]}>{attachments.length} attachment{attachments.length === 1 ? "" : "s"}</Text> : null}
        <Text style={{ color: mine ? "#bdf7e5" : colors.muted, fontSize: 11, marginTop: 5 }}>{formatTime(message.created_at)}{mine && message.read_at ? " / read" : mine && message.delivered_at ? " / delivered" : ""}</Text>
      </View>
    </View>
  );
}

function EmptyConversations({ label, queued, onRetry }: { label: string; queued: number; onRetry: () => void }) {
  return (
    <View style={[screenStyles.card, { width: 220 }]}>
      <Text style={screenStyles.cardTitle}>No {label.toLowerCase()} yet</Text>
      <Text style={screenStyles.muted}>{queued} queued message{queued === 1 ? "" : "s"} waiting to retry.</Text>
      {queued ? (
        <TouchableOpacity onPress={onRetry} style={screenStyles.secondaryButton}>
          <Text style={screenStyles.secondaryButtonText}>Retry Queue</Text>
        </TouchableOpacity>
      ) : null}
    </View>
  );
}

function FilterButton({ label, active, onPress }: { label: string; active: boolean; onPress: () => void }) {
  return (
    <TouchableOpacity onPress={onPress} style={[screenStyles.secondaryButton, { minHeight: 34, marginTop: 0, paddingHorizontal: 10 }, active ? { borderColor: colors.accent } : null]}>
      <Text style={[screenStyles.secondaryButtonText, { fontSize: 12 }, active ? { color: colors.accent } : null]}>{label}</Text>
    </TouchableOpacity>
  );
}

function Avatar({ label }: { label: string }) {
  return (
    <View style={{ width: 38, height: 38, borderRadius: 19, backgroundColor: colors.surfaceSoft, alignItems: "center", justifyContent: "center" }}>
      <Text style={{ color: colors.accent, fontWeight: "900" }}>{label.slice(0, 2).toUpperCase()}</Text>
    </View>
  );
}

function conversationId(conversation: ConversationSummary) {
  return String(conversation.conversation_id || conversation.id || conversation.room_id || conversation.group_id || conversation.community_id || conversation.channel_id || "");
}

function titleText(conversation: ConversationSummary) {
  return String(conversation.title || conversation.name || conversation.display_name || "Pulse conversation");
}

function previewText(conversation: ConversationSummary) {
  return String(conversation.preview_text || conversation.last_message_preview || "No messages yet.");
}

function conversationKind(conversation: ConversationSummary) {
  return String(conversation.conversation_type || conversation.kind || conversation.type || "direct").replace("_", " ");
}

function presenceText(conversation: ConversationSummary) {
  const online = Number(conversation.online_count || 0);
  if (online > 0) return `${online} online`;
  return String(conversation.presence || "presence pending");
}

function typingText(conversation: ConversationSummary) {
  const typingUsers = Array.isArray(conversation.typing_users) ? conversation.typing_users : [];
  if (typingUsers.length === 1) return "typing now";
  if (typingUsers.length > 1) return `${typingUsers.length} typing`;
  return "typing ready";
}

function readReceiptText(conversation: ConversationSummary) {
  if (conversation.read_at) return "read";
  if (conversation.delivered_at) return "delivered";
  return "read receipts";
}

function isMine(message: CommunicationMessage, userId: unknown) {
  return Boolean(userId && String(message.sender_user_id || "") === String(userId));
}

function formatTime(value?: string) {
  if (!value) return "now";
  const time = Date.parse(value);
  if (!Number.isFinite(time)) return "now";
  return new Intl.DateTimeFormat(undefined, { hour: "numeric", minute: "2-digit" }).format(new Date(time));
}

function markLocalRead(data: CommunicationsBootstrap, id: string) {
  const mapItems = (items: ConversationSummary[]) => items.map(item => (conversationId(item) === id ? { ...item, unread_count: 0, read_at: new Date().toISOString() } : item));
  return {
    ...data,
    all: mapItems(data.all),
    direct: mapItems(data.direct),
    group: mapItems(data.group),
    room: mapItems(data.room),
    community_channel: mapItems(data.community_channel)
  };
}

const circleButtonStyle = {
  width: 44,
  height: 44,
  borderRadius: 22,
  alignItems: "center" as const,
  justifyContent: "center" as const,
  borderWidth: 1,
  borderColor: colors.border,
  backgroundColor: colors.surfaceSoft
};
