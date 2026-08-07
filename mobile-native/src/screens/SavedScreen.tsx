import { useCallback, useEffect, useMemo, useRef, useState } from "react";
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
  createSavedCollection,
  deleteSavedCollection,
  listSavedContent,
  loadCachedSavedLibrary,
  moveSavedItem,
  removeSavedItem,
  SavedCollection,
  SavedContentType,
  SavedItem,
  updateSavedCollection
} from "../api/saved";
import { NativeMediaViewer, NativeMediaViewerItem, mediaViewerItemFromPulseMedia } from "../components/NativeMediaViewer";
import { useTranslation } from "../i18n";
import { useBottomNavSurface } from "../navigation/BottomNavVisibility";
import { routeNotificationTarget } from "../navigation/notificationRouting";
import type { RootStackParamList } from "../navigation/types";
import { PRIVATE_CONTENT_MESSAGE, resolveRouteProfileContext } from "../profile/profileContext";
import { useAuth } from "../session/auth";
import { describeSavedActionError, describeSavedLibraryError } from "../social/actionGuard";
import { SavableContentType, saveKey } from "../social/saveContract";
import { observeSavedState, subscribeToSaveChanges } from "../social/savedStore";
import { setSaved } from "../social/useSaveAction";
import { colors } from "../theme/colors";
import { createThemedStyles } from "../theme/themedStyles";

/**
 * The content types this screen can hand back to the save contract.
 *
 * The library stores more types than the contract has routes for — rooms,
 * groups, learning items — and those keep the row-id removal path they have
 * always used. Only the four with a real route go through the store, because
 * only those have cards elsewhere in the app whose state has to agree with
 * this list.
 */
const STORE_BACKED_TYPES: SavableContentType[] = ["post", "reel", "status", "marketplace"];

function storeBackedType(contentType: string): SavableContentType | null {
  const candidate = String(contentType || "").toLowerCase() as SavableContentType;
  return STORE_BACKED_TYPES.includes(candidate) ? candidate : null;
}

/**
 * Every store key that refers to this row.
 *
 * A saved Reel is one row here and two identities in the store: the reel it was
 * saved as, and the post that backs it. The library now reports that `post_id`,
 * so unsaving from the *post* card of the same video removes this row too —
 * before, the row survived, and a refresh brought back a Save state the user had
 * already revoked. Types with no backing post yield a single key.
 */
function itemStoreKeys(item: SavedItem): string[] {
  const contentType = storeBackedType(item.content_type);
  if (!contentType) return [];
  const keys = [saveKey(contentType, item.content_id)];
  const postId = Number(item.post_id || 0);
  if (postId > 0) {
    const postKey = saveKey("post", postId);
    if (!keys.includes(postKey)) keys.push(postKey);
  }
  return keys;
}

/**
 * The viewer payload for a saved row, or an empty list when there is nothing to
 * play. `unavailable` rows return empty by construction: the snapshot title is
 * all that is left of deleted content, and offering to play it would be a lie
 * the player would have to break.
 */
function playableMediaFor(item: SavedItem): NativeMediaViewerItem[] {
  if (item.unavailable) return [];
  return (item.media || [])
    .map((media) => mediaViewerItemFromPulseMedia(media, {
      title: item.title,
      subtitle: item.preview_text || undefined,
      sourceUrl: item.source_url
    }))
    .filter((viewerItem) => Boolean(viewerItem.url));
}

const TYPE_FILTERS: Array<{ key: SavedContentType; label: string }> = [
  { key: "all", label: "All" },
  { key: "post", label: "Posts" },
  { key: "reel", label: "Reels" },
  { key: "status", label: "Status" },
  { key: "marketplace", label: "Marketplace" },
  { key: "video", label: "Videos" },
  { key: "room", label: "Rooms" },
  { key: "group", label: "Groups" },
  { key: "teacher", label: "Learning" }
];

type Props = {
  route?: { params?: RootStackParamList["Saved"] };
};

export function SavedScreen({ route }: Props = {}) {
  // Bottom-dock coupling: drives hide-on-scroll-down / reveal-on-scroll-up and
  // reserves the matching clearance so the last row never sits under the dock.
  const dock = useBottomNavSurface();
  const { t } = useTranslation();
  const { authState } = useAuth();
  // Wrong-subject guard: the saved library is the signed-in viewer's private
  // collection. On another profile's route params (deep link, stray call site)
  // this screen refuses instead of showing the viewer's library as theirs.
  const routeContext = resolveRouteProfileContext(route?.params, authState.user?.user_id);
  const [items, setItems] = useState<SavedItem[]>([]);
  const [collections, setCollections] = useState<SavedCollection[]>([]);
  const [type, setType] = useState<SavedContentType>("all");
  const [collectionId, setCollectionId] = useState(0);
  const [query, setQuery] = useState("");
  const [collectionName, setCollectionName] = useState("");
  const [editName, setEditName] = useState("");
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [offline, setOffline] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  /**
   * Playback happens here rather than by navigating away.
   *
   * A saved library video has no destination in the native stack — there is
   * ReelDetail and PostDetail and nothing that plays a single library video —
   * so tapping Play on one used to resolve to the Activity Inbox. The library
   * now returns resolved `media`, which is everything the shared viewer needs,
   * so the row plays where it sits. The viewer owns the playback claim and the
   * audio session; nothing about audio is configured from this screen.
   */
  const [viewer, setViewer] = useState<{ items: NativeMediaViewerItem[]; title: string } | null>(null);
  /**
   * The store keys currently on screen. Held in a ref rather than derived from
   * `items` inside the subscription so the subscription can stay mounted for
   * the life of the screen — resubscribing on every list change would drop
   * events that land between the unsubscribe and the resubscribe.
   */
  const presentKeys = useRef(new Set<string>());
  /**
   * Key -> every key belonging to the same row. Lets the store subscription
   * retire a reel and its backing post together without reading `items`, which
   * it deliberately does not close over.
   */
  const keyGroups = useRef(new Map<string, string[]>());
  const reload = useRef<() => void>(() => undefined);
  reload.current = () => { load("refresh").catch(() => undefined); };

  const selectedCollection = useMemo(() => collections.find((collection) => collection.id === collectionId) || null, [collectionId, collections]);

  async function load(mode: "initial" | "refresh" | "filter" = "initial", nextQuery = query) {
    setError("");
    setOffline(false);
    if (mode === "initial") setLoading(true);
    if (mode === "refresh") setRefreshing(true);
    try {
      const data = await listSavedContent({ type, collectionId, query: nextQuery });
      adoptItems(data.items || []);
      setCollections(data.collections || []);
    } catch (loadError) {
      const cached = await loadCachedSavedLibrary();
      if (cached) {
        adoptItems(cached.items || []);
        setCollections(cached.collections || []);
        setOffline(true);
      } else {
        // Never `loadError.message`. The backend answers every failing JSON API
        // path — reads included — with write-side copy ("Upload failed. Please
        // retry…"), so echoing the server's own words describes an upload the
        // user never started. Classify the failure and say what actually
        // happened: a read of this library did not come back.
        setError(describeSavedLibraryError(loadError));
      }
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }

  /**
   * Take a freshly loaded page as the truth for what is saved.
   *
   * Being in this list *is* the definition of saved, so every row seeds the
   * store. That is how a post whose feed card was rendered from a stale
   * payload — one fetched before the user saved it from somewhere else — shows
   * Saved the moment it scrolls back into view, instead of waiting for the feed
   * to be refetched.
   */
  const adoptItems = useCallback((next: SavedItem[]) => {
    const keys = new Set<string>();
    const groups = new Map<string, string[]>();
    next.forEach((item) => {
      const contentType = storeBackedType(item.content_type);
      if (!contentType) return;
      // Both identities of the row. The second one — the post backing a saved
      // Reel — is what makes the post card for the same video agree with this
      // list instead of offering to save content that is already here.
      const rowKeys = itemStoreKeys(item);
      rowKeys.forEach((key) => {
        keys.add(key);
        groups.set(key, rowKeys);
      });
      observeSavedState(contentType, item.content_id, true);
      const postId = Number(item.post_id || 0);
      if (postId > 0 && contentType !== "post") observeSavedState("post", postId, true);
    });
    presentKeys.current = keys;
    keyGroups.current = groups;
    setItems(next);
  }, []);

  useEffect(() => {
    // Owner-only fetch: skip entirely on a visitor route (no fetch-then-hide).
    if (!routeContext.isOwnProfile) return;
    load("initial").catch(() => undefined);
  }, [routeContext.isOwnProfile]);

  /**
   * An unsave performed anywhere else has to remove the row here — that is the
   * half of the mission's requirement this screen owns, and the reason the
   * store publishes a global channel at all. Removal is applied locally rather
   * than by refetching, so the row disappears at the speed of the tap.
   *
   * A *save* elsewhere is the opposite case: the row does not exist yet and
   * only the server can supply its id, collection and snapshot, so that one
   * does cost a refetch — but only when the content is not already listed,
   * which keeps a save on a feed card from reloading this screen needlessly.
   */
  useEffect(() => {
    if (!routeContext.isOwnProfile) return;
    return subscribeToSaveChanges((key, state) => {
    if (state.saved) {
      // Optimistic saves are not refetched — the row would arrive before the
      // server had agreed to create it. A rollback lands here too, with
      // `pending` false and the key already dropped below, which is what puts
      // a failed unsave's row back.
      if (state.pending) return;
      if (!presentKeys.current.has(key)) reload.current();
      return;
    }
    if (!presentKeys.current.has(key)) return;
    // Drop every identity of the row, not just the key that announced it.
    // A reel row answers to both `reel:<id>` and `post:<post_id>`; leaving the
    // sibling behind would make the next event for the same content look like a
    // row this screen still has, and trigger a refetch that finds nothing.
    const siblings = keyGroups.current.get(key) || [key];
    siblings.forEach((sibling) => {
      presentKeys.current.delete(sibling);
      keyGroups.current.delete(sibling);
    });
    setItems((current) => current.filter((item) => {
      const keys = itemStoreKeys(item);
      return !keys.length || !keys.includes(key);
    }));
    });
  }, [routeContext.isOwnProfile]);

  useEffect(() => {
    if (!routeContext.isOwnProfile) return;
    load("filter").catch(() => undefined);
  }, [type, collectionId]);

  useEffect(() => {
    if (!routeContext.isOwnProfile) return;
    const timer = setTimeout(() => load("filter", query).catch(() => undefined), 280);
    return () => clearTimeout(timer);
  }, [query]);

  async function handleCreateCollection() {
    const clean = collectionName.trim();
    if (clean.length < 2) return;
    setBusy(true);
    setError("");
    try {
      await createSavedCollection(clean);
      setCollectionName("");
      await load("refresh");
    } catch (createError) {
      // Same rule as `load`: never the thrown message. `err.message` here is
      // whatever the backend's generic JSON error handler said, which is upload
      // copy on every path, and `instanceof Error` does not make it any more
      // about collections. Classified and named for the action instead.
      setError(describeSavedActionError(createError, "create"));
    } finally {
      setBusy(false);
    }
  }

  async function handleUpdateCollection() {
    if (!selectedCollection) return;
    const clean = editName.trim() || selectedCollection.name;
    setBusy(true);
    setError("");
    try {
      await updateSavedCollection(selectedCollection.id, clean);
      setEditName("");
      await load("refresh");
    } catch (updateError) {
      setError(describeSavedActionError(updateError, "rename"));
    } finally {
      setBusy(false);
    }
  }

  async function handleDeleteCollection() {
    if (!selectedCollection || selectedCollection.is_default) return;
    setBusy(true);
    setError("");
    try {
      await deleteSavedCollection(selectedCollection.id);
      setCollectionId(0);
      await load("refresh");
    } catch (deleteError) {
      setError(describeSavedActionError(deleteError, "delete"));
    } finally {
      setBusy(false);
    }
  }

  /**
   * Remove goes through the save contract for anything with a card elsewhere.
   *
   * Deleting by library row id works, but it is invisible to the rest of the
   * app: the feed card for that post would go on showing Saved until its next
   * refetch, which is the same disagreement — read one way, written another —
   * that made Save unreliable in the first place. Types with no route (rooms,
   * groups, learning) keep the row-id path, since nothing else renders them.
   */
  async function handleRemove(item: SavedItem) {
    const contentType = storeBackedType(item.content_type);
    setBusy(true);
    setError("");
    try {
      if (contentType) {
        const outcome = await setSaved({ type: contentType, id: item.content_id }, false);
        // Deliberately not `outcome.message`: that string is worded for a Save
        // button ("Save could not be completed") and falls through to the raw
        // server message on an unmapped 4xx. The raw error is described here,
        // where the control is called Remove.
        //
        // Reported only when there *is* an error. A `!ok` with none means the
        // call was dropped as a duplicate or superseded by a newer one — the
        // previous code showed "Saved item could not be removed." for a second
        // tap that failed at nothing.
        if (!outcome.ok && outcome.error !== undefined) setError(describeSavedActionError(outcome.error, "remove"));
        return;
      }
      const previous = items;
      setItems((current) => current.filter((candidate) => candidate.id !== item.id));
      try {
        await removeSavedItem(item.id);
        await load("refresh");
      } catch (removeError) {
        setItems(previous);
        setError(describeSavedActionError(removeError, "remove"));
      }
    } finally {
      setBusy(false);
    }
  }

  async function handleMove(item: SavedItem) {
    const nextCollection = nextMoveCollection(collections, item.collection_id || 0);
    if (!nextCollection) return;
    setBusy(true);
    // Cleared like every other handler: without this a stale failure from an
    // earlier action stays on screen while a fresh move succeeds.
    setError("");
    try {
      await moveSavedItem(item.id, nextCollection.id);
      await load("refresh");
    } catch (moveError) {
      setError(describeSavedActionError(moveError, "move"));
    } finally {
      setBusy(false);
    }
  }

  async function handleOpen(item: SavedItem) {
    // Deleted content has nowhere to go: the destination would 404 into a
    // fallback screen that has nothing to do with what the user tapped.
    if (item.unavailable) return;
    await routeNotificationTarget(item.source_url || "/pulse/saved").catch(() => undefined);
  }

  function handlePlay(item: SavedItem) {
    const media = playableMediaFor(item);
    if (!media.length) return;
    setViewer({ items: media, title: item.title });
  }

  // Visitor destination with no visitor variant: refuse rather than render the
  // viewer's private library. All hooks above have already run.
  if (!routeContext.isOwnProfile) {
    return (
      <View style={styles.center}>
        <Text style={styles.centerText}>{PRIVATE_CONTENT_MESSAGE}</Text>
      </View>
    );
  }

  if (loading && !items.length) {
    return (
      <View style={styles.center}>
        <ActivityIndicator color={colors.accent} />
        <Text style={styles.centerText}>Loading Saved</Text>
      </View>
    );
  }

  return (
    <View style={styles.root}>
      <FlatList
        data={items}
        keyExtractor={(item) => String(item.id)}
        {...dock.handlers}
        contentContainerStyle={[styles.content, dock.contentPadding]}
        refreshControl={<RefreshControl refreshing={refreshing} tintColor={colors.accent} onRefresh={() => load("refresh").catch(() => undefined)} />}
        ListHeaderComponent={
          <View style={styles.header}>
            <Text style={styles.title}>Saved</Text>
            <Text style={styles.subtitle}>{offline ? "Showing saved library cache" : "Your private PulseSoc library"}</Text>
            <TextInput
              style={styles.searchInput}
              value={query}
              onChangeText={setQuery}
              placeholder="Search saved content"
              placeholderTextColor={colors.muted}
              returnKeyType="search"
            />
            <ScrollView horizontal showsHorizontalScrollIndicator={false} contentContainerStyle={styles.filterRow}>
              {TYPE_FILTERS.map((filter) => (
                <Pressable accessibilityRole="button" key={filter.key} style={[styles.filter, type === filter.key ? styles.filterActive : undefined]} onPress={() => setType(filter.key)}>
                  <Text style={[styles.filterText, type === filter.key ? styles.filterTextActive : undefined]}>{filter.label}</Text>
                </Pressable>
              ))}
            </ScrollView>
            <ScrollView horizontal showsHorizontalScrollIndicator={false} contentContainerStyle={styles.filterRow}>
              <Pressable accessibilityRole="button" style={[styles.filter, collectionId === 0 ? styles.filterActive : undefined]} onPress={() => setCollectionId(0)}>
                <Text style={[styles.filterText, collectionId === 0 ? styles.filterTextActive : undefined]}>All Collections</Text>
              </Pressable>
              {collections.map((collection) => (
                <Pressable accessibilityRole="button" key={collection.id} style={[styles.filter, collectionId === collection.id ? styles.filterActive : undefined]} onPress={() => setCollectionId(collection.id)}>
                  <Text style={[styles.filterText, collectionId === collection.id ? styles.filterTextActive : undefined]}>
                    {collection.name} {collection.item_count ? `(${collection.item_count})` : ""}
                  </Text>
                </Pressable>
              ))}
            </ScrollView>
            <View style={styles.collectionPanel}>
              <Text style={styles.panelTitle}>Collections</Text>
              <View style={styles.inlineForm}>
                <TextInput
                  style={styles.inlineInput}
                  value={collectionName}
                  onChangeText={setCollectionName}
                  placeholder="New collection"
                  placeholderTextColor={colors.muted}
                />
                <Pressable accessibilityRole="button" accessibilityState={{ disabled: busy }} style={styles.primaryButton} disabled={busy} onPress={handleCreateCollection}>
                  <Text style={styles.primaryText}>Create</Text>
                </Pressable>
              </View>
              {selectedCollection ? (
                <View style={styles.inlineForm}>
                  <TextInput
                    style={styles.inlineInput}
                    value={editName}
                    onChangeText={setEditName}
                    placeholder={`Rename ${selectedCollection.name}`}
                    placeholderTextColor={colors.muted}
                  />
                  <Pressable accessibilityRole="button" accessibilityState={{ disabled: busy }} style={styles.smallButton} disabled={busy} onPress={handleUpdateCollection}>
                    <Text style={styles.smallButtonText}>Rename</Text>
                  </Pressable>
                  <Pressable accessibilityRole="button" accessibilityState={{ disabled: busy || Boolean(selectedCollection.is_default) }} style={styles.smallButton} disabled={busy || Boolean(selectedCollection.is_default)} onPress={handleDeleteCollection}>
                    <Text style={styles.smallButtonText}>Delete</Text>
                  </Pressable>
                </View>
              ) : null}
            </View>
            {error ? <Text style={styles.error}>{error}</Text> : null}
          </View>
        }
        ListEmptyComponent={
          <View style={styles.empty}>
            <Text style={styles.emptyTitle}>{error ? "Saved unavailable" : "No saved items yet"}</Text>
            <Text style={styles.emptyText}>{error || "Save posts, Reels, Statuses, marketplace listings, videos, rooms, and learning content to build your library."}</Text>
          </View>
        }
        renderItem={({ item }) => (
          <SavedCard
            item={item}
            busy={busy}
            collections={collections}
            unavailableLabel={t("common:status.unavailable")}
            playLabel={t("common:actions.play")}
            onOpen={handleOpen}
            onPlay={handlePlay}
            onMove={handleMove}
            onRemove={handleRemove}
          />
        )}
      />
      <NativeMediaViewer
        visible={Boolean(viewer)}
        items={viewer?.items || []}
        title={viewer?.title}
        onClose={() => setViewer(null)}
      />
    </View>
  );
}

function SavedCard({ item, busy, collections, unavailableLabel, playLabel, onOpen, onPlay, onMove, onRemove }: {
  item: SavedItem;
  busy: boolean;
  collections: SavedCollection[];
  unavailableLabel: string;
  playLabel: string;
  onOpen: (item: SavedItem) => void;
  onPlay: (item: SavedItem) => void;
  onMove: (item: SavedItem) => void;
  onRemove: (item: SavedItem) => void;
}) {
  const moveTarget = nextMoveCollection(collections, item.collection_id || 0);
  /**
   * Deleted or newly private content still lists — a row disappearing on its own
   * reads as data loss, and Remove is the user's decision to make. What it loses
   * is every affordance that would fail: Open would route into a 404 recovery
   * screen, Play has nothing to play. Move and Remove still act on the row
   * itself, which is exactly what someone does with a dead bookmark.
   */
  const unavailable = Boolean(item.unavailable);
  const canPlay = !unavailable && playableMediaFor(item).length > 0;
  return (
    <View style={styles.card}>
      {item.thumbnail_url ? <Image source={{ uri: item.thumbnail_url }} style={styles.thumbnail} /> : <View style={styles.thumbnailFallback}><Text style={styles.thumbnailText}>{item.content_type}</Text></View>}
      <View style={styles.cardBody}>
        <Text style={styles.cardType}>{item.content_type}</Text>
        <Text style={[styles.cardTitle, unavailable ? styles.cardTitleUnavailable : undefined]} numberOfLines={1}>{item.title}</Text>
        <Text style={styles.cardPreview} numberOfLines={2}>{item.preview_text || "Open to view this saved PulseSoc item."}</Text>
        <Text style={styles.cardMeta} numberOfLines={1}>{item.collection_name || "Favorites"}</Text>
        {unavailable ? <Text style={styles.cardUnavailable}>{unavailableLabel}</Text> : null}
        <View style={styles.actionRow}>
          {canPlay ? (
            <Pressable accessibilityRole="button" accessibilityState={{ disabled: busy }} style={styles.smallButton} disabled={busy} onPress={() => onPlay(item)}>
              <Text style={styles.smallButtonText}>{playLabel}</Text>
            </Pressable>
          ) : null}
          <Pressable accessibilityRole="button" accessibilityState={{ disabled: busy || unavailable }} style={styles.smallButton} disabled={busy || unavailable} onPress={() => onOpen(item)}>
            <Text style={styles.smallButtonText}>Open</Text>
          </Pressable>
          <Pressable accessibilityRole="button" accessibilityState={{ disabled: busy || !moveTarget }} style={styles.smallButton} disabled={busy || !moveTarget} onPress={() => onMove(item)}>
            <Text style={styles.smallButtonText}>{moveTarget ? `Move to ${moveTarget.name}` : "Move"}</Text>
          </Pressable>
          <Pressable accessibilityRole="button" accessibilityState={{ disabled: busy }} style={styles.smallButton} disabled={busy} onPress={() => onRemove(item)}>
            <Text style={styles.smallButtonText}>Remove</Text>
          </Pressable>
        </View>
      </View>
    </View>
  );
}

function nextMoveCollection(collections: SavedCollection[], currentId: number) {
  const candidates = collections.filter((collection) => collection.id !== currentId);
  return candidates[0] || null;
}

const styles = createThemedStyles(() => ({
  actionRow: {
    flexDirection: "row",
    flexWrap: "wrap",
    gap: 8,
    marginTop: 10
  },
  card: {
    backgroundColor: colors.surface,
    borderColor: colors.border,
    borderRadius: 8,
    borderWidth: 1,
    flexDirection: "row",
    gap: 12,
    marginBottom: 12,
    padding: 12
  },
  cardBody: {
    flex: 1
  },
  cardMeta: {
    color: colors.accent,
    fontSize: 11,
    fontWeight: "800",
    marginTop: 5
  },
  cardPreview: {
    color: colors.muted,
    fontSize: 13,
    lineHeight: 18,
    marginTop: 4
  },
  cardTitle: {
    color: colors.text,
    fontSize: 16,
    fontWeight: "900"
  },
  cardTitleUnavailable: {
    opacity: 0.55
  },
  cardType: {
    color: colors.accent,
    fontSize: 11,
    fontWeight: "900",
    marginBottom: 3,
    textTransform: "uppercase"
  },
  cardUnavailable: {
    color: colors.muted,
    fontSize: 11,
    fontWeight: "900",
    marginTop: 5,
    textTransform: "uppercase"
  },
  center: {
    alignItems: "center",
    backgroundColor: "transparent",
    flex: 1,
    justifyContent: "center",
    padding: 24
  },
  centerText: {
    color: colors.muted,
    marginTop: 12
  },
  collectionPanel: {
    backgroundColor: colors.surface,
    borderColor: colors.border,
    borderRadius: 8,
    borderWidth: 1,
    gap: 10,
    marginTop: 14,
    padding: 12
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
  error: {
    color: "#ff9f9f",
    marginTop: 10
  },
  filter: {
    borderColor: colors.border,
    borderRadius: 8,
    borderWidth: 1,
    minHeight: 34,
    paddingHorizontal: 12,
    paddingVertical: 8
  },
  filterActive: {
    backgroundColor: "rgba(37, 208, 167, 0.14)",
    borderColor: colors.accent
  },
  filterRow: {
    gap: 8,
    paddingTop: 12
  },
  filterText: {
    color: colors.muted,
    fontSize: 12,
    fontWeight: "900"
  },
  filterTextActive: {
    color: colors.accent
  },
  header: {
    marginBottom: 14
  },
  inlineForm: {
    flexDirection: "row",
    flexWrap: "wrap",
    gap: 8
  },
  inlineInput: {
    backgroundColor: colors.background,
    borderColor: colors.border,
    borderRadius: 8,
    borderWidth: 1,
    color: colors.text,
    flex: 1,
    minHeight: 42,
    minWidth: 150,
    paddingHorizontal: 10
  },
  panelTitle: {
    color: colors.text,
    fontSize: 15,
    fontWeight: "900"
  },
  primaryButton: {
    backgroundColor: colors.accent,
    borderRadius: 8,
    justifyContent: "center",
    minHeight: 42,
    paddingHorizontal: 14
  },
  primaryText: {
    color: colors.background,
    fontWeight: "900"
  },
  root: {
    backgroundColor: "transparent",
    flex: 1
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
  smallButton: {
    borderColor: colors.border,
    borderRadius: 8,
    borderWidth: 1,
    justifyContent: "center",
    minHeight: 34,
    paddingHorizontal: 10,
    paddingVertical: 8
  },
  smallButtonText: {
    color: colors.text,
    fontSize: 12,
    fontWeight: "900"
  },
  subtitle: {
    color: colors.muted,
    lineHeight: 20,
    marginTop: 4
  },
  thumbnail: {
    backgroundColor: colors.surfaceRaised,
    borderRadius: 8,
    height: 88,
    width: 88
  },
  thumbnailFallback: {
    alignItems: "center",
    backgroundColor: colors.surfaceRaised,
    borderColor: colors.border,
    borderRadius: 8,
    borderWidth: 1,
    height: 88,
    justifyContent: "center",
    width: 88
  },
  thumbnailText: {
    color: colors.muted,
    fontSize: 11,
    fontWeight: "900",
    textAlign: "center",
    textTransform: "uppercase"
  },
  title: {
    color: colors.text,
    fontSize: 28,
    fontWeight: "900"
  }
}));
