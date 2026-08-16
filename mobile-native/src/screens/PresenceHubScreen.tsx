import { NativeStackScreenProps } from "@react-navigation/native-stack";
import { useCallback, useEffect, useState } from "react";
import {
  ActivityIndicator,
  Image,
  Pressable,
  RefreshControl,
  ScrollView,
  Text,
  View
} from "react-native";
import { listMyPages, pageTypeLabel, PulsePage } from "../api/pages";
import { RootStackParamList } from "../navigation/types";
import { colors } from "../theme/colors";
import { presenceTheme } from "../theme/presenceTheme";
import { createThemedStyles } from "../theme/themedStyles";

type Props = NativeStackScreenProps<RootStackParamList, "Presence">;

/** Page types whose management naturally continues into Business OS. */
const BUSINESS_TYPES = new Set([
  "BUSINESS", "BRAND", "STORE", "RESTAURANT", "PROFESSIONAL_SERVICE",
  "LOCAL_BUSINESS", "NONPROFIT", "ORGANIZATION", "MEDIA", "VENUE", "EDUCATION"
]);

const ARTIST_TYPES = new Set(["ARTIST", "CREATOR", "PUBLIC_FIGURE", "SPORTS_TEAM"]);

function presenceKind(page: PulsePage): "artist" | "business" {
  if (ARTIST_TYPES.has(page.page_type)) return "artist";
  if (BUSINESS_TYPES.has(page.page_type)) return "business";
  return "business";
}

/**
 * Presence Home — the Profile OS destination for professional identities.
 *
 * "Presence" is PulseSoc's word for an artist, business, brand or organization
 * identity controlled by one or more authorized members. Underneath it is the
 * canonical Page OS (`/api/pages/*`): this screen creates and lists pages, it
 * does not own a second backend, social graph or permission engine.
 *
 * The list is deliberately lightweight: `listMyPages()` returns summary rows
 * (name, handle, type, role, status) — no analytics, no manage views. Deeper
 * data loads only when a Presence's management is opened.
 */
export function PresenceHubScreen({ navigation }: Props) {
  const [pages, setPages] = useState<PulsePage[]>([]);
  const [state, setState] = useState<"loading" | "loaded" | "error">("loading");
  const [refreshing, setRefreshing] = useState(false);

  const load = useCallback(async () => {
    try {
      setPages(await listMyPages());
      setState("loaded");
    } catch {
      setState("error");
    } finally {
      setRefreshing(false);
    }
  }, []);

  useEffect(() => {
    load();
    // Refresh when returning from creation so a new Presence appears at once.
    const unsubscribe = navigation.addListener("focus", load);
    return unsubscribe;
  }, [load, navigation]);

  if (state === "loading") {
    return (
      <View style={[styles.root, styles.center]}>
        <ActivityIndicator color={presenceTheme.teal} size="large" />
      </View>
    );
  }

  if (state === "error") {
    return (
      <View style={[styles.root, styles.center]}>
        <Text style={styles.errorText}>Your presences could not be loaded.</Text>
        <Pressable accessibilityRole="button" style={styles.retryButton} onPress={() => { setState("loading"); load(); }}>
          <Text style={styles.retryText}>Try again</Text>
        </Pressable>
      </View>
    );
  }

  return (
    <ScrollView
      style={styles.root}
      contentContainerStyle={styles.content}
      refreshControl={
        <RefreshControl
          refreshing={refreshing}
          onRefresh={() => {
            setRefreshing(true);
            load();
          }}
          tintColor={presenceTheme.teal}
        />
      }
    >
      <Text style={styles.header}>PRESENCE</Text>
      <Text style={styles.subtitle}>Your public identities on PulseSoc.</Text>
      <Text style={styles.supporting}>
        Create and manage identities for your art, brand, organization, or business.
        Your personal account stays exactly as it is.
      </Text>

      <View style={styles.offerCard}>
        <Text style={styles.offerTitle}>Artist Presence</Text>
        <Text style={styles.offerLead}>Build your official artist home.</Text>
        <Text style={styles.offerList}>Music · Releases · Videos · Events · Fans · Store · Insights</Text>
        <Pressable
          accessibilityRole="button"
          style={styles.offerButton}
          onPress={() => navigation.navigate("PageCreate", { flavor: "artist" })}
        >
          <Text style={styles.offerButtonText}>Create Artist Presence</Text>
        </Pressable>
      </View>

      <View style={styles.offerCard}>
        <Text style={styles.offerTitle}>Business Presence</Text>
        <Text style={styles.offerLead}>Build your official business home.</Text>
        <Text style={styles.offerList}>Products · Services · Store · Marketplace · Events · Customers · Insights</Text>
        <Pressable
          accessibilityRole="button"
          style={styles.offerButton}
          onPress={() => navigation.navigate("PageCreate", { flavor: "business" })}
        >
          <Text style={styles.offerButtonText}>Create Business Presence</Text>
        </Pressable>
      </View>

      <Text style={styles.sectionTitle}>YOUR PRESENCES</Text>

      {!pages.length ? (
        <Text style={styles.empty}>You haven't created a Presence yet.</Text>
      ) : (
        pages.map((page) => {
          const kind = presenceKind(page);
          return (
            <View key={page.id} style={styles.presenceCard}>
              <View style={styles.presenceIdentity}>
                {page.avatar_url ? (
                  <Image source={{ uri: page.avatar_url }} style={styles.presenceAvatar} />
                ) : (
                  <View style={[styles.presenceAvatar, styles.presenceAvatarFallback]}>
                    <Text style={styles.presenceAvatarInitial}>{page.name.slice(0, 1).toUpperCase()}</Text>
                  </View>
                )}
                <View style={styles.presenceMetaBlock}>
                  <Text style={styles.presenceName}>{page.name}</Text>
                  <Text style={styles.presenceMeta}>
                    {pageTypeLabel(page.page_type)} · {page.followers_count === 1 ? "1 follower" : `${page.followers_count} followers`}
                  </Text>
                  {/* Badges reflect real server state only: status straight from the
                      row, Verified only when the server granted it. No inferred flags. */}
                  <View style={styles.badgeRow}>
                    <Text style={[styles.badge, page.status === "ACTIVE" ? styles.badgePublic : styles.badgeDraft]}>
                      {page.status === "ACTIVE" ? "Public" : page.status.charAt(0) + page.status.slice(1).toLowerCase()}
                    </Text>
                    {page.verified ? <Text style={[styles.badge, styles.badgeVerified]}>✓ Verified</Text> : null}
                  </View>
                </View>
              </View>
              <View style={styles.presenceActions}>
                <Pressable
                  accessibilityRole="button"
                  style={styles.presenceAction}
                  onPress={() => navigation.navigate("Page", { pageId: page.id, handle: page.handle, title: page.name })}
                >
                  <Text style={styles.presenceActionText}>View</Text>
                </Pressable>
                <Pressable
                  accessibilityRole="button"
                  style={styles.presenceAction}
                  onPress={() => navigation.navigate("PagesHub", { focusPageId: page.id })}
                >
                  <Text style={styles.presenceActionText}>Manage</Text>
                </Pressable>
                {kind === "business" ? (
                  <Pressable
                    accessibilityRole="button"
                    style={styles.presenceAction}
                    onPress={() => navigation.navigate("BusinessOs", { title: page.name })}
                  >
                    <Text style={styles.presenceActionText}>Business OS</Text>
                  </Pressable>
                ) : (
                  <Pressable
                    accessibilityRole="button"
                    style={styles.presenceAction}
                    onPress={() => navigation.navigate("PagesHub", { focusPageId: page.id })}
                  >
                    <Text style={styles.presenceActionText}>Insights</Text>
                  </Pressable>
                )}
              </View>
            </View>
          );
        })
      )}

      <Pressable
        accessibilityRole="button"
        style={styles.createNew}
        onPress={() => navigation.navigate("PageCreate", undefined)}
      >
        <Text style={styles.createNewText}>+ Create New</Text>
      </Pressable>
    </ScrollView>
  );
}

const styles = createThemedStyles(() => ({
  badge: {
    borderRadius: 6,
    fontSize: 10,
    fontWeight: "800",
    overflow: "hidden",
    paddingHorizontal: 6,
    paddingVertical: 2
  },
  badgeDraft: {
    backgroundColor: colors.border,
    color: colors.muted
  },
  badgePublic: {
    backgroundColor: presenceTheme.tealSoft,
    color: presenceTheme.teal
  },
  badgeRow: {
    flexDirection: "row",
    gap: 6,
    marginTop: 4
  },
  badgeVerified: {
    backgroundColor: presenceTheme.tealSoft,
    color: presenceTheme.teal
  },
  center: {
    alignItems: "center",
    gap: 12,
    justifyContent: "center",
    padding: 24
  },
  content: {
    padding: 16,
    paddingBottom: 48
  },
  createNew: {
    alignItems: "center",
    borderColor: presenceTheme.tealBorder,
    borderRadius: 10,
    borderStyle: "dashed",
    borderWidth: 1,
    marginTop: 16,
    minHeight: 44,
    justifyContent: "center"
  },
  createNewText: {
    color: presenceTheme.teal,
    fontSize: 14,
    fontWeight: "900"
  },
  empty: {
    color: colors.muted,
    fontSize: 14,
    lineHeight: 21,
    marginTop: 8,
    textAlign: "center"
  },
  errorText: {
    color: colors.muted,
    fontSize: 14,
    textAlign: "center"
  },
  header: {
    color: presenceTheme.teal,
    fontSize: 22,
    fontWeight: "900",
    letterSpacing: 2
  },
  offerButton: {
    alignItems: "center",
    backgroundColor: presenceTheme.teal,
    borderRadius: 10,
    marginTop: 12,
    minHeight: 44,
    justifyContent: "center"
  },
  offerButtonText: {
    color: colors.background,
    fontSize: 14,
    fontWeight: "900"
  },
  offerCard: {
    backgroundColor: colors.surface,
    borderColor: presenceTheme.tealBorder,
    borderRadius: presenceTheme.radius.card,
    borderWidth: 1,
    marginTop: 14,
    padding: 16
  },
  offerLead: {
    color: colors.text,
    fontSize: 13,
    marginTop: 4
  },
  offerList: {
    color: colors.muted,
    fontSize: 12,
    lineHeight: 18,
    marginTop: 6
  },
  offerTitle: {
    color: colors.text,
    fontSize: 16,
    fontWeight: "900"
  },
  presenceAction: {
    borderColor: colors.border,
    borderRadius: 8,
    borderWidth: 1,
    minHeight: 36,
    justifyContent: "center",
    paddingHorizontal: 12
  },
  presenceActionText: {
    color: colors.text,
    fontSize: 12,
    fontWeight: "800"
  },
  presenceActions: {
    flexDirection: "row",
    flexWrap: "wrap",
    gap: 8,
    marginTop: 12
  },
  presenceAvatar: {
    borderRadius: 20,
    height: 40,
    width: 40
  },
  presenceAvatarFallback: {
    alignItems: "center",
    backgroundColor: presenceTheme.tealSoft,
    justifyContent: "center"
  },
  presenceAvatarInitial: {
    color: presenceTheme.teal,
    fontSize: 16,
    fontWeight: "900"
  },
  presenceCard: {
    backgroundColor: colors.surface,
    borderColor: colors.border,
    borderRadius: 12,
    borderWidth: 1,
    marginTop: 10,
    padding: 14
  },
  presenceIdentity: {
    alignItems: "center",
    flexDirection: "row",
    gap: 10
  },
  presenceMeta: {
    color: colors.muted,
    fontSize: 12
  },
  presenceMetaBlock: {
    flex: 1
  },
  presenceName: {
    color: colors.text,
    fontSize: 15,
    fontWeight: "900"
  },
  retryButton: {
    borderColor: presenceTheme.tealBorder,
    borderRadius: 8,
    borderWidth: 1,
    minHeight: 44,
    justifyContent: "center",
    paddingHorizontal: 18
  },
  retryText: {
    color: presenceTheme.teal,
    fontSize: 13,
    fontWeight: "800"
  },
  root: {
    backgroundColor: colors.background,
    flex: 1
  },
  sectionTitle: {
    color: colors.muted,
    fontSize: 12,
    fontWeight: "900",
    letterSpacing: 1.5,
    marginTop: 22
  },
  subtitle: {
    color: colors.text,
    fontSize: 14,
    fontWeight: "700",
    marginTop: 6
  },
  supporting: {
    color: colors.muted,
    fontSize: 13,
    lineHeight: 19,
    marginTop: 4
  }
}));
