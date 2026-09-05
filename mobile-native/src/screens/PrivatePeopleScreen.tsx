/**
 * Relationship Intelligence — the people recorded in the member's own office.
 *
 * Everything on this screen is the member's own record: the directory is what
 * they added, the counts are how their own obligations and requests cite each
 * person, and the profile timeline is assembled from their own rows. Nothing
 * is looked up about anyone — there is no outside source here to be truthful
 * about, only the member's records reflected back.
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
  TextInput,
  View
} from "react-native";
import { Ionicons } from "@expo/vector-icons";
import { useSafeAreaInsets } from "react-native-safe-area-context";
import {
  PrivatePeopleResult,
  PrivatePersonProfile,
  addPrivatePerson,
  getPrivatePeople,
  getPrivatePersonProfile
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

type Props = NativeStackScreenProps<RootStackParamList, "PrivatePeople">;

export function PrivatePeopleScreen(props: Props) {
  return (
    <PrivateOfficeLockGate
      onDismiss={() => props.navigation.goBack()}
      onRenew={() => props.navigation.navigate("Premium")}
    >
      <PrivatePeopleBody {...props} />
    </PrivateOfficeLockGate>
  );
}

function PrivatePeopleBody(_props: Props) {
  const { t } = useTranslation();
  const insets = useSafeAreaInsets();
  const [result, setResult] = useState<PrivatePeopleResult | null>(null);
  const [refreshing, setRefreshing] = useState(false);
  const [adding, setAdding] = useState(false);
  const [name, setName] = useState("");
  const [role, setRole] = useState("");
  const [saving, setSaving] = useState(false);
  const [openNodeId, setOpenNodeId] = useState(0);

  const load = useCallback(async () => {
    const next = await getPrivatePeople();
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

  const save = useCallback(async () => {
    if (!name.trim()) return;
    setSaving(true);
    try {
      const written = await addPrivatePerson({ name: name.trim(), role: role.trim() || undefined });
      if (written.state === "LOCKED") {
        lockOfficeLocally();
        return;
      }
      if (written.state === "SAVED") {
        setName("");
        setRole("");
        setAdding(false);
        await load();
        return;
      }
      Alert.alert(
        t("premium:privateOffice.people.addFailed"),
        written.state === "REJECTED" && written.message
          ? written.message
          : t("premium:privateOffice.feature.error.body")
      );
    } finally {
      setSaving(false);
    }
  }, [name, role, load, t]);

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
          {t("premium:privateOffice.features.relationshipIntelligence.label")}
        </Text>
        <Text style={styles.subtitle}>{t("premium:privateOffice.people.subtitle")}</Text>
      </View>

      {result === null ? <FeatureLoadingPanel /> : null}

      {result && result.state === "READY" && !adding ? (
        <Pressable
          style={styles.primary}
          onPress={() => setAdding(true)}
          accessibilityRole="button"
          accessibilityLabel={t("premium:privateOffice.people.add")}
        >
          <Ionicons name="person-add-outline" size={18} color={colors.accentStrong} />
          <Text style={styles.primaryText}>{t("premium:privateOffice.people.add")}</Text>
        </Pressable>
      ) : null}

      {result && result.state === "READY" && adding ? (
        <View style={styles.form}>
          <TextInput
            style={styles.input}
            value={name}
            onChangeText={setName}
            placeholder={t("premium:privateOffice.people.form.name")}
            placeholderTextColor={colors.muted}
            accessibilityLabel={t("premium:privateOffice.people.form.name")}
          />
          <TextInput
            style={styles.input}
            value={role}
            onChangeText={setRole}
            placeholder={t("premium:privateOffice.people.form.role")}
            placeholderTextColor={colors.muted}
            accessibilityLabel={t("premium:privateOffice.people.form.role")}
          />
          <View style={styles.formActions}>
            <Pressable
              style={styles.formCancel}
              onPress={() => setAdding(false)}
              accessibilityRole="button"
            >
              <Text style={styles.formCancelText}>
                {t("premium:privateOffice.people.form.cancel")}
              </Text>
            </Pressable>
            <Pressable
              style={[styles.formSave, !name.trim() ? styles.formSaveDisabled : null]}
              onPress={save}
              disabled={saving || !name.trim()}
              accessibilityRole="button"
            >
              {saving ? (
                <ActivityIndicator color={colors.background} />
              ) : (
                <Text style={styles.formSaveText}>
                  {t("premium:privateOffice.people.form.save")}
                </Text>
              )}
            </Pressable>
          </View>
        </View>
      ) : null}

      {result && result.state === "READY" && result.people.length === 0 ? (
        <FeatureEmptyPanel
          title={t("premium:privateOffice.people.empty.title")}
          body={t("premium:privateOffice.people.empty.body")}
        />
      ) : null}

      {result && result.state === "READY"
        ? result.people.map((person) => (
            <View key={person.nodeId} style={styles.card}>
              <Pressable
                style={styles.cardHead}
                onPress={() => setOpenNodeId(openNodeId === person.nodeId ? 0 : person.nodeId)}
                accessibilityRole="button"
                accessibilityLabel={person.name}
              >
                <Ionicons name="person-circle-outline" size={22} color={colors.accent} />
                <View style={styles.cardBody}>
                  <Text style={styles.cardTitle} numberOfLines={1}>
                    {person.name}
                  </Text>
                  {person.role ? (
                    <Text style={styles.cardHint} numberOfLines={1}>
                      {person.role}
                    </Text>
                  ) : null}
                </View>
                <View style={styles.counts}>
                  {person.openCommitments > 0 ? (
                    <Text style={styles.countMark}>
                      {t("premium:privateOffice.people.openCommitments", {
                        count: person.openCommitments
                      })}
                    </Text>
                  ) : null}
                </View>
              </Pressable>
              {openNodeId === person.nodeId ? <PersonDetail nodeId={person.nodeId} /> : null}
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

function PersonDetail({ nodeId }: { nodeId: number }) {
  const { t } = useTranslation();
  const [profile, setProfile] = useState<PrivatePersonProfile | null>(null);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      const next = await getPrivatePersonProfile(nodeId);
      if (cancelled) return;
      if (next.state === "LOCKED") lockOfficeLocally();
      if (next.state === "READY") setProfile(next.profile);
      else setFailed(true);
    })();
    return () => {
      cancelled = true;
    };
  }, [nodeId]);

  if (failed) {
    return (
      <View style={styles.detail}>
        <Text style={styles.note}>{t("premium:privateOffice.feature.error.body")}</Text>
      </View>
    );
  }
  if (profile === null) {
    return (
      <View style={styles.detail}>
        <ActivityIndicator color={colors.accent} />
      </View>
    );
  }

  return (
    <View style={styles.detail}>
      {profile.facts.length ? (
        <View style={styles.block}>
          <Text style={styles.blockTitle}>{t("premium:privateOffice.people.profile.facts")}</Text>
          {profile.facts.map((fact) => (
            <View key={fact.id} style={styles.lineRow}>
              <Text style={styles.lineLabel}>{fact.factType}</Text>
              <Text style={styles.lineValue} numberOfLines={2}>
                {fact.value}
              </Text>
            </View>
          ))}
        </View>
      ) : null}

      {profile.commitments.length ? (
        <View style={styles.block}>
          <Text style={styles.blockTitle}>
            {t("premium:privateOffice.people.profile.commitments")}
          </Text>
          {profile.commitments.map((commitment) => (
            <View key={`${commitment.recordType}-${commitment.id}`} style={styles.lineRow}>
              <Text style={styles.lineValue} numberOfLines={1}>
                {commitment.title}
              </Text>
              {commitment.dueAt ? (
                <Text style={styles.lineDue}>{commitment.dueAt.slice(0, 10)}</Text>
              ) : null}
            </View>
          ))}
        </View>
      ) : null}

      {profile.timeline.length ? (
        <View style={styles.block}>
          <Text style={styles.blockTitle}>
            {t("premium:privateOffice.people.profile.timeline")}
          </Text>
          {profile.timeline.slice(0, 10).map((entry, index) => (
            <View key={`${entry.at}-${index}`} style={styles.lineRow}>
              <Text style={styles.lineDate}>{entry.at.slice(0, 10)}</Text>
              <Text style={styles.lineValue} numberOfLines={1}>
                {entry.label}
              </Text>
            </View>
          ))}
        </View>
      ) : null}

      {!profile.facts.length && !profile.commitments.length && !profile.timeline.length ? (
        <Text style={styles.note}>{t("premium:privateOffice.people.profile.nothing")}</Text>
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
  form: {
    backgroundColor: colors.surface,
    borderColor: colors.border,
    borderWidth: 1,
    borderRadius: 14,
    padding: 14,
    gap: 10
  },
  input: {
    backgroundColor: colors.surfaceRaised,
    borderColor: colors.border,
    borderWidth: 1,
    borderRadius: 10,
    paddingHorizontal: 12,
    paddingVertical: 10,
    color: colors.text,
    fontSize: 14
  },
  formActions: { flexDirection: "row", justifyContent: "flex-end", gap: 10 },
  formCancel: { paddingHorizontal: 14, paddingVertical: 9 },
  formCancelText: { color: colors.muted, fontSize: 13, fontWeight: "700" },
  formSave: {
    paddingHorizontal: 18,
    paddingVertical: 9,
    borderRadius: 999,
    backgroundColor: colors.accent
  },
  formSaveDisabled: { opacity: 0.5 },
  formSaveText: { color: colors.background, fontSize: 13, fontWeight: "800" },
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
  counts: { alignItems: "flex-end", gap: 2 },
  countMark: { color: colors.warning, fontSize: 11, fontWeight: "700" },
  detail: { borderTopColor: colors.border, borderTopWidth: 1, padding: 14, gap: 12 },
  block: { gap: 6 },
  blockTitle: { color: colors.muted, fontSize: 11, fontWeight: "800", letterSpacing: 1.2 },
  lineRow: { flexDirection: "row", alignItems: "center", gap: 8 },
  lineLabel: { color: colors.muted, fontSize: 12, fontWeight: "700", minWidth: 90 },
  lineValue: { color: colors.text, fontSize: 13, flex: 1 },
  lineDue: { color: colors.warning, fontSize: 11, fontWeight: "700" },
  lineDate: { color: colors.muted, fontSize: 11, minWidth: 78 },
  note: { color: colors.muted, fontSize: 12, lineHeight: 18 }
});

export default PrivatePeopleScreen;
