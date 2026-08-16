import { NativeStackScreenProps } from "@react-navigation/native-stack";
import { useCallback, useEffect, useState } from "react";
import {
  ActivityIndicator,
  Alert,
  Image,
  Pressable,
  RefreshControl,
  ScrollView,
  StyleSheet,
  Text,
  View
} from "react-native";
import {
  getPageManageView,
  listMyPages,
  PageManageView,
  PageStatus,
  pageTypeLabel,
  PulsePage,
  requestPageVerification,
  setPageStatus
} from "../api/pages";
import { PulseApiError } from "../api/pulseApi";
import { RootStackParamList } from "../navigation/types";
import { colors } from "../theme/colors";
import { createThemedStyles } from "../theme/themedStyles";

type Props = NativeStackScreenProps<RootStackParamList, "PagesHub">;

/**
 * Page types whose management continues into Business OS. A Presence answers
 * WHO the identity is; Business OS is HOW it operates — this hub links the
 * two, it never embeds Business OS features here.
 */
const BUSINESS_TYPES = new Set([
  "BUSINESS", "BRAND", "STORE", "RESTAURANT", "PROFESSIONAL_SERVICE",
  "LOCAL_BUSINESS", "NONPROFIT", "ORGANIZATION", "MEDIA", "VENUE", "EDUCATION"
]);

/**
 * Page OS hub — every page the signed-in user belongs to, with management for
 * the selected one. Management surfaces route into the EXISTING canonical
 * systems (Advertising, Marketplace manager, Payments); this screen links,
 * it does not duplicate.
 *
 * What appears here is role-gated by the server: the manage view only carries
 * members/analytics when the caller's role allows them, and status changes are
 * owner-only server-side regardless of what the client shows.
 */
export function PagesHubScreen({ route, navigation }: Props) {
  const focusPageId = route.params?.focusPageId;
  const [pages, setPages] = useState<PulsePage[]>([]);
  const [selectedId, setSelectedId] = useState<number | null>(null);
  const [manage, setManage] = useState<PageManageView | null>(null);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState("");

  const load = useCallback(async () => {
    try {
      const mine = await listMyPages();
      setPages(mine);
      const target = focusPageId && mine.some((p) => p.id === focusPageId)
        ? focusPageId
        : mine[0]?.id ?? null;
      setSelectedId((current) => (current && mine.some((p) => p.id === current) ? current : target));
    } catch {
      setNotice("Your pages could not be loaded.");
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }, [focusPageId]);

  useEffect(() => {
    load();
  }, [load]);

  useEffect(() => {
    let cancelled = false;
    setManage(null);
    if (!selectedId) return;
    getPageManageView(selectedId)
      .then((view) => {
        if (!cancelled) setManage(view);
      })
      .catch(() => {
        if (!cancelled) setManage(null);
      });
    return () => {
      cancelled = true;
    };
  }, [selectedId]);

  const selected = pages.find((p) => p.id === selectedId) || null;
  const capabilities = new Set(manage?.capabilities || []);
  const isOwner = manage?.role === "OWNER";

  async function changeStatus(status: PageStatus) {
    if (!selected || busy) return;
    setBusy(true);
    setNotice("");
    try {
      await setPageStatus(selected.id, status);
      setNotice(`Page is now ${status.toLowerCase()}.`);
      await load();
    } catch (statusError) {
      setNotice(statusError instanceof PulseApiError ? statusError.message : "Status change failed.");
    } finally {
      setBusy(false);
    }
  }

  function confirmDeactivate() {
    if (!selected) return;
    Alert.alert(
      "Deactivate page?",
      "The page will no longer be publicly visible. Nothing is deleted — history stays auditable and an owner can review it later.",
      [
        { text: "Cancel", style: "cancel" },
        { text: "Deactivate", style: "destructive", onPress: () => changeStatus("DEACTIVATED") }
      ]
    );
  }

  async function askVerification() {
    if (!selected || busy) return;
    setBusy(true);
    setNotice("");
    try {
      const result = await requestPageVerification(selected.id);
      setNotice(result.message || "Verification request submitted for review.");
    } catch (verifyError) {
      setNotice(verifyError instanceof PulseApiError ? verifyError.message : "Verification request failed.");
    } finally {
      setBusy(false);
    }
  }

  if (loading) {
    return (
      <View style={[styles.root, styles.center]}>
        <ActivityIndicator color={colors.accent} size="large" />
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
          tintColor={colors.accent}
        />
      }
    >
      <Pressable
        accessibilityRole="button"
        style={styles.createButton}
        onPress={() => navigation.navigate("PageCreate")}
      >
        <Text style={styles.createButtonText}>+ Create a Presence</Text>
      </Pressable>

      {!pages.length ? (
        <Text style={styles.empty}>
          You don't manage any presences yet. Create one for your artist project, business or
          organization — your personal account stays exactly as it is.
        </Text>
      ) : null}

      <ScrollView horizontal showsHorizontalScrollIndicator={false} contentContainerStyle={styles.pageRow}>
        {pages.map((page) => (
          <Pressable
            key={page.id}
            accessibilityRole="button"
            accessibilityState={{ selected: page.id === selectedId }}
            style={[styles.pageChip, page.id === selectedId && styles.pageChipActive]}
            onPress={() => setSelectedId(page.id)}
          >
            {page.avatar_url ? (
              <Image source={{ uri: page.avatar_url }} style={styles.pageChipAvatar} />
            ) : null}
            <View>
              <Text style={[styles.pageChipName, page.id === selectedId && styles.pageChipNameActive]}>
                {page.name}
              </Text>
              <Text style={styles.pageChipMeta}>
                {pageTypeLabel(page.page_type)} · {page.role || ""}
              </Text>
            </View>
          </Pressable>
        ))}
      </ScrollView>

      {selected ? (
        <View style={styles.card}>
          <Text style={styles.cardTitle}>@{selected.handle}</Text>
          <Text style={styles.cardMeta}>
            Status: {selected.status} · {selected.verification_status}
          </Text>
          <Text style={styles.cardMeta}>
            {selected.followers_count} followers · {selected.posts_count} posts
          </Text>

          <View style={styles.actionsGrid}>
            <Pressable
              accessibilityRole="button"
              style={styles.action}
              onPress={() => navigation.navigate("Page", { pageId: selected.id, handle: selected.handle, title: selected.name })}
            >
              <Text style={styles.actionText}>View public page</Text>
            </Pressable>
            {manage && BUSINESS_TYPES.has(selected.page_type) ? (
              <Pressable
                accessibilityRole="button"
                style={styles.action}
                onPress={() => navigation.navigate("BusinessOs", { title: selected.name })}
              >
                <Text style={styles.actionText}>Open Business OS</Text>
              </Pressable>
            ) : null}
            {capabilities.has("manage_ads") ? (
              <Pressable
                accessibilityRole="button"
                style={styles.action}
                onPress={() => navigation.navigate("BusinessOsAdvertising", { title: selected.name })}
              >
                <Text style={styles.actionText}>Advertising</Text>
              </Pressable>
            ) : null}
            {capabilities.has("manage_marketplace") ? (
              <Pressable
                accessibilityRole="button"
                style={styles.action}
                onPress={() => navigation.navigate("MarketplaceManager", { title: selected.name })}
              >
                <Text style={styles.actionText}>Marketplace</Text>
              </Pressable>
            ) : null}
            {isOwner ? (
              <Pressable
                accessibilityRole="button"
                style={styles.action}
                onPress={() => navigation.navigate("BusinessOsPayments", { title: selected.name })}
              >
                <Text style={styles.actionText}>Payments</Text>
              </Pressable>
            ) : null}
          </View>

          {manage?.analytics ? (
            <View style={styles.analyticsCard}>
              <Text style={styles.sectionTitle}>Measured analytics</Text>
              <Text style={styles.cardMeta}>
                Followers: {manage.analytics.followers}
                {typeof manage.analytics.followers_7d === "number"
                  ? ` (+${manage.analytics.followers_7d} this week, +${manage.analytics.followers_30d ?? 0} this month)`
                  : ""}
              </Text>
              <Text style={styles.cardMeta}>
                Posts: {manage.analytics.posts}
                {typeof manage.analytics.posts_30d === "number" ? ` (+${manage.analytics.posts_30d} this month)` : ""}
              </Text>
              <Text style={styles.cardMeta}>Team members: {manage.analytics.team_members}</Text>
              {manage.analytics.note ? <Text style={styles.note}>{manage.analytics.note}</Text> : null}
            </View>
          ) : null}

          {manage?.completeness ? (
            <View style={styles.analyticsCard}>
              <Text style={styles.sectionTitle}>Profile completeness — {manage.completeness.percent}%</Text>
              <View style={styles.meterTrack}>
                <View
                  style={[styles.meterFill, { width: `${Math.min(100, Math.max(0, manage.completeness.percent))}%` }]}
                />
              </View>
              {manage.completeness.items
                .filter((item) => !item.done)
                .map((item) => (
                  <Text key={item.key} style={styles.cardMeta}>
                    ○ {item.label}
                  </Text>
                ))}
              {manage.completeness.percent >= 100 ? (
                <Text style={styles.note}>All set — nothing left to add.</Text>
              ) : null}
            </View>
          ) : null}

          {manage?.members ? (
            <View style={styles.analyticsCard}>
              <Text style={styles.sectionTitle}>Team</Text>
              {manage.members.map((member) => (
                <Text key={member.user_id} style={styles.cardMeta}>
                  {member.display_name || member.username || `Member ${member.user_id}`} — {member.role}
                  {member.status === "invited" ? " (invited)" : ""}
                </Text>
              ))}
            </View>
          ) : null}

          {isOwner ? (
            <View style={styles.ownerZone}>
              <Text style={styles.sectionTitle}>Owner controls</Text>
              <View style={styles.actionsGrid}>
                {selected.status !== "ACTIVE" ? (
                  <Pressable accessibilityRole="button" style={styles.action} disabled={busy} onPress={() => changeStatus("ACTIVE")}>
                    <Text style={styles.actionText}>Activate</Text>
                  </Pressable>
                ) : (
                  <Pressable accessibilityRole="button" style={styles.action} disabled={busy} onPress={() => changeStatus("PAUSED")}>
                    <Text style={styles.actionText}>Pause</Text>
                  </Pressable>
                )}
                <Pressable accessibilityRole="button" style={styles.action} disabled={busy} onPress={() => changeStatus("UNPUBLISHED")}>
                  <Text style={styles.actionText}>Unpublish</Text>
                </Pressable>
                <Pressable accessibilityRole="button" style={[styles.action, styles.dangerAction]} disabled={busy} onPress={confirmDeactivate}>
                  <Text style={styles.dangerText}>Deactivate</Text>
                </Pressable>
              </View>
              {selected.verification_status === "unverified" ? (
                <Pressable accessibilityRole="button" style={styles.action} disabled={busy} onPress={askVerification}>
                  <Text style={styles.actionText}>Request verification</Text>
                </Pressable>
              ) : null}
              <Text style={styles.note}>
                Ownership transfer requires explicit confirmation and is fully audited. Verification
                is reviewed — it is never granted automatically.
              </Text>
            </View>
          ) : null}
        </View>
      ) : null}

      {notice ? <Text style={styles.notice}>{notice}</Text> : null}
    </ScrollView>
  );
}

const styles = createThemedStyles(() => ({
  action: {
    borderColor: colors.border,
    borderRadius: 8,
    borderWidth: 1,
    paddingHorizontal: 14,
    paddingVertical: 10
  },
  actionText: {
    color: colors.text,
    fontSize: 13,
    fontWeight: "800"
  },
  actionsGrid: {
    flexDirection: "row",
    flexWrap: "wrap",
    gap: 8,
    marginTop: 10
  },
  analyticsCard: {
    borderTopColor: colors.border,
    borderTopWidth: 1,
    gap: 4,
    marginTop: 14,
    paddingTop: 12
  },
  card: {
    backgroundColor: colors.surface,
    borderColor: colors.border,
    borderRadius: 12,
    borderWidth: 1,
    marginTop: 14,
    padding: 16
  },
  cardMeta: {
    color: colors.muted,
    fontSize: 13
  },
  cardTitle: {
    color: colors.accent,
    fontSize: 16,
    fontWeight: "900"
  },
  center: {
    alignItems: "center",
    justifyContent: "center"
  },
  content: {
    padding: 16,
    paddingBottom: 48
  },
  createButton: {
    alignItems: "center",
    backgroundColor: colors.accent,
    borderRadius: 10,
    minHeight: 44,
    justifyContent: "center"
  },
  createButtonText: {
    color: colors.background,
    fontSize: 15,
    fontWeight: "900"
  },
  dangerAction: {
    borderColor: colors.danger
  },
  dangerText: {
    color: colors.danger,
    fontSize: 13,
    fontWeight: "800"
  },
  empty: {
    color: colors.muted,
    fontSize: 14,
    lineHeight: 21,
    marginTop: 16,
    textAlign: "center"
  },
  meterFill: {
    backgroundColor: colors.accent,
    borderRadius: 3,
    height: 6
  },
  meterTrack: {
    backgroundColor: colors.border,
    borderRadius: 3,
    height: 6,
    marginVertical: 6,
    overflow: "hidden"
  },
  note: {
    color: colors.muted,
    fontSize: 11,
    lineHeight: 16,
    marginTop: 8
  },
  notice: {
    color: colors.accent,
    fontWeight: "800",
    marginTop: 14,
    textAlign: "center"
  },
  ownerZone: {
    borderTopColor: colors.border,
    borderTopWidth: 1,
    marginTop: 14,
    paddingTop: 12
  },
  pageChip: {
    alignItems: "center",
    borderColor: colors.border,
    borderRadius: 10,
    borderWidth: 1,
    flexDirection: "row",
    gap: 8,
    padding: 10
  },
  pageChipActive: {
    backgroundColor: colors.signalDim,
    borderColor: colors.accent
  },
  pageChipAvatar: {
    borderRadius: 14,
    height: 28,
    width: 28
  },
  pageChipMeta: {
    color: colors.muted,
    fontSize: 10
  },
  pageChipName: {
    color: colors.text,
    fontSize: 13,
    fontWeight: "800"
  },
  pageChipNameActive: {
    color: colors.accent
  },
  pageRow: {
    gap: 8,
    marginTop: 14
  },
  root: {
    backgroundColor: colors.background,
    flex: 1
  },
  sectionTitle: {
    color: colors.text,
    fontSize: 13,
    fontWeight: "900"
  }
}));
