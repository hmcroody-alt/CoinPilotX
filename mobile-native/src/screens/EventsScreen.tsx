import { NativeStackScreenProps } from "@react-navigation/native-stack";
import { useEffect, useMemo, useState } from "react";
import {
  ActivityIndicator,
  FlatList,
  Image,
  Pressable,
  RefreshControl,
  ScrollView,
  StyleSheet,
  Text,
  View
} from "react-native";
import {
  listScheduledLiveEvents,
  loadCachedScheduledLiveEvents,
  PulseScheduledEvent
} from "../api/events";
import { profileNavigationParams, profileTargetFromAuthor } from "../api/profileTarget";
import { RootStackParamList } from "../navigation/types";
import { colors } from "../theme/colors";
import { formatShortTime } from "../utils/format";
import { sharePulseObject } from "../sharing/nativeShare";

type Props = Partial<NativeStackScreenProps<RootStackParamList, "Events">>;

export function EventsScreen({ route, navigation }: Props) {
  const routeName = String(route?.name || "Events");
  const routeMode = routeName === "LiveScheduleGateway"
    ? "schedule"
    : routeName === "LiveEventCreateGateway"
      ? "create"
      : "events";
  const eventId = Number(route?.params?.eventId || 0);
  const [items, setItems] = useState<PulseScheduledEvent[]>([]);
  const [selected, setSelected] = useState<PulseScheduledEvent | null>(null);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [offline, setOffline] = useState(false);
  const [error, setError] = useState("");

  const heroCopy = useMemo(() => {
    if (routeMode === "schedule") {
      return {
        title: "Schedule Live",
        subtitle: "Native scheduling gateway. Stream setup, eligibility, and review stay on the existing PulseSoc Live backend."
      };
    }
    if (routeMode === "create") {
      return {
        title: "Create Live Event",
        subtitle: "Create flow stays connected to the current Live Studio so event permissions and stream setup remain server-controlled."
      };
    }
    return {
      title: "Events",
      subtitle: offline ? "Showing saved scheduled Live events" : "Scheduled broadcasts, creator events, and Live gateways from the existing PulseSoc platform."
    };
  }, [offline, routeMode]);

  async function load(mode: "initial" | "refresh" = "initial") {
    setError("");
    setOffline(false);
    if (mode === "initial") setLoading(true);
    if (mode === "refresh") setRefreshing(true);
    try {
      const state = await listScheduledLiveEvents({ limit: 32 });
      setItems(state.items || []);
      if (eventId) {
        const focused = (state.items || []).find((item) => item.id === eventId || item.live_id === eventId);
        setSelected(focused || emptyEvent(eventId));
      }
    } catch (loadError) {
      const cached = await loadCachedScheduledLiveEvents();
      if (cached.items?.length) {
        setItems(cached.items);
        if (eventId) {
          const focused = cached.items.find((item) => item.id === eventId || item.live_id === eventId);
          setSelected(focused || emptyEvent(eventId));
        }
        setOffline(true);
      } else {
        setError(loadError instanceof Error ? loadError.message : "Events could not load.");
        if (eventId) setSelected(emptyEvent(eventId));
      }
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }

  function openEvent(item: PulseScheduledEvent) {
    setSelected(item);
    navigation?.navigate("EventDetail", { eventId: item.id, title: item.title || "Event" });
  }

  function watchEvent(item: PulseScheduledEvent | null) {
    const liveId = Number(item?.live_id || item?.id || eventId || 0);
    if (liveId) navigation?.navigate("LiveDetail", { liveId, title: item?.title || "Live" });
  }

  function hostProfile(item: PulseScheduledEvent | null) {
    const target = profileTargetFromAuthor((item?.author || item?.creator) as Record<string, unknown> | undefined, item as unknown as Record<string, unknown>);
    const params = profileNavigationParams(target, item?.creator_name || "Profile");
    if (params) navigation?.navigate("ProfileDetail", params);
  }

  function shareEvent(item: PulseScheduledEvent | null) {
    const path = item?.event_url || "/pulse/events";
    const url = /^https?:\/\//i.test(path) ? path : `https://pulsesoc.com${path.startsWith("/") ? path : `/${path}`}`;
    sharePulseObject({
      kind: "event",
      url,
      title: item?.title || "PulseSoc Event",
      description: item?.category,
      author: item?.creator_name,
      previewImageUrl: item?.thumbnail_url
    }).catch(() => undefined);
  }

  useEffect(() => {
    load("initial").catch(() => undefined);
  }, [eventId, routeMode]);

  if (loading && !items.length && !selected) {
    return (
      <View style={styles.center}>
        <ActivityIndicator color={colors.accent} />
        <Text style={styles.centerText}>Loading Events</Text>
      </View>
    );
  }

  if (selected) {
    return (
      <ScrollView
        style={styles.root}
        contentContainerStyle={styles.detailContent}
        refreshControl={<RefreshControl refreshing={refreshing} tintColor={colors.accent} onRefresh={() => load("refresh").catch(() => undefined)} />}
      >
        <View style={styles.detailShell}>
          <Pressable accessibilityRole="button" style={styles.backButton} onPress={() => setSelected(null)}>
            <Text style={styles.backText}>Back to events</Text>
          </Pressable>
          <View style={styles.detailHero}>
            {selected.thumbnail_url || selected.preview_url ? <Image source={{ uri: selected.thumbnail_url || selected.preview_url }} style={styles.detailImage} /> : <View style={styles.detailImageFallback} />}
            <View style={styles.detailOverlay}>
              <Text style={styles.kicker}>Scheduled Live Gateway</Text>
              <Text style={styles.detailTitle}>{selected.title || "PulseSoc Event"}</Text>
              <Pressable accessibilityRole="button" onPress={() => hostProfile(selected)}>
                <Text style={styles.detailMeta}>{selected.creator_name || "PulseSoc Creator"} · {selected.category || "Live"}</Text>
              </Pressable>
              <Text style={styles.detailMeta}>{formatShortTime(selected.scheduled_at || selected.started_at || "") || "Schedule pending"} · {Number(selected.viewer_count || 0)} interested</Text>
            </View>
          </View>
          {offline ? <Text style={styles.offline}>Showing cached event details</Text> : null}
          {error ? <Text style={styles.error}>{error}</Text> : null}
          <View style={styles.actionGrid}>
            <Pressable accessibilityRole="button" style={styles.primaryButton} onPress={() => watchEvent(selected)}>
              <Text style={styles.primaryButtonText}>Join or Watch</Text>
            </Pressable>
            <Pressable accessibilityRole="button" style={styles.secondaryButton} onPress={() => shareEvent(selected)}>
              <Text style={styles.secondaryButtonText}>Share</Text>
            </Pressable>
            <Pressable accessibilityRole="button" style={styles.secondaryButton} onPress={() => navigation?.navigate("LiveStudio", { title: "Live Studio" })}>
              <Text style={styles.secondaryButtonText}>Go Live</Text>
            </Pressable>
          </View>
          <View style={styles.notice}>
            <Text style={styles.noticeTitle}>Backend authority preserved</Text>
            <Text style={styles.noticeText}>Reminders, ticketing, event checkout, hosting, co-hosting, and studio setup stay on the existing PulseSoc backend until dedicated native contracts exist.</Text>
          </View>
        </View>
      </ScrollView>
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
          <Text style={styles.title}>{heroCopy.title}</Text>
          <Text style={styles.subtitle}>{heroCopy.subtitle}</Text>
          <View style={styles.gatewayPanel}>
            <View>
              <Text style={styles.gatewayTitle}>Scheduled Live gateway</Text>
              <Text style={styles.gatewayText}>Native discovery uses existing Live scheduled data. Creation and payments stay inside provider-owned boundaries.</Text>
            </View>
            <View style={styles.headerActions}>
              <Pressable accessibilityRole="button" style={styles.primaryButton} onPress={() => load("refresh").catch(() => undefined)}>
                <Text style={styles.primaryButtonText}>Refresh</Text>
              </Pressable>
            </View>
          </View>
          {error ? <Text style={styles.error}>{error}</Text> : null}
          {offline ? <Text style={styles.offline}>Showing cached scheduled events</Text> : null}
          <Text style={styles.sectionTitle}>Scheduled Live</Text>
        </View>
      }
      renderItem={({ item }) => <EventCard item={item} onOpen={() => openEvent(item)} onWatch={() => watchEvent(item)} onHostPress={() => hostProfile(item)} />}
      ListEmptyComponent={
        <View style={styles.empty}>
          <Text style={styles.emptyTitle}>No scheduled events yet</Text>
          <Text style={styles.emptyText}>PulseSoc will show scheduled Live events here when `/api/pulse/live-now` returns scheduled data.</Text>
        </View>
      }
      ListFooterComponent={
        <View style={styles.footer}>
          <Text style={styles.sectionTitle}>Go live</Text>
          <GatewayRow title="Create Live Event" body="Uses the current Live Studio and backend eligibility rules." onPress={() => navigation?.navigate("LiveStudio", { title: "Live Studio" })} />
          <GatewayRow title="Live Studio" body="Set up your device, camera, and network, then broadcast natively in-app." onPress={() => navigation?.navigate("LiveStudio", { title: "Live Studio" })} />
        </View>
      }
    />
  );
}

function EventCard({ item, onOpen, onWatch, onHostPress }: { item: PulseScheduledEvent; onOpen: () => void; onWatch: () => void; onHostPress: () => void }) {
  return (
    <Pressable accessibilityRole="button" style={styles.card} onPress={onOpen}>
      {item.thumbnail_url || item.preview_url ? <Image source={{ uri: item.thumbnail_url || item.preview_url }} style={styles.cardImage} /> : <View style={styles.cardImageFallback} />}
      <View style={styles.cardBody}>
        <Text style={styles.kicker}>Scheduled</Text>
        <Text style={styles.cardTitle} numberOfLines={2}>{item.title || "PulseSoc Event"}</Text>
        <Pressable accessibilityRole="button" onPress={onHostPress}>
          <Text style={styles.cardMeta} numberOfLines={1}>{item.creator_name || "PulseSoc Creator"} · {item.category || "Live"}</Text>
        </Pressable>
        <Text style={styles.cardMeta}>{formatShortTime(item.scheduled_at || item.started_at || "") || "Time pending"} · {Number(item.viewer_count || 0)} interested</Text>
        <View style={styles.cardActions}>
          <Pressable accessibilityRole="button" style={styles.smallButton} onPress={onWatch}>
            <Text style={styles.smallButtonText}>Watch</Text>
          </Pressable>
          <Pressable accessibilityRole="button" style={styles.smallGhostButton} onPress={onOpen}>
            <Text style={styles.smallGhostText}>Details</Text>
          </Pressable>
        </View>
      </View>
    </Pressable>
  );
}

function GatewayRow({ title, body, onPress }: { title: string; body: string; onPress: () => void }) {
  return (
    <Pressable accessibilityRole="button" style={styles.gatewayRow} onPress={onPress}>
      <View style={styles.gatewayPulse} />
      <View style={styles.gatewayBody}>
        <Text style={styles.gatewayRowTitle}>{title}</Text>
        <Text style={styles.gatewayRowText}>{body}</Text>
      </View>
      <Text style={styles.gatewayArrow}>Open</Text>
    </Pressable>
  );
}

function emptyEvent(eventId: number): PulseScheduledEvent {
  return {
    id: eventId,
    live_id: eventId,
    event_kind: "scheduled_live",
    event_url: `/pulse/events/${eventId}`,
    watch_url: `/pulse/live/${eventId}`,
    schedule_url: "/pulse/live/schedule",
    create_url: "/pulse/live/events/create",
    title: "PulseSoc Event",
    creator_name: "PulseSoc Creator",
    category: "Live"
  };
}

const styles = StyleSheet.create({
  actionGrid: {
    flexDirection: "row",
    flexWrap: "wrap",
    gap: 10
  },
  backButton: {
    alignSelf: "flex-start",
    borderColor: colors.border,
    borderRadius: 999,
    borderWidth: 1,
    paddingHorizontal: 12,
    paddingVertical: 8
  },
  backText: {
    color: colors.text,
    fontWeight: "800"
  },
  card: {
    backgroundColor: colors.surface,
    borderColor: colors.border,
    borderRadius: 8,
    borderWidth: 1,
    flexDirection: "row",
    gap: 12,
    marginBottom: 12,
    overflow: "hidden",
    padding: 10
  },
  cardActions: {
    flexDirection: "row",
    gap: 8,
    marginTop: 10
  },
  cardBody: {
    flex: 1,
    justifyContent: "center"
  },
  cardImage: {
    backgroundColor: colors.surfaceRaised,
    borderRadius: 8,
    height: 108,
    width: 118
  },
  cardImageFallback: {
    backgroundColor: "rgba(37,208,167,0.12)",
    borderColor: "rgba(37,208,167,0.28)",
    borderRadius: 8,
    borderWidth: 1,
    height: 108,
    width: 118
  },
  cardMeta: {
    color: colors.muted,
    fontSize: 12,
    marginTop: 4
  },
  cardTitle: {
    color: colors.text,
    fontSize: 17,
    fontWeight: "900",
    lineHeight: 22
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
  content: {
    padding: 16,
    paddingBottom: 36
  },
  detailContent: {
    padding: 16,
    paddingBottom: 36
  },
  detailHero: {
    borderColor: "rgba(79,140,255,0.26)",
    borderRadius: 8,
    borderWidth: 1,
    minHeight: 320,
    overflow: "hidden"
  },
  detailImage: {
    height: 320,
    width: "100%"
  },
  detailImageFallback: {
    backgroundColor: "rgba(37,208,167,0.12)",
    height: 320,
    width: "100%"
  },
  detailMeta: {
    color: colors.muted,
    fontWeight: "700",
    marginTop: 6
  },
  detailOverlay: {
    backgroundColor: "rgba(16,18,20,0.86)",
    bottom: 0,
    gap: 2,
    left: 0,
    padding: 16,
    position: "absolute",
    right: 0
  },
  detailShell: {
    gap: 14
  },
  detailTitle: {
    color: colors.text,
    fontSize: 28,
    fontWeight: "900",
    lineHeight: 34
  },
  empty: {
    alignItems: "center",
    gap: 10,
    padding: 28
  },
  emptyText: {
    color: colors.muted,
    lineHeight: 20,
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
    gap: 10,
    paddingTop: 10
  },
  gatewayArrow: {
    color: colors.accent,
    fontWeight: "900"
  },
  gatewayBody: {
    flex: 1
  },
  gatewayPanel: {
    backgroundColor: colors.surface,
    borderColor: "rgba(37,208,167,0.24)",
    borderRadius: 8,
    borderWidth: 1,
    gap: 12,
    marginTop: 8,
    padding: 14
  },
  gatewayPulse: {
    backgroundColor: colors.accent,
    borderRadius: 999,
    height: 10,
    marginTop: 5,
    shadowColor: colors.accent,
    shadowOpacity: 0.5,
    shadowRadius: 12,
    width: 10
  },
  gatewayRow: {
    alignItems: "center",
    backgroundColor: colors.surface,
    borderColor: colors.border,
    borderRadius: 8,
    borderWidth: 1,
    flexDirection: "row",
    gap: 12,
    padding: 12
  },
  gatewayRowText: {
    color: colors.muted,
    lineHeight: 19,
    marginTop: 3
  },
  gatewayRowTitle: {
    color: colors.text,
    fontWeight: "900"
  },
  gatewayText: {
    color: colors.muted,
    lineHeight: 20,
    marginTop: 4
  },
  gatewayTitle: {
    color: colors.text,
    fontSize: 17,
    fontWeight: "900"
  },
  header: {
    gap: 8,
    marginBottom: 14
  },
  headerActions: {
    flexDirection: "row",
    flexWrap: "wrap",
    gap: 10
  },
  kicker: {
    color: colors.accent,
    fontSize: 11,
    fontWeight: "900",
    letterSpacing: 0.8,
    textTransform: "uppercase"
  },
  notice: {
    backgroundColor: "rgba(243,185,78,0.08)",
    borderColor: "rgba(243,185,78,0.22)",
    borderRadius: 8,
    borderWidth: 1,
    padding: 12
  },
  noticeText: {
    color: colors.muted,
    lineHeight: 20,
    marginTop: 4
  },
  noticeTitle: {
    color: colors.warning,
    fontWeight: "900"
  },
  offline: {
    color: colors.warning,
    marginTop: 8
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
    color: "#08110f",
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
    marginTop: 8
  },
  smallButton: {
    backgroundColor: colors.accent,
    borderRadius: 8,
    paddingHorizontal: 12,
    paddingVertical: 8
  },
  smallButtonText: {
    color: "#08110f",
    fontWeight: "900"
  },
  smallGhostButton: {
    borderColor: colors.border,
    borderRadius: 8,
    borderWidth: 1,
    paddingHorizontal: 12,
    paddingVertical: 8
  },
  smallGhostText: {
    color: colors.text,
    fontWeight: "900"
  },
  subtitle: {
    color: colors.muted,
    fontSize: 15,
    lineHeight: 21
  },
  title: {
    color: colors.text,
    fontSize: 30,
    fontWeight: "900"
  }
});
