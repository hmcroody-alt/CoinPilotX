import React, { useCallback, useEffect, useMemo, useState } from "react";
import { ActivityIndicator, FlatList, RefreshControl, Text, TextInput, TouchableOpacity, View } from "react-native";
import { colors, screenStyles } from "../styles/theme";
import {
  CommunicationsBootstrap,
  ConversationKind,
  ConversationSummary,
  flushQueuedMessages,
  loadCommunicationsBootstrap,
  markConversationRead,
  sendCommunicationMessage,
  sendTypingHeartbeat
} from "../services/communications";

const filters: Array<{ key: keyof CommunicationsBootstrap; label: string; kind: ConversationKind }> = [
  { key: "directs", label: "Direct", kind: "direct" },
  { key: "groups", label: "Groups", kind: "group" },
  { key: "rooms", label: "Rooms", kind: "room" },
  { key: "communities", label: "Communities", kind: "community" },
  { key: "channels", label: "Channels", kind: "channel" }
];

const emptyState: CommunicationsBootstrap = {
  directs: [],
  groups: [],
  rooms: [],
  communities: [],
  channels: [],
  queued: []
};

export function CommunicationsScreen() {
  const [data, setData] = useState<CommunicationsBootstrap>(emptyState);
  const [selected, setSelected] = useState<keyof CommunicationsBootstrap>("directs");
  const [active, setActive] = useState<ConversationSummary | null>(null);
  const [draft, setDraft] = useState("");
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [status, setStatus] = useState("");

  const load = useCallback(async () => {
    setStatus("");
    const next = await loadCommunicationsBootstrap();
    setData(next);
    if (!active) {
      const first = [...next.directs, ...next.groups, ...next.rooms, ...next.communities, ...next.channels][0];
      if (first) setActive(first);
    }
  }, [active]);

  useEffect(() => {
    load()
      .catch(error => setStatus(error instanceof Error ? error.message : "Communications could not load."))
      .finally(() => setLoading(false));
  }, [load]);

  async function refresh() {
    setRefreshing(true);
    await load().finally(() => setRefreshing(false));
  }

  async function selectConversation(conversation: ConversationSummary) {
    setActive(conversation);
    const id = conversationId(conversation);
    if (id) {
      markConversationRead(id).catch(() => undefined);
      setData(current => markLocalRead(current, id));
    }
  }

  async function sendDraft() {
    const body = draft.trim();
    const id = active ? conversationId(active) : "";
    if (!body || !id) return;
    setDraft("");
    const result = await sendCommunicationMessage({ conversationId: id, body, kind: active ? conversationKind(active) : "direct" });
    if (result.queued) setStatus("Offline queue saved this message. It will retry when the app is online.");
    else setStatus("Message sent.");
    const next = await loadCommunicationsBootstrap();
    setData(next);
  }

  async function flushQueue() {
    const result = await flushQueuedMessages();
    setStatus(`Queue sync: ${result.sent}/${result.attempted} sent, ${result.remaining} pending.`);
    const next = await loadCommunicationsBootstrap();
    setData(next);
  }

  async function typing() {
    const id = active ? conversationId(active) : "";
    if (id) sendTypingHeartbeat(id).catch(() => undefined);
  }

  const activeFilter = filters.find(filter => filter.key === selected) || filters[0];
  const items = selected === "queued" ? [] : (data[selected] as ConversationSummary[]);
  const allCount = filters.reduce((sum, filter) => sum + (data[filter.key] as ConversationSummary[]).length, 0);
  const activePreview = active ? previewText(active) : "Select a conversation to see previews, read state, presence, and queue controls.";

  if (loading) {
    return (
      <View style={screenStyles.centered}>
        <ActivityIndicator color={colors.accent} />
      </View>
    );
  }

  return (
    <View style={screenStyles.screen}>
      <FlatList
        data={items}
        keyExtractor={(item, index) => `${conversationId(item) || index}`}
        refreshControl={<RefreshControl refreshing={refreshing} onRefresh={refresh} tintColor={colors.accent} />}
        ListHeaderComponent={
          <View>
            <Text style={screenStyles.title}>Messages</Text>
            <Text style={screenStyles.subtitle}>Direct messages, groups, rooms, communities, and channels with previews, read receipts, typing, presence, offline queue, and notification deep links.</Text>
            <View style={{ flexDirection: "row", flexWrap: "wrap", gap: 8, marginBottom: 12 }}>
              {filters.map(filter => (
                <FilterButton
                  key={filter.key}
                  label={`${filter.label} ${(data[filter.key] as ConversationSummary[]).length}`}
                  active={selected === filter.key}
                  onPress={() => setSelected(filter.key)}
                />
              ))}
            </View>
            <View style={screenStyles.card}>
              <Text style={screenStyles.cardTitle}>{active ? titleText(active) : "Realtime-ready communications"}</Text>
              <Text style={screenStyles.muted}>{activePreview}</Text>
              {active ? (
                <View style={{ marginTop: 10 }}>
                  <Text style={screenStyles.muted}>Presence: {presenceText(active)} · Read: {readReceiptText(active)}</Text>
                  <TextInput
                    value={draft}
                    onChangeText={value => {
                      setDraft(value);
                      typing();
                    }}
                    placeholder="Write a message..."
                    placeholderTextColor={colors.muted}
                    style={[screenStyles.input, { marginTop: 10 }]}
                    multiline
                  />
                  <TouchableOpacity onPress={sendDraft} disabled={!draft.trim()} style={[screenStyles.button, { opacity: draft.trim() ? 1 : 0.55 }]}>
                    <Text style={screenStyles.buttonText}>Send Message</Text>
                  </TouchableOpacity>
                </View>
              ) : null}
            </View>
            <View style={screenStyles.card}>
              <Text style={screenStyles.cardTitle}>Offline Queue</Text>
              <Text style={screenStyles.muted}>{data.queued.length} pending message{data.queued.length === 1 ? "" : "s"} · {allCount} live conversation surface{allCount === 1 ? "" : "s"} loaded.</Text>
              <TouchableOpacity onPress={flushQueue} style={screenStyles.secondaryButton}>
                <Text style={screenStyles.secondaryButtonText}>Retry Queue</Text>
              </TouchableOpacity>
              {status ? <Text style={[screenStyles.muted, { marginTop: 8 }]}>{status}</Text> : null}
            </View>
            <Text style={[screenStyles.muted, { marginBottom: 8 }]}>{activeFilter.label}</Text>
          </View>
        }
        ListEmptyComponent={<Text style={screenStyles.muted}>No {activeFilter.label.toLowerCase()} conversations yet.</Text>}
        renderItem={({ item }) => (
          <ConversationCard
            conversation={item}
            selected={Boolean(active && conversationId(active) === conversationId(item))}
            onPress={() => selectConversation(item)}
          />
        )}
        contentContainerStyle={{ paddingBottom: 28 }}
      />
    </View>
  );
}

function ConversationCard({ conversation, selected, onPress }: { conversation: ConversationSummary; selected: boolean; onPress: () => void }) {
  const unread = Number(conversation.unread_count || 0);
  return (
    <TouchableOpacity onPress={onPress} style={[screenStyles.card, selected ? { borderColor: colors.accent } : null]}>
      <View style={{ flexDirection: "row", justifyContent: "space-between", gap: 10 }}>
        <View style={{ flex: 1 }}>
          <Text style={screenStyles.cardTitle}>{titleText(conversation)}</Text>
          <Text style={screenStyles.muted}>{conversationKind(conversation)} · {presenceText(conversation)}</Text>
        </View>
        {unread > 0 ? <Text style={{ color: colors.accent, fontWeight: "800" }}>{unread}</Text> : null}
      </View>
      <Text style={[screenStyles.muted, { marginTop: 8 }]}>{previewText(conversation)}</Text>
      <Text style={[screenStyles.muted, { marginTop: 6 }]}>{readReceiptText(conversation)} · Typing: {typingText(conversation)}</Text>
    </TouchableOpacity>
  );
}

function FilterButton({ label, active, onPress }: { label: string; active: boolean; onPress: () => void }) {
  return (
    <TouchableOpacity onPress={onPress} style={[screenStyles.secondaryButton, { minHeight: 38, marginTop: 0, paddingHorizontal: 10 }, active ? { borderColor: colors.accent } : null]}>
      <Text style={[screenStyles.secondaryButtonText, active ? { color: colors.accent } : null]}>{label}</Text>
    </TouchableOpacity>
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
  return String(conversation.kind || conversation.type || "direct");
}

function presenceText(conversation: ConversationSummary) {
  const online = Number(conversation.online_count || 0);
  if (online > 0) return `${online} online`;
  return String(conversation.presence || "presence pending");
}

function typingText(conversation: ConversationSummary) {
  const typing = conversation.typing_users || [];
  return typing.length ? typing.join(", ") : "none";
}

function readReceiptText(conversation: ConversationSummary) {
  if (conversation.read_at) return `read ${conversation.read_at}`;
  if (conversation.delivered_at) return `delivered ${conversation.delivered_at}`;
  return "read receipt pending";
}

function markLocalRead(data: CommunicationsBootstrap, id: string) {
  const mapItems = (items: ConversationSummary[]) => items.map(item => (conversationId(item) === id ? { ...item, unread_count: 0, read_at: new Date().toISOString() } : item));
  return {
    ...data,
    directs: mapItems(data.directs),
    groups: mapItems(data.groups),
    rooms: mapItems(data.rooms),
    communities: mapItems(data.communities),
    channels: mapItems(data.channels)
  };
}
