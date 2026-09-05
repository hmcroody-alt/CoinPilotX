/**
 * Capital Graph — the member's holdings, coverage, structure and documents,
 * as the server delivered them.
 *
 * ## No totals, ever
 *
 * There is no aggregate monetary value on this screen because the server
 * deliberately sends none: it refuses to total an estate whose parts have
 * different truth states, and this client must not compute one either. The
 * counts strip counts *things* — three properties, two policies — never money.
 *
 * ## `complete` is read, never derived
 *
 * "3 properties" is only an honest sentence while the server says the view is
 * complete. Otherwise the copy switches to "3 so far". The flag comes down the
 * wire; nothing here infers it from list lengths.
 *
 * ## The states are not interchangeable
 *
 * Same discipline as Private Facts: READY/EMPTY, DENIED, NOT_ENTITLED,
 * FEATURE_DISABLED, NOT_IMPLEMENTED, UNAVAILABLE, LOCKED and ERROR are
 * different sentences. UNAVAILABLE, ERROR and DENIED must never be drawn as
 * EMPTY — "we could not look" or "we refused to answer" dressed as "you own
 * nothing" is exactly the confusion this surface exists to prevent. EMPTY is
 * READY with zero nodes, and only that.
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
  CAPITAL_VIEWS,
  CapitalGraphResult,
  CapitalView,
  asCapitalView,
  getCapitalGraph
} from "../api/capitalGraph";
import { useTranslation } from "../i18n";
import { BOTTOM_NAV_CONTENT_CLEARANCE } from "../navigation/BottomNavVisibility";
import { RootStackParamList } from "../navigation/types";
import { PrivateOfficeLockGate } from "../privateOffice/PrivateOfficeLockGate";
import { lockOfficeLocally } from "../privateOffice/officeLock";
import { colors } from "../theme/colors";

type Props = NativeStackScreenProps<RootStackParamList, "CapitalGraph">;

/**
 * The server's states plus the two the screen adds. LOADING is the window
 * before an answer exists; EMPTY is the server's READY with zero nodes,
 * promoted to its own word so it can never be written by the same branch that
 * writes UNAVAILABLE.
 */
type ScreenState = "LOADING" | "EMPTY" | CapitalGraphResult["state"];

export function CapitalGraphScreen(props: Props) {
  return (
    <PrivateOfficeLockGate
      onDismiss={() => props.navigation.goBack()}
      onRenew={() => props.navigation.navigate("Premium")}
    >
      <CapitalGraphBody {...props} />
    </PrivateOfficeLockGate>
  );
}

function CapitalGraphBody({ navigation, route }: Props) {
  const { t } = useTranslation();
  const insets = useSafeAreaInsets();
  const [view, setView] = useState<CapitalView>(asCapitalView(route.params?.view) ?? "holdings");
  const [state, setState] = useState<ScreenState>("LOADING");
  const [result, setResult] = useState<CapitalGraphResult | null>(null);
  const [refreshing, setRefreshing] = useState(false);

  const load = useCallback(async (wanted: CapitalView) => {
    const next = await getCapitalGraph(wanted);
    // The server said the grant is dead (revoked elsewhere, expired). Drop the
    // local token so the enclosing gate flips back to the unlock door.
    if (next.state === "LOCKED") lockOfficeLocally();
    setResult(next);
    setState(next.state === "READY" && next.graph.nodes.length === 0 ? "EMPTY" : next.state);
  }, []);

  useEffect(() => {
    let cancelled = false;
    setState("LOADING");
    (async () => {
      const next = await getCapitalGraph(view);
      if (cancelled) return;
      if (next.state === "LOCKED") lockOfficeLocally();
      setResult(next);
      setState(next.state === "READY" && next.graph.nodes.length === 0 ? "EMPTY" : next.state);
    })();
    return () => {
      cancelled = true;
    };
  }, [view]);

  const onRefresh = useCallback(async () => {
    setRefreshing(true);
    try {
      await load(view);
    } finally {
      setRefreshing(false);
    }
  }, [load, view]);

  const graph = result && result.state === "READY" ? result.graph : null;
  const minimumTier = result && result.state === "NOT_ENTITLED" ? result.minimumTier : "";
  const deniedReason = result && result.state === "DENIED" ? result.reason : "";

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

  const notice = (
    icon: keyof typeof Ionicons.glyphMap,
    tint: string,
    title: string,
    body: string,
    retry: boolean,
    caption?: string
  ) => (
    <View style={styles.panel}>
      <Ionicons name={icon} size={22} color={tint} />
      <Text style={styles.panelTitle}>{title}</Text>
      <Text style={styles.panelText}>{body}</Text>
      {caption ? <Text style={styles.panelCaption}>{caption}</Text> : null}
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
        <Text style={styles.title}>{t("premium:privateOffice.capital.title")}</Text>
        <Text style={styles.subtitle}>{t("premium:privateOffice.capital.subtitle")}</Text>
      </View>

      <ScrollView
        horizontal
        showsHorizontalScrollIndicator={false}
        contentContainerStyle={styles.chips}
      >
        {CAPITAL_VIEWS.map((candidate) => (
          <Pressable
            key={candidate}
            style={[styles.chip, candidate === view ? styles.chipActive : null]}
            onPress={() => setView(candidate)}
            accessibilityRole="button"
            accessibilityState={{ selected: candidate === view }}
          >
            <Text style={[styles.chipText, candidate === view ? styles.chipTextActive : null]}>
              {t(`premium:privateOffice.capital.views.${candidate}`)}
            </Text>
          </Pressable>
        ))}
      </ScrollView>

      {state === "LOADING" ? (
        <View style={styles.panel} accessibilityRole="progressbar">
          <ActivityIndicator color={colors.accent} />
          <Text style={styles.panelText}>{t("premium:privateOffice.capital.loading")}</Text>
        </View>
      ) : null}

      {state === "EMPTY"
        ? notice(
            "file-tray-outline",
            colors.muted,
            t("premium:privateOffice.capital.empty.title"),
            t(`premium:privateOffice.capital.emptyBody.${view}`),
            false
          )
        : null}

      {/* The headline is ours; the reason is the server's, shown verbatim in
          the caption because it was written for a person. */}
      {state === "DENIED"
        ? notice(
            "hand-left-outline",
            colors.warning,
            t("premium:privateOffice.capital.denied.title"),
            t("premium:privateOffice.capital.denied.body"),
            false,
            deniedReason || undefined
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

      {graph && state === "READY" ? (
        <>
          {/* Counts of things, never of money. `complete` gates the phrasing:
              exact counts only while the server says nothing was truncated. */}
          {Object.keys(graph.counted).length ? (
            <View style={styles.countStrip}>
              {Object.entries(graph.counted).map(([token, count]) => (
                <View key={token} style={styles.countCard}>
                  <Text style={styles.countValue}>
                    {graph.complete
                      ? t("premium:privateOffice.capital.countExact", { count })
                      : t("premium:privateOffice.capital.countSoFar", { count })}
                  </Text>
                  <Text style={styles.countLabel}>{nodeTypeLabel(token)}</Text>
                </View>
              ))}
            </View>
          ) : null}

          {graph.conflicts.length ? (
            <View style={styles.warnPanel}>
              <View style={styles.warnHead}>
                <Ionicons name="warning-outline" size={18} color={colors.warning} />
                <Text style={styles.warnTitle}>
                  {t("premium:privateOffice.capital.conflicts.title")}
                </Text>
              </View>
              {graph.conflicts.map((conflict) => (
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

          {graph.stale.length ? (
            <View style={styles.warnPanel}>
              <View style={styles.warnHead}>
                <Ionicons name="time-outline" size={18} color={colors.warning} />
                <Text style={styles.warnTitle}>
                  {t("premium:privateOffice.capital.stale.title")}
                </Text>
              </View>
              {graph.stale.map((flag) => (
                <View key={flag.factId} style={styles.staleRow}>
                  <Text style={styles.staleType}>{flag.factType}</Text>
                  {flag.ageDays !== null ? (
                    <Text style={styles.staleAge}>
                      {t("premium:privateOffice.capital.stale.age", { days: flag.ageDays })}
                    </Text>
                  ) : null}
                </View>
              ))}
            </View>
          ) : null}

          {/* Nodes render in the order the server delivered them. */}
          {graph.nodes.map((node) => (
            <Pressable
              key={node.id}
              style={styles.nodeRow}
              onPress={() => navigation.navigate("CapitalEntity", { id: node.id, view })}
              accessibilityRole="button"
              accessibilityLabel={node.externalRef || nodeTypeLabel(node.nodeType)}
            >
              <View style={styles.nodeHead}>
                <Text style={styles.nodeName}>
                  {node.externalRef || nodeTypeLabel(node.nodeType)}
                </Text>
                <Text style={[styles.truthMark, truthStyle(node.truth)]}>
                  {truthLabel(node.truth)}
                </Text>
              </View>
              <Text style={styles.nodeCaption}>
                {t("premium:privateOffice.capital.factCount", { count: node.factCount })}
              </Text>
            </Pressable>
          ))}
        </>
      ) : null}
    </ScrollView>
  );
}

const styles = StyleSheet.create({
  root: { flex: 1, backgroundColor: colors.background },
  content: { padding: 18, gap: 16 },
  header: { gap: 6 },
  title: { color: colors.text, fontSize: 24, fontWeight: "800" },
  subtitle: { color: colors.muted, fontSize: 13, lineHeight: 19 },
  chips: { gap: 8, paddingVertical: 2 },
  chip: {
    paddingHorizontal: 13,
    paddingVertical: 7,
    borderRadius: 999,
    backgroundColor: colors.surface,
    borderColor: colors.border,
    borderWidth: 1
  },
  chipActive: { backgroundColor: colors.surfaceRaised, borderColor: colors.accentStrong },
  chipText: { color: colors.muted, fontSize: 12, fontWeight: "700" },
  chipTextActive: { color: colors.accentStrong },
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
  panelCaption: { color: colors.muted, fontSize: 11, lineHeight: 16, fontStyle: "italic" },
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
  countStrip: { flexDirection: "row", flexWrap: "wrap", gap: 8 },
  countCard: {
    backgroundColor: colors.surface,
    borderColor: colors.border,
    borderWidth: 1,
    borderRadius: 12,
    paddingHorizontal: 13,
    paddingVertical: 9,
    gap: 2,
    alignItems: "flex-start"
  },
  countValue: { color: colors.text, fontSize: 15, fontWeight: "800" },
  countLabel: { color: colors.muted, fontSize: 10, fontWeight: "800", letterSpacing: 0.8 },
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
  conflictMeta: { color: colors.muted, fontSize: 11 },
  staleRow: {
    flexDirection: "row",
    justifyContent: "space-between",
    alignItems: "center",
    gap: 10
  },
  staleType: { color: colors.text, fontSize: 12, fontWeight: "700", flexShrink: 1 },
  staleAge: { color: colors.warning, fontSize: 11, fontWeight: "700" },
  nodeRow: {
    backgroundColor: colors.surface,
    borderColor: colors.border,
    borderWidth: 1,
    borderRadius: 14,
    padding: 14,
    gap: 6
  },
  nodeHead: {
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "space-between",
    gap: 10
  },
  nodeName: { color: colors.text, fontSize: 15, fontWeight: "700", flexShrink: 1 },
  truthMark: { color: colors.muted, fontSize: 10, fontWeight: "800", letterSpacing: 0.8 },
  truthDanger: { color: colors.danger },
  truthWarning: { color: colors.warning },
  nodeCaption: { color: colors.muted, fontSize: 11 }
});

export default CapitalGraphScreen;
