import { NativeStackScreenProps } from "@react-navigation/native-stack";
import { useEffect, useMemo, useRef, useState } from "react";
import {
  ActivityIndicator,
  FlatList,
  Image,
  Pressable,
  RefreshControl,
  ScrollView,
  StyleSheet,
  Text,
  TextInput,
  View
} from "react-native";
import {
  defaultTrendingSearches,
  loadCachedPulseSearch,
  loadRecentSearches,
  PulseSearchGroupKey,
  PulseSearchResult,
  PulseSearchResults,
  saveRecentSearch,
  searchPulse,
  SEARCH_GROUPS
} from "../api/search";
import { profileNavigationParams, resolveProfileTarget } from "../api/profileTarget";
import { useBottomNavSurface } from "../navigation/BottomNavVisibility";
import { routeNotificationTarget } from "../navigation/notificationRouting";
import { RootStackParamList } from "../navigation/types";
import { SavableContentType, SaveTarget } from "../social/saveContract";
import { useSavedState } from "../social/savedStore";
import { setSaved } from "../social/useSaveAction";
import { colors } from "../theme/colors";
import { createThemedStyles } from "../theme/themedStyles";

/**
 * Which search result types can be saved, keyed on the `type` the search API
 * stamps on each row.
 *
 * Deliberately not every type the results contain. `video` rows come from
 * `pulse_videos`, a different table with no save route, and a Save button that
 * posts a video id to the post route would 404 — a broken button is worse than
 * an absent one. Creators, groups, rooms, music and comments are excluded for
 * the same reason.
 */
const SEARCH_SAVE_TYPES: Record<string, SavableContentType> = {
  post: "post",
  reel: "reel",
  status: "status",
  marketplace: "marketplace"
};

function searchSaveTarget(item: PulseSearchResult): SaveTarget | null {
  const contentType = SEARCH_SAVE_TYPES[String(item.type || "").toLowerCase()];
  if (!contentType) return null;
  const id = Number(item.id || 0);
  if (!id) return null;
  return {
    type: contentType,
    id,
    title: item.title || "PulseSoc result",
    previewText: item.description || item.meta || "",
    thumbnailUrl: item.avatar_url || "",
    sourceUrl: item.url || ""
  };
}

type Props = Partial<NativeStackScreenProps<RootStackParamList, "Search">>;

type DiscoveryTab = "all" | "people" | "posts" | "reels" | "status" | "marketplace" | "communities" | "events" | "learning" | "trending" | "hashtags";

const DISCOVERY_TABS: Array<{ key: DiscoveryTab; label: string; groups: PulseSearchGroupKey[] }> = [
  { key: "all", label: "All", groups: SEARCH_GROUPS.map((group) => group.key) },
  { key: "people", label: "People", groups: ["creators"] },
  { key: "posts", label: "Posts", groups: ["posts", "comments"] },
  { key: "reels", label: "Reels", groups: ["reels", "videos"] },
  { key: "status", label: "Status", groups: ["statuses"] },
  { key: "marketplace", label: "Marketplace", groups: ["marketplace"] },
  { key: "communities", label: "Communities", groups: ["groups", "rooms"] },
  { key: "events", label: "Events", groups: [] },
  { key: "learning", label: "Learning", groups: [] },
  { key: "trending", label: "Trending", groups: [] },
  { key: "hashtags", label: "Hashtags", groups: [] }
];

export function SearchScreen({ route, navigation }: Props) {
  // Bottom-dock coupling: drives hide-on-scroll-down / reveal-on-scroll-up and
  // reserves the matching clearance so the last row never sits under the dock.
  const dock = useBottomNavSurface();
  const initialQuery = String(route?.params?.query || "");
  const debounceRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const [query, setQuery] = useState(initialQuery);
  const [activeTab, setActiveTab] = useState<DiscoveryTab>("all");
  const [results, setResults] = useState<PulseSearchResults | null>(null);
  const [recent, setRecent] = useState<string[]>([]);
  const [trending, setTrending] = useState<string[]>(defaultTrendingSearches());
  const [loading, setLoading] = useState(false);
  const [refreshing, setRefreshing] = useState(false);
  const [offline, setOffline] = useState(false);
  const [error, setError] = useState("");
  const [visibleLimit, setVisibleLimit] = useState(24);

  useEffect(() => {
    loadRecentSearches().then(setRecent).catch(() => undefined);
  }, []);

  useEffect(() => {
    const clean = query.trim();
    if (debounceRef.current) clearTimeout(debounceRef.current);
    if (!clean) {
      setResults(null);
      setError("");
      setOffline(false);
      return;
    }
    debounceRef.current = setTimeout(() => {
      runSearch(clean, "search").catch(() => undefined);
    }, 320);
    return () => {
      if (debounceRef.current) clearTimeout(debounceRef.current);
    };
  }, [query]);

  const visibleResults = useMemo(() => flattenResults(results, activeTab).slice(0, visibleLimit), [activeTab, results, visibleLimit]);
  const totalResults = useMemo(() => flattenResults(results, activeTab).length, [activeTab, results]);

  async function runSearch(nextQuery = query, mode: "search" | "refresh" = "search") {
    const clean = nextQuery.trim();
    if (!clean) return;
    setError("");
    setOffline(false);
    setVisibleLimit(24);
    if (mode === "refresh") setRefreshing(true);
    else setLoading(true);
    try {
      const data = await searchPulse({ query: clean, limit: 20 });
      setResults(data.results || null);
      setTrending(data.trending || defaultTrendingSearches());
      setRecent(await saveRecentSearch(clean));
    } catch (searchError) {
      const cached = await loadCachedPulseSearch(clean);
      if (cached?.results) {
        setResults(cached.results as PulseSearchResults);
        setTrending(cached.trending || defaultTrendingSearches());
        setOffline(true);
      } else {
        setError(searchError instanceof Error ? searchError.message : "PulseSoc search could not load.");
      }
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }

  function applyChip(value: string) {
    setQuery(value);
    runSearch(value, "search").catch(() => undefined);
  }

  async function openResult(item: PulseSearchResult) {
    // A presence is an entity, not a person: route it to its own surface so it
    // is never resolved against the personal-profile navigator.
    if (item.type === "presence" && navigation) {
      navigation.navigate("Page", {
        pageId: typeof item.id === "number" ? item.id : Number(item.id) || undefined,
        handle: item.handle,
        title: item.title
      });
      return;
    }
    const profileTarget = isProfileResult(item) ? resolveProfileTarget({ ...item, source: "search" }) : null;
    const params = profileNavigationParams(profileTarget, item.title || "Profile");
    if (params && navigation) {
      navigation.navigate("ProfileDetail", params);
      return;
    }
    await routeNotificationTarget(item.url || "/pulse/search").catch(() => undefined);
  }

  const hasQuery = Boolean(query.trim());
  const unsupportedTab = Boolean(hasQuery && !DISCOVERY_TABS.find((tab) => tab.key === activeTab)?.groups.length);

  return (
    <View style={styles.root}>
      <View style={styles.header}>
        <Text style={styles.title}>Search</Text>
        <Text style={styles.subtitle}>
          {offline ? "Showing saved results" : "Find PulseSoc people, posts, reels, status, listings, rooms, and signals."}
        </Text>
        <TextInput
          style={styles.searchInput}
          value={query}
          onChangeText={setQuery}
          placeholder="Search PulseSoc"
          placeholderTextColor={colors.muted}
          returnKeyType="search"
          autoCorrect={false}
          onSubmitEditing={() => runSearch(query, "search").catch(() => undefined)}
        />
        <ScrollView horizontal showsHorizontalScrollIndicator={false} contentContainerStyle={styles.tabRow}>
          {DISCOVERY_TABS.map((tab) => (
            <Pressable key={tab.key} style={[styles.tab, activeTab === tab.key ? styles.tabActive : undefined]} onPress={() => setActiveTab(tab.key)}>
              <Text style={[styles.tabText, activeTab === tab.key ? styles.tabTextActive : undefined]}>{tab.label}</Text>
            </Pressable>
          ))}
        </ScrollView>
        {error ? <Text style={styles.error}>{error}</Text> : null}
      </View>

      {!hasQuery ? (
        <ScrollView contentContainerStyle={styles.starter}>
          {activeTab === "events" ? <EventsGatewayShortcut onPress={() => routeNotificationTarget("/pulse/events").catch(() => undefined)} /> : null}
          {activeTab === "learning" ? <LearningGatewayShortcut onPress={() => routeNotificationTarget("/pulse/courses").catch(() => undefined)} /> : null}
          <ChipSection title="Recent searches" emptyText="Your searches will appear here." items={recent} onPress={applyChip} />
          <ChipSection title="Suggested searches" items={trending} onPress={applyChip} />
        </ScrollView>
      ) : (
        <FlatList
          data={visibleResults}
          keyExtractor={(item, index) => `${item.type || "result"}-${item.id || item.url || index}`}
          {...dock.handlers}
          contentContainerStyle={[styles.content, dock.contentPadding]}
          refreshControl={<RefreshControl refreshing={refreshing} tintColor={colors.accent} onRefresh={() => runSearch(query, "refresh").catch(() => undefined)} />}
          ListHeaderComponent={
            loading && !visibleResults.length ? (
              <View style={styles.loadingRow}>
                <ActivityIndicator color={colors.accent} />
                <Text style={styles.loadingText}>Searching PulseSoc</Text>
              </View>
            ) : (
              <Text style={styles.resultCount}>{unsupportedTab ? "Native destination coming soon" : `${totalResults} results`}</Text>
            )
          }
          ListEmptyComponent={
            loading ? null : (
              <View style={styles.empty}>
                <Text style={styles.emptyTitle}>{unsupportedTab ? "Discovery tab is not native yet" : "No PulseSoc results found"}</Text>
                <Text style={styles.emptyText}>
                  {unsupportedTab
                    ? "There is nothing to open here yet."
                    : "Try another creator, topic, video, listing, room, reel, or signal."}
                </Text>
              </View>
            )
          }
          renderItem={({ item }) => <SearchResultCard item={item} onPress={openResult} />}
          onEndReached={() => setVisibleLimit((current) => Math.min(current + 24, totalResults))}
          onEndReachedThreshold={0.4}
        />
      )}
    </View>
  );
}

function EventsGatewayShortcut({ onPress }: { onPress: () => void }) {
  return (
    <Pressable style={styles.eventsGateway} onPress={onPress}>
      <Text style={styles.eventsGatewayKicker}>Native gateway</Text>
      <Text style={styles.eventsGatewayTitle}>Events and scheduled Live</Text>
      <Text style={styles.eventsGatewayText}>Browse broadcasts people have scheduled. Creating events and selling tickets are handled on pulsesoc.com.</Text>
    </Pressable>
  );
}

function LearningGatewayShortcut({ onPress }: { onPress: () => void }) {
  return (
    <Pressable style={styles.eventsGateway} onPress={onPress}>
      <Text style={styles.eventsGatewayKicker}>Native gateway</Text>
      <Text style={styles.eventsGatewayTitle}>Courses and learning</Text>
      <Text style={styles.eventsGatewayText}>Open native lesson discovery. Course creation, payments, teacher tools, and unsupported lesson media stay inside provider-owned boundaries.</Text>
    </Pressable>
  );
}

function ChipSection({ title, items, emptyText, onPress }: { title: string; items: string[]; emptyText?: string; onPress: (value: string) => void }) {
  return (
    <View style={styles.section}>
      <Text style={styles.sectionTitle}>{title}</Text>
      {items.length ? (
        <View style={styles.chipRow}>
          {items.map((item) => (
            <Pressable key={item} style={styles.chip} onPress={() => onPress(item)}>
              <Text style={styles.chipText}>{item}</Text>
            </Pressable>
          ))}
        </View>
      ) : (
        <Text style={styles.emptyText}>{emptyText || "Suggestions will appear here."}</Text>
      )}
    </View>
  );
}

function SearchResultCard({ item, onPress }: { item: PulseSearchResult; onPress: (item: PulseSearchResult) => void }) {
  const letter = String(item.type || item.title || "P").slice(0, 1).toUpperCase();
  const target = searchSaveTarget(item);
  return (
    <Pressable style={styles.card} onPress={() => onPress(item)}>
      {item.avatar_url ? <Image source={{ uri: item.avatar_url }} style={styles.avatar} /> : <View style={styles.resultMark}><Text style={styles.resultMarkText}>{letter}</Text></View>}
      <View style={styles.cardBody}>
        <Text style={styles.cardTitle} numberOfLines={1}>{item.title || "PulseSoc result"}</Text>
        <Text style={styles.cardDescription} numberOfLines={2}>{item.description || item.meta || "PulseSoc"}</Text>
        <Text style={styles.cardMeta} numberOfLines={1}>{item.meta || item.type || "PulseSoc"}</Text>
      </View>
      {target ? <SearchSaveButton target={target} /> : <Text style={styles.cardType}>{item.type || "PulseSoc"}</Text>}
    </Pressable>
  );
}

/**
 * Search results carry no `saved` flag — the search API selects columns rather
 * than building post payloads — so the store is the only source of truth here.
 * That is enough: a result the user saved from the feed a moment ago already
 * has an entry, and one they save from here seeds it for every other screen.
 */
function SearchSaveButton({ target }: { target: SaveTarget }) {
  const state = useSavedState(target.type, target.id);
  return (
    <Pressable
      testID={`search-save-${target.type}-${target.id}`}
      accessibilityRole="button"
      accessibilityLabel={state.saved ? `Remove ${target.type} from Saved` : `Save ${target.type}`}
      accessibilityState={{ selected: state.saved, busy: state.pending, disabled: state.pending }}
      style={[styles.saveButton, state.saved ? styles.saveButtonActive : undefined]}
      disabled={state.pending}
      onPress={() => { setSaved(target, !state.saved).catch(() => undefined); }}
    >
      <Text style={[styles.saveButtonText, state.saved ? styles.saveButtonTextActive : undefined]}>
        {state.pending ? (state.saved ? "Saving" : "Removing") : state.saved ? "Saved" : "Save"}
      </Text>
    </Pressable>
  );
}

function flattenResults(results: PulseSearchResults | null, activeTab: DiscoveryTab) {
  if (!results) return [];
  const tab = DISCOVERY_TABS.find((item) => item.key === activeTab) || DISCOVERY_TABS[0];
  return tab.groups.flatMap((group) => results[group] || []);
}

function isProfileResult(item: PulseSearchResult) {
  const type = String(item.type || "").toLowerCase();
  const url = String(item.url || "");
  return type === "creator" || type === "person" || type === "people" || type === "user" || /^\/pulse\/(@|profile\/|u\/|id\/)/.test(url);
}

const styles = createThemedStyles(() => ({
  avatar: {
    backgroundColor: colors.surfaceRaised,
    borderRadius: 22,
    height: 44,
    width: 44
  },
  card: {
    alignItems: "center",
    backgroundColor: colors.surface,
    borderColor: colors.border,
    borderRadius: 8,
    borderWidth: 1,
    flexDirection: "row",
    gap: 12,
    marginBottom: 10,
    padding: 12
  },
  cardBody: {
    flex: 1,
    gap: 3
  },
  cardDescription: {
    color: colors.muted,
    fontSize: 13,
    lineHeight: 18
  },
  cardMeta: {
    color: colors.accent,
    fontSize: 11,
    fontWeight: "800"
  },
  cardTitle: {
    color: colors.text,
    fontSize: 15,
    fontWeight: "900"
  },
  cardType: {
    color: colors.muted,
    fontSize: 11,
    fontWeight: "800",
    textTransform: "capitalize"
  },
  chip: {
    backgroundColor: colors.surface,
    borderColor: colors.border,
    borderRadius: 8,
    borderWidth: 1,
    paddingHorizontal: 12,
    paddingVertical: 9
  },
  chipRow: {
    flexDirection: "row",
    flexWrap: "wrap",
    gap: 8
  },
  chipText: {
    color: colors.text,
    fontWeight: "800"
  },
  content: {
    padding: 16,
    paddingBottom: 32
  },
  empty: {
    alignItems: "center",
    padding: 24
  },
  emptyText: {
    color: colors.muted,
    lineHeight: 20,
    textAlign: "center"
  },
  emptyTitle: {
    color: colors.text,
    fontSize: 17,
    fontWeight: "900",
    marginBottom: 6
  },
  eventsGateway: {
    backgroundColor: colors.surface,
    borderColor: "rgba(37,208,167,0.26)",
    borderRadius: 8,
    borderWidth: 1,
    gap: 6,
    marginBottom: 24,
    padding: 14
  },
  eventsGatewayKicker: {
    color: colors.accent,
    fontSize: 11,
    fontWeight: "900",
    letterSpacing: 0.8,
    textTransform: "uppercase"
  },
  eventsGatewayText: {
    color: colors.muted,
    lineHeight: 20
  },
  eventsGatewayTitle: {
    color: colors.text,
    fontSize: 18,
    fontWeight: "900"
  },
  error: {
    color: "#ff9f9f",
    marginTop: 10
  },
  header: {
    borderBottomColor: colors.border,
    borderBottomWidth: 1,
    padding: 16,
    paddingBottom: 12
  },
  loadingRow: {
    alignItems: "center",
    flexDirection: "row",
    gap: 10,
    marginBottom: 14
  },
  loadingText: {
    color: colors.muted
  },
  resultCount: {
    color: colors.muted,
    fontSize: 12,
    fontWeight: "800",
    marginBottom: 12,
    textTransform: "uppercase"
  },
  resultMark: {
    alignItems: "center",
    backgroundColor: colors.surfaceRaised,
    borderColor: colors.border,
    borderRadius: 22,
    borderWidth: 1,
    height: 44,
    justifyContent: "center",
    width: 44
  },
  resultMarkText: {
    color: colors.accent,
    fontWeight: "900"
  },
  root: {
    backgroundColor: "transparent",
    flex: 1
  },
  saveButton: {
    alignItems: "center",
    borderColor: colors.border,
    borderRadius: 8,
    borderWidth: 1,
    justifyContent: "center",
    minHeight: 34,
    minWidth: 68,
    paddingHorizontal: 10,
    paddingVertical: 7
  },
  saveButtonActive: {
    backgroundColor: "rgba(37, 208, 167, 0.14)",
    borderColor: colors.accent
  },
  saveButtonText: {
    color: colors.text,
    fontSize: 12,
    fontWeight: "900"
  },
  saveButtonTextActive: {
    color: colors.accent
  },
  searchInput: {
    backgroundColor: colors.surface,
    borderColor: colors.border,
    borderRadius: 8,
    borderWidth: 1,
    color: colors.text,
    fontSize: 16,
    marginTop: 14,
    minHeight: 46,
    paddingHorizontal: 12
  },
  section: {
    marginBottom: 24
  },
  sectionTitle: {
    color: colors.text,
    fontSize: 16,
    fontWeight: "900",
    marginBottom: 10
  },
  starter: {
    padding: 16,
    paddingBottom: 32
  },
  subtitle: {
    color: colors.muted,
    lineHeight: 20,
    marginTop: 4
  },
  tab: {
    borderColor: colors.border,
    borderRadius: 8,
    borderWidth: 1,
    minHeight: 34,
    paddingHorizontal: 12,
    paddingVertical: 8
  },
  tabActive: {
    backgroundColor: "rgba(37, 208, 167, 0.14)",
    borderColor: colors.accent
  },
  tabRow: {
    gap: 8,
    paddingTop: 12
  },
  tabText: {
    color: colors.muted,
    fontSize: 12,
    fontWeight: "900"
  },
  tabTextActive: {
    color: colors.accent
  },
  title: {
    color: colors.text,
    fontSize: 28,
    fontWeight: "900"
  }
}));
