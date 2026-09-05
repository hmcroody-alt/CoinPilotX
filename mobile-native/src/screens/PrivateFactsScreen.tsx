/**
 * Private Facts — what PulseSoc holds about you, and why it believes it.
 *
 * ## Every row on this screen came from the server
 *
 * The list is `GET /api/private-office/facts`, owner-scoped in SQL rather than
 * filtered after the fact, projected by `office.project_facts`. There is no
 * seed data, no example row and no placeholder: an empty store renders the
 * empty state, because a fabricated row in a screen whose whole promise is
 * "this is what we actually know" would poison the one thing it exists to do.
 *
 * ## The states are not interchangeable
 *
 * Six outcomes, deliberately kept apart:
 *
 *   READY / EMPTY      we looked, and here is what was there (or was not).
 *   NOT_ENTITLED       the capability exists and your plan does not include it.
 *   FEATURE_DISABLED   built, switched off right now. Not a plan problem.
 *   NOT_IMPLEMENTED    there is nothing to show and nothing to sell.
 *   UNAVAILABLE        we could not look. This is the important one.
 *   ERROR              the request itself failed.
 *
 * UNAVAILABLE must never be drawn as EMPTY. "We looked and found nothing" and
 * "we could not look" are different claims, and on a screen about personal
 * records the second one dressed as the first is how a member concludes that a
 * document they filed was lost.
 *
 * ## Why every fact carries a provenance affordance
 *
 * A number with no source is a rumour with good typography. Each row opens a
 * sheet naming the source *type*, when it was observed, the verification state
 * and the confidence. It does not show the internal locator — that is a pointer
 * into private storage, not an explanation — and it does not dump the raw
 * record.
 */

import { NativeStackScreenProps } from "@react-navigation/native-stack";
import { useCallback, useEffect, useMemo, useState } from "react";
import {
  ActivityIndicator,
  Modal,
  Pressable,
  RefreshControl,
  ScrollView,
  StyleSheet,
  Text,
  View
} from "react-native";
import { Ionicons } from "@expo/vector-icons";
import { useSafeAreaInsets } from "react-native-safe-area-context";
import { PrivateFact, PrivateFactsResult, getPrivateFacts } from "../api/privateOffice";
import { useTranslation } from "../i18n";
import { BOTTOM_NAV_CONTENT_CLEARANCE } from "../navigation/BottomNavVisibility";
import { RootStackParamList } from "../navigation/types";
import { PrivateOfficeLockGate } from "../privateOffice/PrivateOfficeLockGate";
import { lockOfficeLocally } from "../privateOffice/officeLock";
import { colors } from "../theme/colors";

type Props = NativeStackScreenProps<RootStackParamList, "PrivateFacts">;

/**
 * The server's six states plus the two the screen adds.
 *
 * LOADING and EMPTY have no server equivalent on purpose. LOADING is the window
 * before an answer exists. EMPTY is the server's READY with nothing in it —
 * promoted to its own word here precisely so it can never be written by the
 * same branch that writes UNAVAILABLE.
 */
type ScreenState = "LOADING" | "EMPTY" | PrivateFactsResult["state"];

type DomainGroup = { domain: string; facts: PrivateFact[] };

/**
 * Group by the domain each row declares.
 *
 * The headings come from the data, in the order the server returned the rows.
 * A local list of the seven domains would be a second copy of a vocabulary that
 * lives in `services/private_office/model.py`, and it would go stale the first
 * time one is added.
 */
function groupByDomain(facts: PrivateFact[]): DomainGroup[] {
  const groups: DomainGroup[] = [];
  const index = new Map<string, DomainGroup>();
  facts.forEach((fact) => {
    const domain = fact.domain || "";
    let group = index.get(domain);
    if (!group) {
      group = { domain, facts: [] };
      index.set(domain, group);
      groups.push(group);
    }
    group.facts.push(fact);
  });
  return groups;
}

/**
 * Gated shell (Stage 19): a deep link to `pulse/private-office/facts` renders
 * the lock door here and the facts list resumes after unlock. The LOCKED branch
 * below still exists because a grant can die mid-session (relock elsewhere,
 * Stage 12's revoke-everywhere) between the gate's check and this screen's
 * fetch — the honest render for that race is the lock message with a retry.
 */
export function PrivateFactsScreen(props: Props) {
  return (
    <PrivateOfficeLockGate
      onDismiss={() => props.navigation.goBack()}
      onRenew={() => props.navigation.navigate("Premium")}
    >
      <PrivateFactsBody {...props} />
    </PrivateOfficeLockGate>
  );
}

function PrivateFactsBody(_props: Props) {
  const { t } = useTranslation();
  const insets = useSafeAreaInsets();
  const [state, setState] = useState<ScreenState>("LOADING");
  const [result, setResult] = useState<PrivateFactsResult | null>(null);
  const [refreshing, setRefreshing] = useState(false);
  const [inspecting, setInspecting] = useState<PrivateFact | null>(null);

  const load = useCallback(async () => {
    const next = await getPrivateFacts();
    // The server said the grant is dead (revoked elsewhere, expired). Drop the
    // local token so the enclosing gate flips back to the unlock door instead
    // of this body arguing with the server.
    if (next.state === "LOCKED") lockOfficeLocally();
    setResult(next);
    // EMPTY is a distinct screen state but not a distinct server state: the
    // server answered READY with nothing in it, which is a real answer and must
    // not be confused with the refusals above it.
    setState(next.state === "READY" && next.facts.length === 0 ? "EMPTY" : next.state);
  }, []);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      const next = await getPrivateFacts();
      if (cancelled) return;
      if (next.state === "LOCKED") lockOfficeLocally();
      setResult(next);
      setState(next.state === "READY" && next.facts.length === 0 ? "EMPTY" : next.state);
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

  const groups = useMemo(
    () => (result && result.state === "READY" ? groupByDomain(result.facts) : []),
    [result]
  );

  const minimumTier = result && result.state === "NOT_ENTITLED" ? result.minimumTier : "";

  const notice = (icon: keyof typeof Ionicons.glyphMap, tint: string, title: string, body: string, retry: boolean) => (
    <View style={styles.panel}>
      <Ionicons name={icon} size={22} color={tint} />
      <Text style={styles.panelTitle}>{title}</Text>
      <Text style={styles.panelText}>{body}</Text>
      {retry ? (
        <Pressable style={styles.retry} onPress={onRefresh} accessibilityRole="button">
          <Text style={styles.retryText}>{t("premium:privateOffice.retry")}</Text>
        </Pressable>
      ) : null}
    </View>
  );

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
        <Text style={styles.title}>{t("premium:privateOffice.features.privateFacts.label")}</Text>
        <Text style={styles.subtitle}>{t("premium:privateOffice.facts.subtitle")}</Text>
      </View>

      {state === "LOADING" ? (
        <View style={styles.panel} accessibilityRole="progressbar">
          <ActivityIndicator color={colors.accent} />
          <Text style={styles.panelText}>{t("premium:privateOffice.facts.loading")}</Text>
        </View>
      ) : null}

      {state === "EMPTY"
        ? notice(
            "file-tray-outline",
            colors.muted,
            t("premium:privateOffice.facts.empty.title"),
            t("premium:privateOffice.facts.empty.body"),
            false
          )
        : null}

      {state === "NOT_ENTITLED"
        ? notice(
            "lock-closed-outline",
            colors.warning,
            t("premium:privateOffice.facts.notEntitled.title"),
            minimumTier
              ? t("premium:privateOffice.facts.notEntitled.body", { tier: minimumTier })
              : t("premium:privateOffice.facts.notEntitled.bodyGeneric"),
            false
          )
        : null}

      {state === "FEATURE_DISABLED"
        ? notice(
            "pause-circle-outline",
            colors.warning,
            t("premium:privateOffice.facts.disabled.title"),
            t("premium:privateOffice.facts.disabled.body"),
            true
          )
        : null}

      {state === "NOT_IMPLEMENTED"
        ? notice(
            "construct-outline",
            colors.muted,
            t("premium:privateOffice.facts.notImplemented.title"),
            t("premium:privateOffice.facts.notImplemented.body"),
            false
          )
        : null}

      {state === "LOCKED"
        ? notice(
            "lock-closed-outline",
            colors.accent,
            t("premium:privateOffice.lock.locked.title"),
            t("premium:privateOffice.lock.locked.body"),
            true
          )
        : null}

      {state === "UNAVAILABLE"
        ? notice(
            "cloud-offline-outline",
            colors.warning,
            t("premium:privateOffice.facts.unavailable.title"),
            t("premium:privateOffice.facts.unavailable.body"),
            true
          )
        : null}

      {state === "ERROR"
        ? notice(
            "alert-circle-outline",
            colors.danger,
            t("premium:privateOffice.facts.error.title"),
            t("premium:privateOffice.facts.error.body"),
            true
          )
        : null}

      {state === "READY"
        ? groups.map((group) => (
            <View key={group.domain} style={styles.section}>
              <Text style={styles.sectionTitle}>
                {t(`premium:privateOffice.domains.${group.domain}`)}
              </Text>
              {group.facts.map((fact) => (
                <View key={fact.id} style={styles.factRow}>
                  <View style={styles.factHead}>
                    <Text style={styles.factType}>{fact.factType}</Text>
                    {fact.freshness.stale ? (
                      <Text style={styles.staleMark}>{t("premium:privateOffice.facts.stale")}</Text>
                    ) : null}
                  </View>
                  <Text style={styles.factValue}>{fact.value}</Text>
                  <View style={styles.factMeta}>
                    <Text style={styles.metaText}>
                      {t(`premium:privateOffice.verification.${fact.provenance.verification}`, {
                        defaultValue: fact.provenance.verification
                      })}
                    </Text>
                    {fact.observedAt ? (
                      <Text style={styles.metaText}>
                        {t("premium:privateOffice.facts.observed", { date: fact.observedAt })}
                      </Text>
                    ) : null}
                  </View>
                  <Pressable
                    style={styles.whyButton}
                    onPress={() => setInspecting(fact)}
                    accessibilityRole="button"
                  >
                    <Ionicons name="help-circle-outline" size={15} color={colors.accentStrong} />
                    <Text style={styles.whyText}>{t("premium:privateOffice.facts.why")}</Text>
                  </Pressable>
                </View>
              ))}
            </View>
          ))
        : null}

      <Modal
        visible={inspecting !== null}
        transparent
        animationType="slide"
        onRequestClose={() => setInspecting(null)}
      >
        <Pressable style={styles.sheetBackdrop} onPress={() => setInspecting(null)}>
          <Pressable style={styles.sheet} onPress={() => undefined}>
            <Text style={styles.sheetTitle}>{t("premium:privateOffice.facts.why")}</Text>
            {inspecting ? (
              <View style={styles.sheetBody}>
                <Text style={styles.sheetValue}>{inspecting.value}</Text>
                <SheetLine
                  label={t("premium:privateOffice.facts.source")}
                  value={
                    inspecting.provenance.sourceType ||
                    t("premium:privateOffice.facts.sourceUnknown")
                  }
                />
                <SheetLine
                  label={t("premium:privateOffice.facts.verified")}
                  value={t(
                    `premium:privateOffice.verification.${inspecting.provenance.verification}`,
                    { defaultValue: inspecting.provenance.verification }
                  )}
                />
                <SheetLine
                  label={t("premium:privateOffice.facts.observedLabel")}
                  value={
                    inspecting.provenance.observedAt ||
                    inspecting.observedAt ||
                    t("premium:privateOffice.facts.sourceUnknown")
                  }
                />
                <SheetLine
                  label={t("premium:privateOffice.facts.confidence")}
                  value={`${Math.round(inspecting.provenance.confidence * 100)}%`}
                />
                {inspecting.provenance.hasSourceDocument ? (
                  <Text style={styles.sheetNote}>
                    {t("premium:privateOffice.facts.hasDocument")}
                  </Text>
                ) : null}
              </View>
            ) : null}
            <Pressable
              style={styles.retry}
              onPress={() => setInspecting(null)}
              accessibilityRole="button"
            >
              <Text style={styles.retryText}>{t("premium:privateOffice.facts.close")}</Text>
            </Pressable>
          </Pressable>
        </Pressable>
      </Modal>
    </ScrollView>
  );
}

function SheetLine({ label, value }: { label: string; value: string }) {
  return (
    <View style={styles.sheetLine}>
      <Text style={styles.sheetLabel}>{label}</Text>
      <Text style={styles.sheetLineValue}>{value}</Text>
    </View>
  );
}

const styles = StyleSheet.create({
  root: { flex: 1, backgroundColor: colors.background },
  content: { padding: 18, gap: 16 },
  header: { gap: 6 },
  title: { color: colors.text, fontSize: 24, fontWeight: "800" },
  subtitle: { color: colors.muted, fontSize: 13, lineHeight: 19 },
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
  sectionTitle: { color: colors.muted, fontSize: 11, fontWeight: "800", letterSpacing: 1.4 },
  factRow: {
    backgroundColor: colors.surface,
    borderColor: colors.border,
    borderWidth: 1,
    borderRadius: 14,
    padding: 14,
    gap: 6
  },
  factHead: { flexDirection: "row", alignItems: "center", justifyContent: "space-between" },
  factType: { color: colors.muted, fontSize: 11, fontWeight: "800", letterSpacing: 1 },
  staleMark: { color: colors.warning, fontSize: 10, fontWeight: "800", letterSpacing: 0.8 },
  factValue: { color: colors.text, fontSize: 16, fontWeight: "700" },
  factMeta: { flexDirection: "row", flexWrap: "wrap", gap: 12 },
  metaText: { color: colors.muted, fontSize: 11 },
  whyButton: { flexDirection: "row", alignItems: "center", gap: 5, marginTop: 2 },
  whyText: { color: colors.accentStrong, fontSize: 12, fontWeight: "700" },
  sheetBackdrop: { flex: 1, backgroundColor: "rgba(0,0,0,0.6)", justifyContent: "flex-end" },
  sheet: {
    backgroundColor: colors.surfaceRaised,
    borderTopLeftRadius: 20,
    borderTopRightRadius: 20,
    padding: 20,
    gap: 12
  },
  sheetTitle: { color: colors.text, fontSize: 16, fontWeight: "800" },
  sheetBody: { gap: 8 },
  sheetValue: { color: colors.text, fontSize: 15, fontWeight: "700" },
  sheetLine: { flexDirection: "row", justifyContent: "space-between", gap: 12 },
  sheetLabel: { color: colors.muted, fontSize: 12 },
  sheetLineValue: { color: colors.text, fontSize: 12, fontWeight: "600", flexShrink: 1, textAlign: "right" },
  sheetNote: { color: colors.muted, fontSize: 11, lineHeight: 16 }
});

export default PrivateFactsScreen;
