/**
 * Private Office — the first real native surface.
 *
 * ## What this screen is allowed to claim
 *
 * Nothing on it is decided here. The entry state, the list of children, and the
 * reason each child is or is not reachable all arrive from
 * `/api/private-office/overview`, which is rendered by
 * `services/private_office/office.product_state` over the canonical feature
 * matrix. This screen reads `opens` to decide tappability and `reason` to
 * decide copy, and it computes neither.
 *
 * That is deliberate to the point of being awkward: it would be shorter to keep
 * a local list of the seven capabilities and light them up by tier. It would
 * also be a second authority on what exists, and the first time a capability
 * ships or is killed the two would disagree — with the client winning, because
 * the client is what the member sees. So the list itself comes down the wire.
 * The only local table is `COPY_KEYS`, which maps a feature id to a translation
 * key, and an id missing from it still renders (as its raw id) rather than
 * silently vanishing from the list.
 *
 * ## Why "not built" and "needs a provider" are different rows
 *
 * The availability vocabulary collapses them; `reason` does not. A capability
 * nobody has built may one day be built by us. A capability that needs an
 * outside data provider cannot answer at all until that provider is connected —
 * and for `private_shield` in particular, drawing it as a merely-locked feature
 * invites the reading that we are already watching and would tell them. We are
 * not. So PROVIDER_REQUIRED gets its own words.
 *
 * ## Why a degraded resolve is not "you don't have this"
 *
 * ENTRY_UNKNOWN means the tier resolver did not answer. The screen says so and
 * offers a retry. Rendering it as an empty or locked office would be a
 * confident answer to a question we failed to ask, told to the member most
 * likely to have paid for the thing.
 */

import { NativeStackScreenProps } from "@react-navigation/native-stack";
import { useCallback, useEffect, useState } from "react";
import { ActivityIndicator, Pressable, RefreshControl, ScrollView, StyleSheet, Text, View } from "react-native";
import { Ionicons } from "@expo/vector-icons";
import { useSafeAreaInsets } from "react-native-safe-area-context";
import {
  PrivateOfficeChild,
  PrivateOfficeOverview,
  UNKNOWN_OVERVIEW,
  getPrivateOfficeOverview
} from "../api/privateOffice";
import { useTranslation } from "../i18n";
import { BOTTOM_NAV_CONTENT_CLEARANCE } from "../navigation/BottomNavVisibility";
import { RootStackParamList } from "../navigation/types";
import { PrivateOfficeLockGate } from "../privateOffice/PrivateOfficeLockGate";
import { colors } from "../theme/colors";

type Props = NativeStackScreenProps<RootStackParamList, "PrivateOffice">;

/**
 * Feature id → translation key stem. Copy only.
 *
 * This table says what a capability is *called*. It never says whether it is
 * available; that word always comes from the server row next to it.
 */
const COPY_KEYS: Readonly<Record<string, string>> = {
  private_facts: "privateFacts",
  capital_graph: "capitalGraph",
  private_briefings: "privateBriefings",
  relationship_intelligence: "relationshipIntelligence",
  private_shield: "privateShield",
  "private_shield.breach_monitoring": "breachMonitoring",
  "private_office.document.extraction": "documentIntelligence",
  human_concierge: "humanConcierge"
};

/**
 * Feature id → the screen that actually exists for it.
 *
 * One entry, because one capability is built. A row whose id is absent here is
 * never tappable even if the server said it opens — a missing destination is a
 * client bug, and the honest failure is a row that does not move rather than a
 * tap into a screen that is not registered.
 */
const DESTINATIONS: Readonly<Record<string, keyof RootStackParamList>> = {
  private_facts: "PrivateFacts"
};

const ICONS: Readonly<Record<string, keyof typeof Ionicons.glyphMap>> = {
  private_facts: "document-text-outline",
  capital_graph: "git-network-outline",
  private_briefings: "newspaper-outline",
  relationship_intelligence: "people-outline",
  private_shield: "shield-outline",
  "private_shield.breach_monitoring": "eye-outline",
  "private_office.document.extraction": "scan-outline",
  human_concierge: "person-circle-outline"
};

type LoadState = "LOADING" | "LOADED";

/**
 * The screen exports a gated shell (Stage 19: a deep link lands on the correct
 * lock door and this content resumes after unlock) and keeps the original
 * component as the unlocked body.
 */
export function PrivateOfficeScreen(props: Props) {
  return (
    <PrivateOfficeLockGate
      onDismiss={() => props.navigation.goBack()}
      onRenew={() => props.navigation.navigate("Premium")}
    >
      <PrivateOfficeBody {...props} />
    </PrivateOfficeLockGate>
  );
}

function PrivateOfficeBody({ navigation }: Props) {
  const { t } = useTranslation();
  const insets = useSafeAreaInsets();
  const [loadState, setLoadState] = useState<LoadState>("LOADING");
  const [refreshing, setRefreshing] = useState(false);
  const [overview, setOverview] = useState<PrivateOfficeOverview>(UNKNOWN_OVERVIEW);

  const load = useCallback(async () => {
    const next = await getPrivateOfficeOverview();
    setOverview(next);
    setLoadState("LOADED");
  }, []);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      const next = await getPrivateOfficeOverview();
      if (cancelled) return;
      setOverview(next);
      setLoadState("LOADED");
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  const onRefresh = useCallback(async () => {
    setRefreshing(true);
    try {
      await load();
    } finally {
      setRefreshing(false);
    }
  }, [load]);

  const open = useCallback(
    (child: PrivateOfficeChild) => {
      const destination = DESTINATIONS[child.featureId];
      if (!child.opens || !destination) return;
      navigation.navigate(destination as never);
    },
    [navigation]
  );

  const office = overview.office;
  const label = (featureId: string, part: "label" | "hint") => {
    const stem = COPY_KEYS[featureId];
    if (!stem) return part === "label" ? featureId : "";
    return t(`premium:privateOffice.features.${stem}.${part}`);
  };

  return (
    <ScrollView
      style={styles.root}
      contentContainerStyle={[
        styles.content,
        { paddingBottom: Math.max(insets.bottom, 18) + BOTTOM_NAV_CONTENT_CLEARANCE }
      ]}
      refreshControl={
        <RefreshControl refreshing={refreshing} onRefresh={onRefresh} tintColor={colors.accent} />
      }
    >
      <View style={styles.header}>
        <Text style={styles.title}>{t("premium:privateOffice.title")}</Text>
        <Text style={styles.subtitle}>{t("premium:privateOffice.subtitle")}</Text>
      </View>

      {loadState === "LOADING" ? (
        <View style={styles.panel} accessibilityRole="progressbar">
          <ActivityIndicator color={colors.accent} />
          <Text style={styles.panelText}>{t("premium:privateOffice.loading")}</Text>
        </View>
      ) : null}

      {loadState === "LOADED" && office.state === "ENTRY_UNKNOWN" ? (
        <View style={styles.panel}>
          <Ionicons name="cloud-offline-outline" size={22} color={colors.warning} />
          <Text style={styles.panelTitle}>{t("premium:privateOffice.unknown.title")}</Text>
          <Text style={styles.panelText}>{t("premium:privateOffice.unknown.body")}</Text>
          <Pressable style={styles.retry} onPress={onRefresh} accessibilityRole="button">
            <Text style={styles.retryText}>{t("premium:privateOffice.retry")}</Text>
          </Pressable>
        </View>
      ) : null}

      {loadState === "LOADED" && office.state === "ENTRY_UPGRADE_REQUIRED" ? (
        <View style={styles.panel}>
          <Ionicons name="lock-closed-outline" size={22} color={colors.warning} />
          <Text style={styles.panelTitle}>{t("premium:privateOffice.upgrade.title")}</Text>
          <Text style={styles.panelText}>
            {office.upgradeTier
              ? t("premium:privateOffice.upgrade.body", { tier: office.upgradeTier })
              : t("premium:privateOffice.upgrade.bodyGeneric")}
          </Text>
        </View>
      ) : null}

      {loadState === "LOADED" && office.state === "ENTRY_UNAVAILABLE" ? (
        <View style={styles.panel}>
          <Ionicons name="construct-outline" size={22} color={colors.muted} />
          <Text style={styles.panelTitle}>{t("premium:privateOffice.unavailable.title")}</Text>
          <Text style={styles.panelText}>{t("premium:privateOffice.unavailable.body")}</Text>
        </View>
      ) : null}

      {office.available.length ? (
        <View style={styles.section}>
          <Text style={styles.sectionTitle}>{t("premium:privateOffice.sections.available")}</Text>
          {office.available.map((child) => (
            <Pressable
              key={child.featureId}
              style={styles.rowOpen}
              onPress={() => open(child)}
              disabled={!DESTINATIONS[child.featureId]}
              accessibilityRole="button"
              accessibilityLabel={label(child.featureId, "label")}
            >
              <Ionicons
                name={ICONS[child.featureId] || "ellipse-outline"}
                size={20}
                color={colors.accent}
              />
              <View style={styles.rowBody}>
                <Text style={styles.rowLabel}>{label(child.featureId, "label")}</Text>
                <Text style={styles.rowHint}>{label(child.featureId, "hint")}</Text>
              </View>
              <Text style={styles.openMark}>{t("premium:privateOffice.open")}</Text>
            </Pressable>
          ))}
        </View>
      ) : null}

      {office.unavailable.length ? (
        <View style={styles.section}>
          <Text style={styles.sectionTitle}>{t("premium:privateOffice.sections.notYet")}</Text>
          {office.unavailable.map((child) => (
            <View
              key={child.featureId}
              style={styles.rowClosed}
              accessibilityLabel={`${label(child.featureId, "label")} — ${t(
                `premium:privateOffice.reason.${child.reason}`
              )}`}
            >
              <Ionicons
                name={ICONS[child.featureId] || "ellipse-outline"}
                size={20}
                color={colors.disabled}
              />
              <View style={styles.rowBody}>
                <Text style={styles.rowLabelMuted}>{label(child.featureId, "label")}</Text>
                <Text style={styles.rowHint}>{label(child.featureId, "hint")}</Text>
              </View>
              <Text style={styles.stateMark}>{t(`premium:privateOffice.reason.${child.reason}`)}</Text>
            </View>
          ))}
        </View>
      ) : null}

      {loadState === "LOADED" && office.state !== "ENTRY_UNKNOWN" ? (
        <View style={styles.section}>
          <Text style={styles.sectionTitle}>{t("premium:privateOffice.sections.security")}</Text>
          <Pressable
            style={styles.rowOpen}
            onPress={() => navigation.navigate("PrivateOfficeSecurity" as never)}
            accessibilityRole="button"
            accessibilityLabel={t("premium:privateOffice.security.row.label")}
          >
            <Ionicons name="lock-closed-outline" size={20} color={colors.accent} />
            <View style={styles.rowBody}>
              <Text style={styles.rowLabel}>{t("premium:privateOffice.security.row.label")}</Text>
              <Text style={styles.rowHint}>{t("premium:privateOffice.security.row.hint")}</Text>
            </View>
            <Text style={styles.openMark}>{t("premium:privateOffice.open")}</Text>
          </Pressable>
        </View>
      ) : null}

      {loadState === "LOADED" ? (
        <Text style={styles.footnote}>{t("premium:privateOffice.footnote")}</Text>
      ) : null}
    </ScrollView>
  );
}

const styles = StyleSheet.create({
  root: { flex: 1, backgroundColor: colors.background },
  content: { padding: 18, gap: 16 },
  header: { gap: 6 },
  title: { color: colors.text, fontSize: 26, fontWeight: "800", letterSpacing: 1.2 },
  subtitle: { color: colors.muted, fontSize: 14, lineHeight: 20 },
  panel: {
    backgroundColor: colors.surface,
    borderColor: colors.border,
    borderWidth: 1,
    borderRadius: 16,
    padding: 18,
    gap: 8,
    alignItems: "flex-start"
  },
  panelTitle: { color: colors.text, fontSize: 15, fontWeight: "700" },
  panelText: { color: colors.muted, fontSize: 13, lineHeight: 19 },
  retry: {
    marginTop: 6,
    paddingHorizontal: 14,
    paddingVertical: 8,
    borderRadius: 999,
    backgroundColor: colors.surfaceRaised,
    borderColor: colors.border,
    borderWidth: 1
  },
  retryText: { color: colors.accentStrong, fontSize: 13, fontWeight: "700" },
  section: { gap: 10 },
  sectionTitle: {
    color: colors.muted,
    fontSize: 11,
    fontWeight: "800",
    letterSpacing: 1.4
  },
  rowOpen: {
    flexDirection: "row",
    alignItems: "center",
    gap: 12,
    backgroundColor: colors.surface,
    borderColor: colors.border,
    borderWidth: 1,
    borderRadius: 14,
    padding: 14
  },
  rowClosed: {
    flexDirection: "row",
    alignItems: "center",
    gap: 12,
    backgroundColor: "transparent",
    borderColor: colors.border,
    borderWidth: 1,
    borderRadius: 14,
    padding: 14,
    opacity: 0.7
  },
  rowBody: { flex: 1, gap: 2 },
  rowLabel: { color: colors.text, fontSize: 15, fontWeight: "700" },
  rowLabelMuted: { color: colors.muted, fontSize: 15, fontWeight: "700" },
  rowHint: { color: colors.muted, fontSize: 12, lineHeight: 17 },
  openMark: { color: colors.accent, fontSize: 12, fontWeight: "800", letterSpacing: 0.8 },
  stateMark: {
    color: colors.disabled,
    fontSize: 10,
    fontWeight: "800",
    letterSpacing: 0.8,
    maxWidth: 110,
    textAlign: "right"
  },
  footnote: { color: colors.muted, fontSize: 11, lineHeight: 16 }
});

export default PrivateOfficeScreen;
