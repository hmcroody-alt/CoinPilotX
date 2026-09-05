/**
 * Private Shield — the member's security posture, told honestly.
 *
 * Two truths this screen must not blur. First: the scan is internal — it
 * checks the named list in `posture.checks` and nothing else, so "no
 * findings" means "none in what we looked at", never "you are safe". Second:
 * the `external` block says what no outside provider has checked; its notes
 * are rendered verbatim and never summarized into reassurance. The person
 * most likely to read this screen is the person who paid for protection —
 * they get the exact scope of it.
 */

import { NativeStackScreenProps } from "@react-navigation/native-stack";
import { useCallback, useEffect, useState } from "react";
import {
  ActivityIndicator,
  Alert,
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
  ShieldFinding,
  ShieldHomeResult,
  getShieldHome,
  runShieldScan,
  setShieldFindingStatus
} from "../api/privateFeatures";
import { useTranslation } from "../i18n";
import { BOTTOM_NAV_CONTENT_CLEARANCE } from "../navigation/BottomNavVisibility";
import { RootStackParamList } from "../navigation/types";
import {
  FeatureEmptyPanel,
  FeatureLoadingPanel,
  FeatureRefusalPanel
} from "../privateOffice/FeatureStatePanels";
import { PrivateOfficeLockGate } from "../privateOffice/PrivateOfficeLockGate";
import { lockOfficeLocally } from "../privateOffice/officeLock";
import { colors } from "../theme/colors";

type Props = NativeStackScreenProps<RootStackParamList, "PrivateShield">;

const SEVERITY_COLORS: Record<string, string> = {
  HIGH: colors.danger,
  MEDIUM: colors.warning,
  LOW: colors.muted
};

export function PrivateShieldScreen(props: Props) {
  return (
    <PrivateOfficeLockGate
      onDismiss={() => props.navigation.goBack()}
      onRenew={() => props.navigation.navigate("Premium")}
    >
      <PrivateShieldBody {...props} />
    </PrivateOfficeLockGate>
  );
}

function PrivateShieldBody(_props: Props) {
  const { t } = useTranslation();
  const insets = useSafeAreaInsets();
  const [result, setResult] = useState<ShieldHomeResult | null>(null);
  const [refreshing, setRefreshing] = useState(false);
  const [scanning, setScanning] = useState(false);
  const [busyFindingId, setBusyFindingId] = useState(0);

  const load = useCallback(async () => {
    const next = await getShieldHome();
    if (next.state === "LOCKED") lockOfficeLocally();
    setResult(next);
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  const onRefresh = useCallback(async () => {
    setRefreshing(true);
    try {
      await load();
    } finally {
      setRefreshing(false);
    }
  }, [load]);

  const scan = useCallback(async () => {
    setScanning(true);
    try {
      const scanned = await runShieldScan();
      if (scanned.state === "LOCKED") {
        lockOfficeLocally();
        return;
      }
      if (scanned.state === "SCANNED") {
        await load();
        return;
      }
      Alert.alert(
        t("premium:privateOffice.shield.scanFailed"),
        t("premium:privateOffice.feature.error.body")
      );
    } finally {
      setScanning(false);
    }
  }, [load, t]);

  const updateFinding = useCallback(
    async (findingId: number, status: "ACKNOWLEDGED" | "RESOLVED" | "DISMISSED") => {
      setBusyFindingId(findingId);
      try {
        const written = await setShieldFindingStatus(findingId, status);
        if (written.state === "LOCKED") {
          lockOfficeLocally();
          return;
        }
        if (written.state === "OK") {
          await load();
          return;
        }
        Alert.alert(
          t("premium:privateOffice.shield.updateFailed"),
          written.state === "REJECTED" && written.message
            ? written.message
            : t("premium:privateOffice.feature.error.body")
        );
      } finally {
        setBusyFindingId(0);
      }
    },
    [load, t]
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
        <Text style={styles.title}>{t("premium:privateOffice.features.privateShield.label")}</Text>
        <Text style={styles.subtitle}>{t("premium:privateOffice.shield.subtitle")}</Text>
      </View>

      {result === null ? <FeatureLoadingPanel /> : null}

      {result && result.state === "READY" ? (
        <>
          <View style={styles.posture}>
            <View style={styles.postureHead}>
              <Ionicons name="shield-half-outline" size={22} color={colors.accent} />
              <Text style={styles.postureCount}>
                {t("premium:privateOffice.shield.openFindings", {
                  count: result.posture.openFindings
                })}
              </Text>
            </View>
            {Object.entries(result.posture.bySeverity)
              .filter(([, count]) => count > 0)
              .map(([severity, count]) => (
                <View key={severity} style={styles.severityRow}>
                  <View
                    style={[
                      styles.severityDot,
                      { backgroundColor: SEVERITY_COLORS[severity] ?? colors.muted }
                    ]}
                  />
                  <Text style={styles.severityText}>
                    {t(`premium:privateOffice.shield.severity.${severity}`, {
                      defaultValue: severity
                    })}
                    {"  ·  "}
                    {count}
                  </Text>
                </View>
              ))}
            <Pressable
              style={styles.primary}
              onPress={scan}
              disabled={scanning}
              accessibilityRole="button"
              accessibilityLabel={t("premium:privateOffice.shield.scan")}
            >
              {scanning ? (
                <ActivityIndicator color={colors.accentStrong} />
              ) : (
                <Ionicons name="scan-outline" size={18} color={colors.accentStrong} />
              )}
              <Text style={styles.primaryText}>
                {t(
                  scanning
                    ? "premium:privateOffice.shield.scanning"
                    : "premium:privateOffice.shield.scan"
                )}
              </Text>
            </Pressable>
          </View>

          <View style={styles.scope}>
            <Text style={styles.blockTitle}>{t("premium:privateOffice.shield.checksTitle")}</Text>
            <Text style={styles.scopeHint}>{t("premium:privateOffice.shield.checksHint")}</Text>
            {result.posture.checks.map((check) => (
              <View key={check} style={styles.checkRow}>
                <Ionicons name="checkmark-circle-outline" size={14} color={colors.muted} />
                <Text style={styles.checkText}>{check}</Text>
              </View>
            ))}
          </View>

          {result.posture.external.length ? (
            <View style={styles.external}>
              <View style={styles.externalHead}>
                <Ionicons name="cloud-offline-outline" size={18} color={colors.warning} />
                <Text style={styles.externalTitle}>
                  {t("premium:privateOffice.shield.externalTitle")}
                </Text>
              </View>
              {result.posture.external.map((coverage, index) => (
                <View key={`${coverage.state}-${index}`} style={styles.externalRow}>
                  <Text style={styles.externalState}>
                    {coverage.monitored
                      ? t("premium:privateOffice.shield.externalMonitored")
                      : t("premium:privateOffice.shield.externalNotMonitored")}
                  </Text>
                  {coverage.note ? <Text style={styles.externalNote}>{coverage.note}</Text> : null}
                </View>
              ))}
            </View>
          ) : null}

          {result.findings.length === 0 ? (
            <FeatureEmptyPanel
              title={t("premium:privateOffice.shield.empty.title")}
              body={t("premium:privateOffice.shield.empty.body")}
            />
          ) : null}

          {result.findings.map((finding) => (
            <FindingCard
              key={finding.id}
              finding={finding}
              busy={busyFindingId === finding.id}
              onUpdate={updateFinding}
            />
          ))}
        </>
      ) : null}

      {result && result.state === "NOT_ENTITLED" ? (
        <FeatureRefusalPanel state="NOT_ENTITLED" minimumTier={result.minimumTier} />
      ) : null}
      {result && result.state === "FEATURE_DISABLED" ? (
        <FeatureRefusalPanel state="FEATURE_DISABLED" />
      ) : null}
      {result && result.state === "NOT_IMPLEMENTED" ? (
        <FeatureRefusalPanel state="NOT_IMPLEMENTED" />
      ) : null}
      {result && result.state === "UNAVAILABLE" ? (
        <FeatureRefusalPanel state="UNAVAILABLE" onRetry={onRefresh} />
      ) : null}
      {result && result.state === "ERROR" ? (
        <FeatureRefusalPanel state="ERROR" onRetry={onRefresh} />
      ) : null}
    </ScrollView>
  );
}

function FindingCard({
  finding,
  busy,
  onUpdate
}: {
  finding: ShieldFinding;
  busy: boolean;
  onUpdate: (findingId: number, status: "ACKNOWLEDGED" | "RESOLVED" | "DISMISSED") => void;
}) {
  const { t } = useTranslation();
  const open = finding.status === "OPEN" || finding.status === "ACKNOWLEDGED";
  return (
    <View style={styles.card}>
      <View style={styles.cardHead}>
        <View
          style={[
            styles.severityDot,
            { backgroundColor: SEVERITY_COLORS[finding.severity] ?? colors.muted }
          ]}
        />
        <View style={styles.cardBody}>
          <Text style={styles.cardTitle}>{finding.title}</Text>
          {finding.detail ? <Text style={styles.cardHint}>{finding.detail}</Text> : null}
          <Text style={styles.cardStatus}>
            {t(`premium:privateOffice.shield.status.${finding.status}`, {
              defaultValue: finding.status
            })}
          </Text>
        </View>
      </View>
      {open ? (
        <View style={styles.actions}>
          {busy ? (
            <ActivityIndicator color={colors.accent} />
          ) : (
            <>
              {finding.status === "OPEN" ? (
                <Pressable
                  style={styles.action}
                  onPress={() => onUpdate(finding.id, "ACKNOWLEDGED")}
                  accessibilityRole="button"
                >
                  <Text style={styles.actionText}>
                    {t("premium:privateOffice.shield.acknowledge")}
                  </Text>
                </Pressable>
              ) : null}
              <Pressable
                style={styles.action}
                onPress={() => onUpdate(finding.id, "RESOLVED")}
                accessibilityRole="button"
              >
                <Text style={styles.actionText}>{t("premium:privateOffice.shield.resolve")}</Text>
              </Pressable>
              <Pressable
                style={styles.action}
                onPress={() => onUpdate(finding.id, "DISMISSED")}
                accessibilityRole="button"
              >
                <Text style={styles.actionMutedText}>
                  {t("premium:privateOffice.shield.dismiss")}
                </Text>
              </Pressable>
            </>
          )}
        </View>
      ) : null}
    </View>
  );
}

const styles = StyleSheet.create({
  root: { flex: 1, backgroundColor: colors.background },
  content: { padding: 18, gap: 14 },
  header: { gap: 6 },
  title: { color: colors.text, fontSize: 24, fontWeight: "800", letterSpacing: 1 },
  subtitle: { color: colors.muted, fontSize: 13, lineHeight: 19 },
  posture: {
    backgroundColor: colors.surface,
    borderColor: colors.border,
    borderWidth: 1,
    borderRadius: 16,
    padding: 16,
    gap: 10
  },
  postureHead: { flexDirection: "row", alignItems: "center", gap: 10 },
  postureCount: { color: colors.text, fontSize: 16, fontWeight: "800" },
  severityRow: { flexDirection: "row", alignItems: "center", gap: 8 },
  severityDot: { width: 8, height: 8, borderRadius: 4 },
  severityText: { color: colors.muted, fontSize: 12, fontWeight: "600" },
  primary: {
    flexDirection: "row",
    alignItems: "center",
    gap: 8,
    alignSelf: "flex-start",
    marginTop: 4,
    paddingHorizontal: 16,
    paddingVertical: 10,
    borderRadius: 999,
    backgroundColor: colors.surfaceRaised,
    borderColor: colors.border,
    borderWidth: 1
  },
  primaryText: { color: colors.accentStrong, fontSize: 14, fontWeight: "700" },
  scope: {
    backgroundColor: colors.surface,
    borderColor: colors.border,
    borderWidth: 1,
    borderRadius: 14,
    padding: 14,
    gap: 6
  },
  scopeHint: { color: colors.muted, fontSize: 12, lineHeight: 18 },
  blockTitle: { color: colors.muted, fontSize: 11, fontWeight: "800", letterSpacing: 1.2 },
  checkRow: { flexDirection: "row", alignItems: "center", gap: 8 },
  checkText: { color: colors.text, fontSize: 13, flex: 1 },
  external: {
    backgroundColor: colors.surface,
    borderColor: colors.warning,
    borderWidth: 1,
    borderRadius: 14,
    padding: 14,
    gap: 8
  },
  externalHead: { flexDirection: "row", alignItems: "center", gap: 8 },
  externalTitle: { color: colors.text, fontSize: 13, fontWeight: "700" },
  externalRow: { gap: 2 },
  externalState: { color: colors.warning, fontSize: 12, fontWeight: "700" },
  externalNote: { color: colors.muted, fontSize: 12, lineHeight: 18 },
  card: {
    backgroundColor: colors.surface,
    borderColor: colors.border,
    borderWidth: 1,
    borderRadius: 14,
    padding: 14,
    gap: 10
  },
  cardHead: { flexDirection: "row", gap: 10 },
  cardBody: { flex: 1, gap: 4 },
  cardTitle: { color: colors.text, fontSize: 14, fontWeight: "700" },
  cardHint: { color: colors.muted, fontSize: 12, lineHeight: 18 },
  cardStatus: { color: colors.muted, fontSize: 11, fontWeight: "800", letterSpacing: 1 },
  actions: { flexDirection: "row", alignItems: "center", gap: 8, flexWrap: "wrap" },
  action: {
    paddingHorizontal: 12,
    paddingVertical: 8,
    borderRadius: 999,
    backgroundColor: colors.surfaceRaised,
    borderColor: colors.border,
    borderWidth: 1
  },
  actionText: { color: colors.accentStrong, fontSize: 12, fontWeight: "700" },
  actionMutedText: { color: colors.muted, fontSize: 12, fontWeight: "700" }
});

export default PrivateShieldScreen;
