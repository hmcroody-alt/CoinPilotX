import { ResizeMode, Video } from "expo-av";
import { NativeStackScreenProps } from "@react-navigation/native-stack";
import { useEffect, useMemo, useRef, useState } from "react";
import {
  ActivityIndicator,
  AppState,
  FlatList,
  Image,
  KeyboardAvoidingView,
  Platform,
  Pressable,
  RefreshControl,
  Share,
  StyleSheet,
  Text,
  TextInput,
  View
} from "react-native";
import {
  getLiveState,
  joinLive,
  listLiveChat,
  listLiveNow,
  livePlaybackUrl,
  livePosterUrl,
  liveSupportsNativePlayback,
  liveWebUrl,
  loadCachedLiveDiscovery,
  loadCachedLiveState,
  openLiveWebFallback,
  PulseLiveChatMessage,
  PulseLiveItem,
  PulseLiveState,
  reactToLive,
  sendLiveChat
} from "../api/live";
import { RootStackParamList } from "../navigation/types";
import { colors } from "../theme/colors";
import { formatShortTime } from "../utils/format";

type Props = Partial<NativeStackScreenProps<RootStackParamList, "LiveDetail">>;

export function LiveScreen({ route, navigation }: Props) {
  const initialLiveId = Number(route?.params?.liveId || 0);
  const [items, setItems] = useState<PulseLiveItem[]>([]);
  const [scheduled, setScheduled] = useState<PulseLiveItem[]>([]);
  const [selected, setSelected] = useState<PulseLiveItem | null>(null);
  const [state, setState] = useState<PulseLiveState | null>(null);
  const [messages, setMessages] = useState<PulseLiveChatMessage[]>([]);
  const [chatBody, setChatBody] = useState("");
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [offline, setOffline] = useState(false);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState("");
  const [joined, setJoined] = useState(false);
  const [muted, setMuted] = useState(true);
  const [playbackFailed, setPlaybackFailed] = useState(false);
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  async function load(mode: "initial" | "refresh" = "initial") {
    setError("");
    setOffline(false);
    if (mode === "initial") setLoading(true);
    if (mode === "refresh") setRefreshing(true);
    try {
      const data = await listLiveNow({ limit: 24 });
      setItems(data.items || []);
      setScheduled(data.scheduled || []);
      if (initialLiveId) {
        const focused = (data.items || []).find((item) => item.id === initialLiveId);
        if (focused) openLive(focused).catch(() => undefined);
        else openLiveById(initialLiveId).catch(() => undefined);
      }
    } catch (loadError) {
      const cached = await loadCachedLiveDiscovery();
      if (cached.items?.length || cached.scheduled?.length) {
        setItems(cached.items || []);
        setScheduled(cached.scheduled || []);
        setOffline(true);
      } else {
        setError(loadError instanceof Error ? loadError.message : "Live discovery could not load.");
      }
      if (initialLiveId) openLiveById(initialLiveId).catch(() => undefined);
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }

  async function openLive(item: PulseLiveItem) {
    setSelected(item);
    setState(null);
    setMessages([]);
    setJoined(false);
    setPlaybackFailed(false);
    await refreshLiveState(item.id, "open");
    handleJoin(item.id).catch(() => undefined);
  }

  async function openLiveById(liveId: number) {
    const cached = await loadCachedLiveState(liveId);
    if (cached?.discovery) {
      setSelected(cached.discovery);
      setState(cached);
      setMessages(cached.messages || []);
    } else {
      setSelected({ id: liveId, live_id: liveId, title: "PulseSoc Live", creator_name: "PulseSoc Creator" });
    }
    setPlaybackFailed(false);
    await refreshLiveState(liveId, "open");
    handleJoin(liveId).catch(() => undefined);
  }

  async function refreshLiveState(liveId: number, mode: "open" | "poll" | "manual" = "manual") {
    if (mode !== "poll") setError("");
    try {
      const next = await getLiveState(liveId);
      setState(next);
      setMessages(next.messages || []);
      if (next.discovery) setSelected(next.discovery);
    } catch (stateError) {
      const cached = await loadCachedLiveState(liveId);
      if (cached) {
        setState(cached);
        setMessages(cached.messages || []);
        setOffline(true);
      } else if (mode !== "poll") {
        setError(stateError instanceof Error ? stateError.message : "Live state could not load.");
      }
    }
  }

  async function handleJoin(liveId: number) {
    setBusy("join");
    try {
      const result = await joinLive(liveId);
      setJoined(Boolean(result.ok || result.status === "watching"));
      if (typeof result.viewer_count === "number") {
        setState((current) => current ? { ...current, viewer_count: result.viewer_count } : current);
      }
    } catch (joinError) {
      setError(joinError instanceof Error ? joinError.message : "Live join failed.");
    } finally {
      setBusy("");
    }
  }

  async function handleReact(reactionType = "fire") {
    if (!activeLiveId) return;
    setBusy(`react-${reactionType}`);
    try {
      await reactToLive(activeLiveId, reactionType);
      await refreshLiveState(activeLiveId, "manual");
    } catch (reactError) {
      setError(reactError instanceof Error ? reactError.message : "Live reaction failed.");
    } finally {
      setBusy("");
    }
  }

  async function handleSendChat() {
    if (!activeLiveId || !chatBody.trim()) return;
    const body = chatBody.trim();
    setChatBody("");
    setBusy("chat");
    try {
      const result = await sendLiveChat(activeLiveId, body);
      if (result.chat) setMessages((current) => mergeMessages([...current, result.chat ? result.chat : undefined]));
      await refreshLiveState(activeLiveId, "manual");
    } catch (chatError) {
      setChatBody(body);
      setError(chatError instanceof Error ? chatError.message : "Live chat could not send.");
    } finally {
      setBusy("");
    }
  }

  function closeViewer() {
    setSelected(null);
    setState(null);
    setMessages([]);
    setJoined(false);
    setPlaybackFailed(false);
    setError("");
    if (initialLiveId && navigation?.canGoBack()) navigation.goBack();
  }

  function navigateToHostProfile(item: PulseLiveItem | null | undefined) {
    const profileKey = String(item?.author?.username || item?.author?.public_player_id || item?.author?.user_id || "").trim();
    if (profileKey) navigation?.navigate("ProfileDetail", { profileKey, title: item?.creator_name || "Profile" });
  }

  useEffect(() => {
    load("initial").catch(() => undefined);
    return () => {
      if (pollRef.current) clearInterval(pollRef.current);
    };
  }, [initialLiveId]);

  useEffect(() => {
    if (pollRef.current) clearInterval(pollRef.current);
    if (selected?.id) {
      pollRef.current = setInterval(() => {
        refreshLiveState(selected.id, "poll").catch(() => undefined);
        listLiveChat(selected.id).then(setMessages).catch(() => undefined);
      }, 8000);
    }
    return () => {
      if (pollRef.current) clearInterval(pollRef.current);
    };
  }, [selected?.id]);

  useEffect(() => {
    const subscription = AppState.addEventListener("change", (status) => {
      if (status !== "active") return;
      if (selected?.id) {
        refreshLiveState(selected.id, "manual").catch(() => undefined);
        listLiveChat(selected.id).then(setMessages).catch(() => undefined);
      } else {
        load("refresh").catch(() => undefined);
      }
    });
    return () => subscription.remove();
  }, [selected?.id]);

  const active = state?.discovery || selected;
  const activeLiveId = Number(active?.id || state?.live_id || 0);
  const playbackUrl = useMemo(() => livePlaybackUrl(state || active), [state, active]);
  const posterUrl = useMemo(() => livePosterUrl(state || active), [state, active]);
  const canPlayNative = liveSupportsNativePlayback(state || active) && !playbackFailed;

  useEffect(() => {
    setPlaybackFailed(false);
  }, [playbackUrl, activeLiveId]);

  if (loading && !items.length && !active) {
    return (
      <View style={styles.center}>
        <ActivityIndicator color={colors.accent} />
        <Text style={styles.centerText}>Loading Live</Text>
      </View>
    );
  }

  if (active) {
    return (
      <KeyboardAvoidingView style={styles.root} behavior={Platform.OS === "ios" ? "padding" : undefined}>
        <View style={styles.viewer}>
          <View style={styles.viewerTopBar}>
            <Pressable style={styles.closeButton} onPress={closeViewer}>
              <Text style={styles.closeText}>Close</Text>
            </Pressable>
            <View style={styles.liveBadge}>
              <View style={styles.liveDot} />
              <Text style={styles.liveBadgeText}>{String(state?.status || active.status || "live").toUpperCase()}</Text>
            </View>
            <Pressable style={styles.closeButton} onPress={() => openLiveWebFallback(activeLiveId, "viewer").catch(() => undefined)}>
              <Text style={styles.closeText}>Web</Text>
            </Pressable>
          </View>

          <View style={styles.player}>
            {posterUrl ? <Image source={{ uri: posterUrl }} style={styles.poster} resizeMode="cover" blurRadius={playbackUrl ? 0 : 2} /> : null}
            {canPlayNative && playbackUrl ? (
              <Video
                source={{ uri: playbackUrl }}
                style={styles.video}
                resizeMode={ResizeMode.COVER}
                shouldPlay
                isMuted={muted}
                usePoster={Boolean(posterUrl)}
                posterSource={posterUrl ? { uri: posterUrl } : undefined}
                onError={() => {
                  setPlaybackFailed(true);
                  setError("Native playback could not start. Use web fallback for this Live.");
                }}
              />
            ) : (
              <View style={styles.unsupported}>
                <Text style={styles.unsupportedTitle}>Playback fallback required</Text>
                <Text style={styles.unsupportedText}>
                  This Live is using {state?.playback?.preferred_transport || "a transport"} that is not verified for native playback yet.
                </Text>
                <Pressable style={styles.primaryButton} onPress={() => openLiveWebFallback(activeLiveId, "viewer").catch(() => undefined)}>
                  <Text style={styles.primaryButtonText}>Open Live Web Viewer</Text>
                </Pressable>
              </View>
            )}
            <Pressable style={styles.muteButton} onPress={() => setMuted((value) => !value)}>
              <Text style={styles.muteText}>{muted ? "Muted" : "Sound on"}</Text>
            </Pressable>
          </View>

          <View style={styles.viewerInfo}>
            <Text style={styles.viewerTitle} numberOfLines={2}>{active.title || "PulseSoc Live"}</Text>
            <Pressable onPress={() => navigateToHostProfile(active)}>
              <Text style={styles.viewerMeta} numberOfLines={1}>{active.creator_name || active.author?.display_name || "PulseSoc Creator"} · {active.category || "Live"}</Text>
            </Pressable>
            <Text style={styles.viewerMeta}>{Number(state?.viewer_count || active.viewer_count || 0)} watching · {state?.playback?.preferred_transport || "state"} · {joined ? "joined" : "local leave available"}</Text>
            {offline ? <Text style={styles.offline}>Showing cached Live state</Text> : null}
            {error ? <Text style={styles.error}>{error}</Text> : null}
          </View>

          <View style={styles.actionRow}>
            <Pressable style={styles.actionButton} disabled={busy === "join"} onPress={() => activeLiveId ? handleJoin(activeLiveId) : undefined}>
              <Text style={styles.actionText}>{joined ? "Refresh Join" : "Join"}</Text>
            </Pressable>
            <Pressable style={styles.actionButton} onPress={() => setJoined(false)}>
              <Text style={styles.actionText}>Leave</Text>
            </Pressable>
            <Pressable style={styles.actionButton} disabled={busy.startsWith("react")} onPress={() => handleReact("🔥")}>
              <Text style={styles.actionText}>Fire</Text>
            </Pressable>
            <Pressable style={styles.actionButton} onPress={() => Share.share({ message: liveWebUrl(activeLiveId) }).catch(() => undefined)}>
              <Text style={styles.actionText}>Share</Text>
            </Pressable>
          </View>

          <FlatList
            data={messages}
            keyExtractor={(item) => String(item.id)}
            style={styles.chatList}
            contentContainerStyle={styles.chatContent}
            renderItem={({ item }) => <ChatLine message={item} />}
            ListEmptyComponent={<Text style={styles.emptyChat}>Live chat messages appear here.</Text>}
          />

          <View style={styles.chatComposer}>
            <TextInput
              style={styles.chatInput}
              value={chatBody}
              onChangeText={setChatBody}
              placeholder="Send a Live chat message"
              placeholderTextColor={colors.muted}
              multiline
            />
            <Pressable style={[styles.sendButton, busy === "chat" ? styles.disabledButton : undefined]} disabled={busy === "chat"} onPress={handleSendChat}>
              <Text style={styles.sendText}>Send</Text>
            </Pressable>
          </View>
        </View>
      </KeyboardAvoidingView>
    );
  }

  return (
    <FlatList
      style={styles.root}
      data={items}
      keyExtractor={(item) => String(item.id)}
      refreshControl={<RefreshControl refreshing={refreshing} tintColor={colors.accent} onRefresh={() => load("refresh").catch(() => undefined)} />}
      contentContainerStyle={styles.content}
      ListHeaderComponent={
        <View style={styles.header}>
          <Text style={styles.title}>Live</Text>
          <Text style={styles.subtitle}>{offline ? "Showing saved Live discovery" : "Discover active PulseSoc broadcasts"}</Text>
          <View style={styles.headerActions}>
            <Pressable style={styles.primaryButton} onPress={() => load("refresh").catch(() => undefined)}>
              <Text style={styles.primaryButtonText}>Refresh</Text>
            </Pressable>
            <Pressable style={styles.secondaryButton} onPress={() => openLiveWebFallback(undefined, "studio").catch(() => undefined)}>
              <Text style={styles.secondaryButtonText}>Go Live Web</Text>
            </Pressable>
          </View>
          {error ? <Text style={styles.error}>{error}</Text> : null}
          <Text style={styles.sectionTitle}>Live now</Text>
        </View>
      }
      renderItem={({ item }) => <LiveCard item={item} onOpen={openLive} onHostPress={() => navigateToHostProfile(item)} />}
      ListEmptyComponent={
        <View style={styles.empty}>
          <Text style={styles.emptyTitle}>No one is live right now</Text>
          <Text style={styles.emptyText}>Native discovery uses the existing PulseSoc Live backend. Start hosting still opens the current web Studio.</Text>
        </View>
      }
      ListFooterComponent={
        <View style={styles.footer}>
          <Text style={styles.sectionTitle}>Scheduled</Text>
          {scheduled.length ? scheduled.map((item) => <LiveCard key={`scheduled-${item.id}`} item={item} onOpen={openLive} onHostPress={() => navigateToHostProfile(item)} />) : (
            <Text style={styles.footerNote}>Scheduled Live/events will appear here when the existing API returns them to native.</Text>
          )}
        </View>
      }
    />
  );
}

function LiveCard({ item, onOpen, onHostPress }: { item: PulseLiveItem; onOpen: (item: PulseLiveItem) => void; onHostPress: () => void }) {
  return (
    <Pressable style={styles.card} onPress={() => onOpen(item)}>
      <View style={styles.thumb}>
        {item.thumbnail_url || item.preview_url ? <Image source={{ uri: item.thumbnail_url || item.preview_url }} style={styles.thumbImage} /> : <View style={styles.thumbFallback} />}
        <View style={styles.liveChip}>
          <View style={styles.liveDot} />
          <Text style={styles.liveChipText}>{String(item.status || "live").toUpperCase()}</Text>
        </View>
      </View>
      <View style={styles.cardBody}>
        <Text style={styles.cardTitle} numberOfLines={2}>{item.title || "PulseSoc Live"}</Text>
        <Pressable onPress={onHostPress}>
          <Text style={styles.cardMeta} numberOfLines={1}>{item.creator_name || "PulseSoc Creator"} · {item.category || "Live"}</Text>
        </Pressable>
        <Text style={styles.cardMeta}>{Number(item.viewer_count || 0)} watching · {item.playback?.preferred_transport || "state"} · {formatShortTime(item.started_at || item.scheduled_at || "")}</Text>
      </View>
    </Pressable>
  );
}

function ChatLine({ message }: { message: PulseLiveChatMessage }) {
  const blocked = message.moderation_status === "blocked";
  return (
    <View style={[styles.chatLine, blocked ? styles.blockedChatLine : undefined]}>
      <Text style={styles.chatName}>{message.display_name || "Viewer"}</Text>
      <Text style={styles.chatText}>{blocked ? "Message blocked by moderation." : message.body}</Text>
    </View>
  );
}

function mergeMessages(messages: Array<PulseLiveChatMessage | undefined>) {
  const seen = new Set<number>();
  return messages.filter((message): message is PulseLiveChatMessage => {
    if (!message || seen.has(message.id)) return false;
    seen.add(message.id);
    return true;
  });
}

const styles = StyleSheet.create({
  actionButton: {
    alignItems: "center",
    backgroundColor: colors.surfaceRaised,
    borderColor: colors.border,
    borderRadius: 8,
    borderWidth: 1,
    flex: 1,
    minHeight: 42,
    justifyContent: "center"
  },
  actionRow: {
    flexDirection: "row",
    gap: 8,
    paddingHorizontal: 14,
    paddingVertical: 10
  },
  actionText: {
    color: colors.text,
    fontWeight: "800"
  },
  blockedChatLine: {
    opacity: 0.62
  },
  card: {
    backgroundColor: colors.surface,
    borderColor: colors.border,
    borderRadius: 8,
    borderWidth: 1,
    flexDirection: "row",
    gap: 12,
    marginBottom: 12,
    padding: 10
  },
  cardBody: {
    flex: 1,
    justifyContent: "center"
  },
  cardMeta: {
    color: colors.muted,
    fontSize: 12,
    marginTop: 4
  },
  cardTitle: {
    color: colors.text,
    fontSize: 17,
    fontWeight: "900"
  },
  center: {
    alignItems: "center",
    backgroundColor: colors.background,
    flex: 1,
    justifyContent: "center"
  },
  centerText: {
    color: colors.muted,
    marginTop: 12
  },
  chatComposer: {
    borderTopColor: colors.border,
    borderTopWidth: 1,
    flexDirection: "row",
    gap: 8,
    padding: 10
  },
  chatContent: {
    gap: 8,
    padding: 12
  },
  chatInput: {
    backgroundColor: colors.surface,
    borderColor: colors.border,
    borderRadius: 8,
    borderWidth: 1,
    color: colors.text,
    flex: 1,
    maxHeight: 92,
    minHeight: 44,
    padding: 10
  },
  chatLine: {
    backgroundColor: colors.surface,
    borderRadius: 8,
    padding: 9
  },
  chatList: {
    flex: 1
  },
  chatName: {
    color: colors.accent,
    fontSize: 12,
    fontWeight: "900"
  },
  chatText: {
    color: colors.text,
    marginTop: 2
  },
  closeButton: {
    minWidth: 54,
    padding: 8
  },
  closeText: {
    color: colors.text,
    fontWeight: "800"
  },
  content: {
    padding: 16,
    paddingBottom: 36
  },
  disabledButton: {
    opacity: 0.62
  },
  empty: {
    alignItems: "center",
    padding: 28
  },
  emptyChat: {
    color: colors.muted,
    textAlign: "center"
  },
  emptyText: {
    color: colors.muted,
    lineHeight: 20,
    marginTop: 8,
    textAlign: "center"
  },
  emptyTitle: {
    color: colors.text,
    fontSize: 18,
    fontWeight: "900"
  },
  error: {
    color: colors.danger,
    marginTop: 10
  },
  footer: {
    paddingTop: 8
  },
  footerNote: {
    color: colors.muted,
    lineHeight: 20,
    marginTop: 8
  },
  header: {
    gap: 8,
    marginBottom: 14
  },
  headerActions: {
    flexDirection: "row",
    gap: 10,
    marginTop: 8
  },
  liveBadge: {
    alignItems: "center",
    flexDirection: "row",
    gap: 7
  },
  liveBadgeText: {
    color: colors.text,
    fontSize: 12,
    fontWeight: "900"
  },
  liveChip: {
    alignItems: "center",
    backgroundColor: "rgba(0,0,0,0.55)",
    borderRadius: 999,
    flexDirection: "row",
    gap: 6,
    left: 8,
    paddingHorizontal: 8,
    paddingVertical: 4,
    position: "absolute",
    top: 8
  },
  liveChipText: {
    color: colors.text,
    fontSize: 10,
    fontWeight: "900"
  },
  liveDot: {
    backgroundColor: colors.danger,
    borderRadius: 999,
    height: 8,
    width: 8
  },
  muteButton: {
    backgroundColor: "rgba(0,0,0,0.56)",
    borderRadius: 999,
    bottom: 12,
    paddingHorizontal: 12,
    paddingVertical: 8,
    position: "absolute",
    right: 12
  },
  muteText: {
    color: colors.text,
    fontWeight: "800"
  },
  offline: {
    color: colors.warning,
    marginTop: 6
  },
  player: {
    aspectRatio: 16 / 9,
    backgroundColor: "#02050b",
    overflow: "hidden",
    position: "relative"
  },
  poster: {
    ...StyleSheet.absoluteFillObject,
    height: "100%",
    width: "100%"
  },
  primaryButton: {
    alignItems: "center",
    backgroundColor: colors.accent,
    borderRadius: 8,
    minHeight: 44,
    justifyContent: "center",
    paddingHorizontal: 14
  },
  primaryButtonText: {
    color: "#04110d",
    fontWeight: "900"
  },
  root: {
    backgroundColor: colors.background,
    flex: 1
  },
  secondaryButton: {
    alignItems: "center",
    borderColor: colors.border,
    borderRadius: 8,
    borderWidth: 1,
    flex: 1,
    minHeight: 44,
    justifyContent: "center",
    paddingHorizontal: 14
  },
  secondaryButtonText: {
    color: colors.text,
    fontWeight: "900"
  },
  sectionTitle: {
    color: colors.text,
    fontSize: 18,
    fontWeight: "900",
    marginTop: 14
  },
  sendButton: {
    alignItems: "center",
    backgroundColor: colors.accent,
    borderRadius: 8,
    minHeight: 44,
    justifyContent: "center",
    paddingHorizontal: 14
  },
  sendText: {
    color: "#04110d",
    fontWeight: "900"
  },
  subtitle: {
    color: colors.muted,
    lineHeight: 21
  },
  thumb: {
    backgroundColor: colors.surfaceRaised,
    borderRadius: 8,
    height: 92,
    overflow: "hidden",
    width: 116
  },
  thumbFallback: {
    backgroundColor: colors.surfaceRaised,
    flex: 1
  },
  thumbImage: {
    height: "100%",
    width: "100%"
  },
  title: {
    color: colors.text,
    fontSize: 30,
    fontWeight: "900"
  },
  unsupported: {
    alignItems: "center",
    flex: 1,
    gap: 10,
    justifyContent: "center",
    padding: 20
  },
  unsupportedText: {
    color: colors.muted,
    lineHeight: 20,
    textAlign: "center"
  },
  unsupportedTitle: {
    color: colors.text,
    fontSize: 18,
    fontWeight: "900"
  },
  video: {
    ...StyleSheet.absoluteFillObject
  },
  viewer: {
    flex: 1
  },
  viewerInfo: {
    borderBottomColor: colors.border,
    borderBottomWidth: 1,
    padding: 14
  },
  viewerMeta: {
    color: colors.muted,
    marginTop: 5
  },
  viewerTitle: {
    color: colors.text,
    fontSize: 22,
    fontWeight: "900"
  },
  viewerTopBar: {
    alignItems: "center",
    borderBottomColor: colors.border,
    borderBottomWidth: 1,
    flexDirection: "row",
    justifyContent: "space-between",
    padding: 10
  }
});
