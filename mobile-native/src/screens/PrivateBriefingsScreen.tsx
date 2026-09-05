/**
 * Private Briefings — structured digests assembled from the member's own
 * office records.
 *
 * A briefing here is a rearrangement, not a synthesis: every item carries the
 * section the server put it in and the label/detail the server wrote from the
 * member's own rows. The screen adds nothing — no summary line, no "all
 * clear", no advice. An empty briefing is shown as empty.
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
  PrivateBriefingDetail,
  PrivateBriefingsResult,
  generatePrivateBriefing,
  getPrivateBriefing,
  getPrivateBriefings
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

type Props = NativeStackScreenProps<RootStackParamList, "PrivateBriefings">;

const KNOWN_SECTIONS = new Set([
  "PEOPLE",
  "OBLIGATIONS",
  "EVENTS",
  "DECISIONS",
  "REQUESTS",
  "RISKS",
  "OPPORTUNITIES"
]);

export function PrivateBriefingsScreen(props: Props) {
  return (
    <PrivateOfficeLockGate
      onDismiss={() => props.navigation.goBack()}
      onRenew={() => props.navigation.navigate("Premium")}
    >
      <PrivateBriefingsBody {...props} />
    </PrivateOfficeLockGate>
  );
}

function PrivateBriefingsBody(_props: Props) {
  const { t } = useTranslation();
  const insets = useSafeAreaInsets();
  const [result, setResult] = useState<PrivateBriefingsResult | null>(null);
  const [refreshing, setRefreshing] = useState(false);
  const [generating, setGenerating] = useState(false);
  const [openId, setOpenId] = useState(0);

  const load = useCallback(async () => {
    const next = await getPrivateBriefings();
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

  const generate = useCallback(async () => {
    setGenerating(true);
    try {
      const written = await generatePrivateBriefing();
      if (written.state === "LOCKED") {
        lockOfficeLocally();
        return;
      }
      if (written.state === "GENERATED") {
        await load();
        setOpenId(written.briefing.id);
        return;
      }
      Alert.alert(
        t("premium:privateOffice.briefings.generateFailed"),
        t("premium:privateOffice.feature.error.body")
      );
    } finally {
      setGenerating(false);
    }
  }, [load, t]);

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
        <Text style={styles.title}>
          {t("premium:privateOffice.features.privateBriefings.label")}
        </Text>
        <Text style={styles.subtitle}>{t("premium:privateOffice.briefings.subtitle")}</Text>
      </View>

      {result === null ? <FeatureLoadingPanel /> : null}

      {result && result.state === "READY" ? (
        <Pressable
          style={styles.primary}
          onPress={generate}
          disabled={generating}
          accessibilityRole="button"
          accessibilityLabel={t("premium:privateOffice.briefings.generate")}
        >
          {generating ? (
            <ActivityIndicator color={colors.accentStrong} />
          ) : (
            <Ionicons name="reader-outline" size={18} color={colors.accentStrong} />
          )}
          <Text style={styles.primaryText}>
            {t(
              generating
                ? "premium:privateOffice.briefings.generating"
                : "premium:privateOffice.briefings.generate"
            )}
          </Text>
        </Pressable>
      ) : null}

      {result && result.state === "READY" && result.briefings.length === 0 ? (
        <FeatureEmptyPanel
          title={t("premium:privateOffice.briefings.empty.title")}
          body={t("premium:privateOffice.briefings.empty.body")}
        />
      ) : null}

      {result && result.state === "READY"
        ? result.briefings.map((briefing) => (
            <View key={briefing.id} style={styles.card}>
              <Pressable
                style={styles.cardHead}
                onPress={() => setOpenId(openId === briefing.id ? 0 : briefing.id)}
                accessibilityRole="button"
                accessibilityLabel={briefing.title}
              >
                <Ionicons name="document-text-outline" size={22} color={colors.accent} />
                <View style={styles.cardBody}>
                  <Text style={styles.cardTitle} numberOfLines={1}>
                    {briefing.title}
                  </Text>
                  <Text style={styles.cardHint}>
                    {briefing.generatedAt ? briefing.generatedAt.slice(0, 10) : ""}
                    {"  ·  "}
                    {t("premium:privateOffice.briefings.itemCount", {
                      count: briefing.itemCount
                    })}
                  </Text>
                </View>
                <Ionicons
                  name={openId === briefing.id ? "chevron-up" : "chevron-down"}
                  size={16}
                  color={colors.muted}
                />
              </Pressable>
              {openId === briefing.id ? <BriefingDetail id={briefing.id} /> : null}
            </View>
          ))
        : null}

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

function BriefingDetail({ id }: { id: number }) {
  const { t } = useTranslation();
  const [briefing, setBriefing] = useState<PrivateBriefingDetail | null>(null);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      const next = await getPrivateBriefing(id);
      if (cancelled) return;
      if (next.state === "LOCKED") lockOfficeLocally();
      if (next.state === "READY") setBriefing(next.briefing);
      else setFailed(true);
    })();
    return () => {
      cancelled = true;
    };
  }, [id]);

  if (failed) {
    return (
      <View style={styles.detail}>
        <Text style={styles.note}>{t("premium:privateOffice.feature.error.body")}</Text>
      </View>
    );
  }
  if (briefing === null) {
    return (
      <View style={styles.detail}>
        <ActivityIndicator color={colors.accent} />
      </View>
    );
  }

  const sections = briefing.sections.filter((section) => section.items.length > 0);

  return (
    <View style={styles.detail}>
      {sections.length === 0 ? (
        <Text style={styles.note}>{t("premium:privateOffice.briefings.noItems")}</Text>
      ) : null}
      {sections.map((section) => (
        <View key={section.section} style={styles.block}>
          <Text style={styles.blockTitle}>
            {KNOWN_SECTIONS.has(section.section)
              ? t(`premium:privateOffice.briefings.sections.${section.section}`)
              : section.section}
          </Text>
          {section.items.map((item) => (
            <View key={item.id} style={styles.item}>
              <Text style={styles.itemLabel}>{item.label}</Text>
              {item.detail ? <Text style={styles.itemDetail}>{item.detail}</Text> : null}
            </View>
          ))}
        </View>
      ))}
    </View>
  );
}

const styles = StyleSheet.create({
  root: { flex: 1, backgroundColor: colors.background },
  content: { padding: 18, gap: 14 },
  header: { gap: 6 },
  title: { color: colors.text, fontSize: 24, fontWeight: "800", letterSpacing: 1 },
  subtitle: { color: colors.muted, fontSize: 13, lineHeight: 19 },
  primary: {
    flexDirection: "row",
    alignItems: "center",
    gap: 8,
    alignSelf: "flex-start",
    paddingHorizontal: 16,
    paddingVertical: 10,
    borderRadius: 999,
    backgroundColor: colors.surfaceRaised,
    borderColor: colors.border,
    borderWidth: 1
  },
  primaryText: { color: colors.accentStrong, fontSize: 14, fontWeight: "700" },
  card: {
    backgroundColor: colors.surface,
    borderColor: colors.border,
    borderWidth: 1,
    borderRadius: 14
  },
  cardHead: { flexDirection: "row", alignItems: "center", gap: 12, padding: 14 },
  cardBody: { flex: 1, gap: 2 },
  cardTitle: { color: colors.text, fontSize: 15, fontWeight: "700" },
  cardHint: { color: colors.muted, fontSize: 12 },
  detail: { borderTopColor: colors.border, borderTopWidth: 1, padding: 14, gap: 12 },
  block: { gap: 6 },
  blockTitle: { color: colors.muted, fontSize: 11, fontWeight: "800", letterSpacing: 1.2 },
  item: { gap: 2, paddingVertical: 2 },
  itemLabel: { color: colors.text, fontSize: 13, fontWeight: "600" },
  itemDetail: { color: colors.muted, fontSize: 12, lineHeight: 18 },
  note: { color: colors.muted, fontSize: 12, lineHeight: 18 }
});

export default PrivateBriefingsScreen;
