/**
 * Capital Entity — one node of the capital graph and its immediate
 * neighbourhood.
 *
 * ## Two reads, two fates
 *
 * The entity and its relationships are separate server calls, loaded in
 * parallel. If the relationships call refuses while the entity call succeeds,
 * the entity still renders and the relationships section says it could not be
 * shown — blanking the whole screen would punish the member for the failure of
 * the half they were not looking at.
 *
 * ## Same rules as the graph screen
 *
 * No monetary totals anywhere; counts are of things. UNAVAILABLE and ERROR are
 * never drawn as an empty section. NOT_FOUND is one deliberate word for
 * "absent, someone else's, or out of view" — the server refuses to distinguish
 * them and so does this screen.
 */

import { NativeStackScreenProps } from "@react-navigation/native-stack";
import { useCallback, useEffect, useState } from "react";
import {
  ActivityIndicator,
  Pressable,
  RefreshControl,
  ScrollView,
  StyleSheet,
  Text,
  View
} from "react-native";
import { Ionicons } from "@expo/vector-icons";
import { useSafeAreaInsets } from "react-native-safe-area-context";
import {
  CapitalEntityResult,
  CapitalRelationshipsResult,
  CapitalView,
  asCapitalView,
  getCapitalEntity,
  getCapitalRelationships
} from "../api/capitalGraph";
import { useTranslation } from "../i18n";
import { BOTTOM_NAV_CONTENT_CLEARANCE } from "../navigation/BottomNavVisibility";
import { RootStackParamList } from "../navigation/types";
import { PrivateOfficeLockGate } from "../privateOffice/PrivateOfficeLockGate";
import { lockOfficeLocally } from "../privateOffice/officeLock";
import { colors } from "../theme/colors";

type Props = NativeStackScreenProps<RootStackParamList, "CapitalEntity">;

type ScreenState = "LOADING" | CapitalEntityResult["state"];

/** "INSURANCE_POLICY" -> "Insurance Policy". Display only. */
function humanize(token: string): string {
  return token
    .toLowerCase()
    .split(/_+/)
    .filter(Boolean)
    .map((word) => word.charAt(0).toUpperCase() + word.slice(1))
    .join(" ");
}

/** A date-time the server sent, shortened for a row. Display only. */
function shortDate(value: string): string {
  return value.length >= 10 ? value.slice(0, 10) : value;
}

export function CapitalEntityScreen(props: Props) {
  return (
    <PrivateOfficeLockGate
      onDismiss={() => props.navigation.goBack()}
      onRenew={() => props.navigation.navigate("Premium")}
    >
      <CapitalEntityBody {...props} />
    </PrivateOfficeLockGate>
  );
}

function CapitalEntityBody({ navigation, route }: Props) {
  const { t } = useTranslation();
  const insets = useSafeAreaInsets();
  const id = route.params.id;
  const view: CapitalView = asCapitalView(route.params.view) ?? "holdings";
  const [state, setState] = useState<ScreenState>("LOADING");
  const [result, setResult] = useState<CapitalEntityResult | null>(null);
  const [relations, setRelations] = useState<CapitalRelationshipsResult | null>(null);
  const [refreshing, setRefreshing] = useState(false);

  const load = useCallback(async () => {
    const [entityNext, relationsNext] = await Promise.all([
      getCapitalEntity(id, view),
      getCapitalRelationships(id, view)
    ]);
    // A dead grant reported by either call locks the office locally so the
    // enclosing gate flips back to the unlock door.
    if (entityNext.state === "LOCKED" || relationsNext.state === "LOCKED") lockOfficeLocally();
    setResult(entityNext);
    setRelations(relationsNext);
    setState(entityNext.state);
  }, [id, view]);

  useEffect(() => {
    let cancelled = false;
    setState("LOADING");
    (async () => {
      const [entityNext, relationsNext] = await Promise.all([
        getCapitalEntity(id, view),
        getCapitalRelationships(id, view)
      ]);
      if (cancelled) return;
      if (entityNext.state === "LOCKED" || relationsNext.state === "LOCKED") lockOfficeLocally();
      setResult(entityNext);
      setRelations(relationsNext);
      setState(entityNext.state);
    })();
    return () => {
      cancelled = true;
    };
  }, [id, view]);

  const onRefresh = useCallback(async () => {
    setRefreshing(true);
    try {
      await load();
    } finally {
      setRefreshing(false);
    }
  }, [load]);

  const ready = result && result.state === "READY" ? result : null;
  const minimumTier = result && result.state === "NOT_ENTITLED" ? result.minimumTier : "";
  const relationRows = relations && relations.state === "READY" ? relations.relationships : [];

  const nodeTypeLabel = (token: string) =>
    t(`premium:privateOffice.capital.nodeType.${token}`, { defaultValue: token });

  const truthLabel = (token: string) =>
    t(`premium:privateOffice.capital.truth.${token}`, { defaultValue: token });

  const truthStyle = (truth: string) =>
    truth === "CONFLICTING" || truth === "MISSING"
      ? styles.truthDanger
      : truth === "STALE" || truth === "ESTIMATED"
        ? styles.truthWarning
        : null;

  const relationLabel = (token: string) =>
    t(`premium:privateOffice.capital.relation.${token}`, { defaultValue: humanize(token) });

  const notice = (
    icon: keyof typeof Ionicons.glyphMap,
    tint: string,
    title: string,
    body: string,
    retry: boolean
  ) => (
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
      {state === "LOADING" ? (
        <View style={styles.panel} accessibilityRole="progressbar">
          <ActivityIndicator color={colors.accent} />
          <Text style={styles.panelText}>{t("premium:privateOffice.capital.loading")}</Text>
        </View>
      ) : null}

      {state === "NOT_FOUND"
        ? notice(
            "help-circle-outline",
            colors.muted,
            t("premium:privateOffice.capital.notFound.title"),
            t("premium:privateOffice.capital.notFound.body"),
            false
          )
        : null}

      {state === "NOT_ENTITLED"
        ? notice(
            "lock-closed-outline",
            colors.warning,
            t("premium:privateOffice.capital.notEntitled.title"),
            minimumTier
              ? t("premium:privateOffice.capital.notEntitled.body", { tier: minimumTier })
              : t("premium:privateOffice.capital.notEntitled.bodyGeneric"),
            false
          )
        : null}

      {state === "FEATURE_DISABLED"
        ? notice(
            "pause-circle-outline",
            colors.warning,
            t("premium:privateOffice.capital.disabled.title"),
            t("premium:privateOffice.capital.disabled.body"),
            true
          )
        : null}

      {state === "NOT_IMPLEMENTED"
        ? notice(
            "construct-outline",
            colors.muted,
            t("premium:privateOffice.capital.notImplemented.title"),
            t("premium:privateOffice.capital.notImplemented.body"),
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
            t("premium:privateOffice.capital.unavailable.title"),
            t("premium:privateOffice.capital.unavailable.body"),
            true
          )
        : null}

      {state === "ERROR"
        ? notice(
            "alert-circle-outline",
            colors.danger,
            t("premium:privateOffice.capital.error.title"),
            t("premium:privateOffice.capital.error.body"),
            true
          )
        : null}

      {ready && state === "READY" ? (
        <>
          <View style={styles.entityCard}>
            <View style={styles.entityHead}>
              <Text style={styles.entityName}>
                {ready.entity.externalRef || nodeTypeLabel(ready.entity.nodeType)}
              </Text>
              <Text style={[styles.truthMark, truthStyle(ready.entity.truth)]}>
                {truthLabel(ready.entity.truth)}
              </Text>
            </View>
            <Text style={styles.entityKind}>{nodeTypeLabel(ready.entity.nodeType)}</Text>
            <View style={styles.entityMeta}>
              {ready.entity.domain ? (
                <Text style={styles.metaText}>{ready.entity.domain}</Text>
              ) : null}
              {ready.entity.lifecycleState ? (
                <Text style={styles.metaText}>{humanize(ready.entity.lifecycleState)}</Text>
              ) : null}
              {ready.entity.updatedAt ? (
                <Text style={styles.metaText}>
                  {t("premium:privateOffice.capital.entity.updated", {
                    date: shortDate(ready.entity.updatedAt)
                  })}
                </Text>
              ) : null}
            </View>
          </View>

          <View style={styles.section}>
            <Text style={styles.sectionTitle}>
              {t("premium:privateOffice.capital.entity.relationships")}
            </Text>
            {/* The relationships call has its own fate. A refusal here must
                not be drawn as "no relationships": the honest render is a note
                that the section could not be shown. */}
            {relations && relations.state !== "READY" ? (
              <Text style={styles.sectionNote}>
                {t("premium:privateOffice.capital.relationshipsUnavailable")}
              </Text>
            ) : null}
            {relationRows.map((relationship) => (
              <Pressable
                key={relationship.id}
                style={styles.relationRow}
                onPress={() =>
                  navigation.push("CapitalEntity", { id: relationship.other.id, view })
                }
                accessibilityRole="button"
                accessibilityLabel={
                  relationship.other.externalRef || nodeTypeLabel(relationship.other.nodeType)
                }
              >
                <Text style={styles.relationText}>
                  {relationship.direction === "out"
                    ? `${relationLabel(relationship.relationType)} → ${
                        relationship.other.externalRef ||
                        nodeTypeLabel(relationship.other.nodeType)
                      }`
                    : `← ${relationLabel(relationship.relationType)} ${
                        relationship.other.externalRef ||
                        nodeTypeLabel(relationship.other.nodeType)
                      }`}
                </Text>
                <Text style={[styles.truthMark, truthStyle(relationship.other.truth)]}>
                  {truthLabel(relationship.other.truth)}
                </Text>
              </Pressable>
            ))}
          </View>

          {ready.graph.facts.length ? (
            <View style={styles.section}>
              <Text style={styles.sectionTitle}>
                {t("premium:privateOffice.capital.entity.facts")}
              </Text>
              {ready.graph.facts.map((fact) => (
                <View key={fact.id} style={styles.factRow}>
                  <View style={styles.factHead}>
                    <Text style={styles.factType}>{humanize(fact.factType)}</Text>
                    {fact.freshness.stale ? (
                      <Text style={styles.staleMark}>
                        {t("premium:privateOffice.facts.stale")}
                      </Text>
                    ) : null}
                  </View>
                  <Text style={styles.factValue}>{fact.value}</Text>
                  <Text style={styles.metaText}>
                    {t(`premium:privateOffice.verification.${fact.provenance.verification}`, {
                      defaultValue: fact.provenance.verification
                    })}
                  </Text>
                </View>
              ))}
            </View>
          ) : null}

          {ready.graph.conflicts.length ? (
            <View style={styles.warnPanel}>
              <View style={styles.warnHead}>
                <Ionicons name="warning-outline" size={18} color={colors.warning} />
                <Text style={styles.warnTitle}>
                  {t("premium:privateOffice.capital.conflicts.title")}
                </Text>
              </View>
              {ready.graph.conflicts.map((conflict) => (
                <View key={conflict.conflictId} style={styles.conflictRow}>
                  <Text style={styles.conflictType}>{conflict.factType}</Text>
                  {conflict.reason ? (
                    <Text style={styles.conflictReason}>{conflict.reason}</Text>
                  ) : null}
                  <Text style={styles.conflictDisagree}>
                    {t("premium:privateOffice.capital.conflicts.disagree")}
                  </Text>
                  {conflict.competing.map((side) => (
                    <View key={side.factId} style={styles.conflictSide}>
                      <Text style={styles.conflictValue}>{side.value}</Text>
                      <Text style={styles.conflictMeta}>
                        {t(`premium:privateOffice.verification.${side.verification}`, {
                          defaultValue: side.verification
                        })}
                      </Text>
                    </View>
                  ))}
                </View>
              ))}
            </View>
          ) : null}
        </>
      ) : null}
    </ScrollView>
  );
}

const styles = StyleSheet.create({
  root: { flex: 1, backgroundColor: colors.background },
  content: { padding: 18, gap: 16 },
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
  entityCard: {
    backgroundColor: colors.surface,
    borderColor: colors.border,
    borderWidth: 1,
    borderRadius: 16,
    padding: 18,
    gap: 6
  },
  entityHead: {
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "space-between",
    gap: 10
  },
  entityName: { color: colors.text, fontSize: 20, fontWeight: "800", flexShrink: 1 },
  entityKind: { color: colors.muted, fontSize: 11, fontWeight: "800", letterSpacing: 1 },
  entityMeta: { flexDirection: "row", flexWrap: "wrap", gap: 12 },
  metaText: { color: colors.muted, fontSize: 11 },
  truthMark: { color: colors.muted, fontSize: 10, fontWeight: "800", letterSpacing: 0.8 },
  truthDanger: { color: colors.danger },
  truthWarning: { color: colors.warning },
  section: { gap: 10 },
  sectionTitle: { color: colors.muted, fontSize: 11, fontWeight: "800", letterSpacing: 1.4 },
  sectionNote: { color: colors.muted, fontSize: 12, lineHeight: 17, fontStyle: "italic" },
  relationRow: {
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "space-between",
    gap: 10,
    backgroundColor: colors.surface,
    borderColor: colors.border,
    borderWidth: 1,
    borderRadius: 14,
    padding: 14
  },
  relationText: { color: colors.text, fontSize: 13, fontWeight: "600", flexShrink: 1 },
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
  warnPanel: {
    backgroundColor: colors.surface,
    borderColor: colors.warning,
    borderWidth: 1,
    borderRadius: 14,
    padding: 14,
    gap: 10
  },
  warnHead: { flexDirection: "row", alignItems: "center", gap: 6 },
  warnTitle: { color: colors.warning, fontSize: 13, fontWeight: "800" },
  conflictRow: { gap: 4 },
  conflictType: { color: colors.text, fontSize: 12, fontWeight: "800", letterSpacing: 0.8 },
  conflictReason: { color: colors.muted, fontSize: 12, lineHeight: 17 },
  conflictDisagree: { color: colors.warning, fontSize: 11, fontWeight: "700" },
  conflictSide: {
    flexDirection: "row",
    justifyContent: "space-between",
    alignItems: "center",
    gap: 10,
    paddingLeft: 10,
    borderLeftWidth: 2,
    borderLeftColor: colors.border
  },
  conflictValue: { color: colors.text, fontSize: 13, fontWeight: "600", flexShrink: 1 },
  conflictMeta: { color: colors.muted, fontSize: 11 }
});

export default CapitalEntityScreen;
