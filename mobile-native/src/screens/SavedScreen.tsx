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
import { useBottomNavSurface } from "../navigation/BottomNavVisibility";
import { routeNotificationTarget } from "../navigation/notificationRouting";
import { colors } from "../theme/colors";

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

export function SavedScreen() {
  // Bottom-dock coupling: drives hide-on-scroll-down / reveal-on-scroll-up and
  // reserves the matching clearance so the last row never sits under the dock.
  const dock = useBottomNavSurface();
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

  const selectedCollection = useMemo(() => collections.find((collection) => collection.id === collectionId) || null, [collectionId, collections]);

  async function load(mode: "initial" | "refresh" | "filter" = "initial", nextQuery = query) {
    setError("");
    setOffline(false);
    if (mode === "initial") setLoading(true);
    if (mode === "refresh") setRefreshing(true);
    try {
      const data = await listSavedContent({ type, collectionId, query: nextQuery });
      setItems(data.items || []);
      setCollections(data.collections || []);
    } catch (loadError) {
      const cached = await loadCachedSavedLibrary();
      if (cached) {
        setItems(cached.items || []);
        setCollections(cached.collections || []);
        setOffline(true);
      } else {
        setError(loadError instanceof Error ? loadError.message : "Saved content could not load.");
      }
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }

  useEffect(() => {
    load("initial").catch(() => undefined);
  }, []);

  useEffect(() => {
    load("filter").catch(() => undefined);
  }, [type, collectionId]);

  useEffect(() => {
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
      setError(createError instanceof Error ? createError.message : "Collection could not be created.");
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
      setError(updateError instanceof Error ? updateError.message : "Collection could not be updated.");
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
      setError(deleteError instanceof Error ? deleteError.message : "Collection could not be deleted.");
    } finally {
      setBusy(false);
    }
  }

  async function handleRemove(item: SavedItem) {
    setBusy(true);
    const previous = items;
    setItems((current) => current.filter((candidate) => candidate.id !== item.id));
    try {
      await removeSavedItem(item.id);
      await load("refresh");
    } catch (removeError) {
      setItems(previous);
      setError(removeError instanceof Error ? removeError.message : "Saved item could not be removed.");
    } finally {
      setBusy(false);
    }
  }

  async function handleMove(item: SavedItem) {
    const nextCollection = nextMoveCollection(collections, item.collection_id || 0);
    if (!nextCollection) return;
    setBusy(true);
    try {
      await moveSavedItem(item.id, nextCollection.id);
      await load("refresh");
    } catch (moveError) {
      setError(moveError instanceof Error ? moveError.message : "Saved item could not be moved.");
    } finally {
      setBusy(false);
    }
  }

  async function handleOpen(item: SavedItem) {
    await routeNotificationTarget(item.source_url || "/pulse/saved").catch(() => undefined);
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
        renderItem={({ item }) => <SavedCard item={item} busy={busy} collections={collections} onOpen={handleOpen} onMove={handleMove} onRemove={handleRemove} />}
      />
    </View>
  );
}

function SavedCard({ item, busy, collections, onOpen, onMove, onRemove }: {
  item: SavedItem;
  busy: boolean;
  collections: SavedCollection[];
  onOpen: (item: SavedItem) => void;
  onMove: (item: SavedItem) => void;
  onRemove: (item: SavedItem) => void;
}) {
  const moveTarget = nextMoveCollection(collections, item.collection_id || 0);
  return (
    <View style={styles.card}>
      {item.thumbnail_url ? <Image source={{ uri: item.thumbnail_url }} style={styles.thumbnail} /> : <View style={styles.thumbnailFallback}><Text style={styles.thumbnailText}>{item.content_type}</Text></View>}
      <View style={styles.cardBody}>
        <Text style={styles.cardType}>{item.content_type}</Text>
        <Text style={styles.cardTitle} numberOfLines={1}>{item.title}</Text>
        <Text style={styles.cardPreview} numberOfLines={2}>{item.preview_text || "Open to view this saved PulseSoc item."}</Text>
        <Text style={styles.cardMeta} numberOfLines={1}>{item.collection_name || "Favorites"}</Text>
        <View style={styles.actionRow}>
          <Pressable accessibilityRole="button" accessibilityState={{ disabled: busy }} style={styles.smallButton} disabled={busy} onPress={() => onOpen(item)}>
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

const styles = StyleSheet.create({
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
  cardType: {
    color: colors.accent,
    fontSize: 11,
    fontWeight: "900",
    marginBottom: 3,
    textTransform: "uppercase"
  },
  center: {
    alignItems: "center",
    backgroundColor: colors.background,
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
    backgroundColor: colors.background,
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
});
