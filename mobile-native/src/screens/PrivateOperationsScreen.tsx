/**
 * Private Operations — obligations, events, decisions, requests, risks and
 * opportunities, in one screen with six views.
 *
 * ## Every row came from the server
 *
 * The list is `GET /api/private-office/records/<view>`, owner-scoped in SQL,
 * projected by `records._serialize`. No seed rows, no placeholders: an empty
 * view renders the empty state, because a fabricated obligation in a screen
 * whose promise is "this is what needs your attention" would be worse than an
 * empty one.
 *
 * ## The states are not interchangeable
 *
 * Same discipline as Private Facts: READY/EMPTY, NOT_ENTITLED,
 * FEATURE_DISABLED, NOT_IMPLEMENTED, UNAVAILABLE, LOCKED and ERROR are seven
 * different sentences, and UNAVAILABLE must never be drawn as EMPTY — "we
 * could not look" dressed as "nothing needs you" is how a member misses a
 * deadline the server knew about.
 *
 * ## Status moves render the server's vocabulary
 *
 * The status sheet lists exactly the statuses the server returned for this
 * view, in the server's order. A local list would be a second copy of
 * `records.SPECS[...]["statuses"]` and would go stale the first time one is
 * added. Rejections from the writer are shown verbatim: they are written for
 * a person, and a generic "invalid input" would leave the member unable to
 * fix it.
 */

import { NativeStackScreenProps } from "@react-navigation/native-stack";
import { useCallback, useEffect, useState } from "react";
import {
  ActivityIndicator,
  Modal,
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
  PrivateRecord,
  PrivateRecordDraft,
  PrivateRecordView,
  PrivateRecordsResult,
  RECORD_VIEWS,
  asRecordView,
  createPrivateRecord,
  getPrivateRecords,
  setPrivateRecordStatus
} from "../api/privateRecords";
import { useTranslation } from "../i18n";
import { BOTTOM_NAV_CONTENT_CLEARANCE } from "../navigation/BottomNavVisibility";
import { RootStackParamList } from "../navigation/types";
import { PrivateOfficeLockGate } from "../privateOffice/PrivateOfficeLockGate";
import { lockOfficeLocally } from "../privateOffice/officeLock";
import { colors } from "../theme/colors";

type Props = NativeStackScreenProps<RootStackParamList, "PrivateOperations">;

type ScreenState = "LOADING" | "EMPTY" | PrivateRecordsResult["state"];

/**
 * Which draft fields each view's creation form shows. `token` fields are
 * normalised to the server's token grammar (`^[A-Z][A-Z0-9_]{0,47}$`) before
 * sending, so "insurance renewal" becomes INSURANCE_RENEWAL rather than a 400.
 */
const FORM_FIELDS: Readonly<
  Record<
    PrivateRecordView,
    { primary: keyof PrivateRecordDraft; token: keyof PrivateRecordDraft | null; long: keyof PrivateRecordDraft | null; due: boolean }
  >
> = {
  obligations: { primary: "title", token: "obligation_type", long: "summary", due: true },
  events: { primary: "title", token: "event_type", long: "summary", due: false },
  decisions: { primary: "question", token: null, long: "summary", due: false },
  requests: { primary: "title", token: "category", long: "description", due: false },
  risks: { primary: "title", token: "risk_type", long: "summary", due: false },
  opportunities: { primary: "title", token: "opportunity_type", long: "summary", due: false }
};

function asToken(value: string): string {
  const cleaned = value
    .trim()
    .toUpperCase()
    .replace(/[^A-Z0-9_]+/g, "_")
    .replace(/^_+|_+$/g, "")
    .slice(0, 48);
  return /^[A-Z]/.test(cleaned) ? cleaned : "";
}

/** "2027-01-15" -> the ISO instant the server stores; anything else passes through. */
function asDueAt(value: string): string {
  const text = value.trim();
  return /^\d{4}-\d{2}-\d{2}$/.test(text) ? `${text}T00:00:00Z` : text;
}

/** A date-time the server sent, shortened for a row. Display only. */
function shortDate(value: string): string {
  return value.length >= 10 ? value.slice(0, 10) : value;
}

export function PrivateOperationsScreen(props: Props) {
  return (
    <PrivateOfficeLockGate
      onDismiss={() => props.navigation.goBack()}
      onRenew={() => props.navigation.navigate("Premium")}
    >
      <PrivateOperationsBody {...props} />
    </PrivateOfficeLockGate>
  );
}

function PrivateOperationsBody({ route }: Props) {
  const { t } = useTranslation();
  const insets = useSafeAreaInsets();
  const [view, setView] = useState<PrivateRecordView>(
    asRecordView(route.params?.view) ?? "obligations"
  );
  const [state, setState] = useState<ScreenState>("LOADING");
  const [result, setResult] = useState<PrivateRecordsResult | null>(null);
  const [refreshing, setRefreshing] = useState(false);
  const [moving, setMoving] = useState<PrivateRecord | null>(null);
  const [composing, setComposing] = useState(false);
  const [draftPrimary, setDraftPrimary] = useState("");
  const [draftToken, setDraftToken] = useState("");
  const [draftLong, setDraftLong] = useState("");
  const [draftDue, setDraftDue] = useState("");
  const [writeError, setWriteError] = useState("");
  const [saving, setSaving] = useState(false);

  const load = useCallback(async (wanted: PrivateRecordView) => {
    const next = await getPrivateRecords(wanted);
    if (next.state === "LOCKED") lockOfficeLocally();
    setResult(next);
    setState(next.state === "READY" && next.records.length === 0 ? "EMPTY" : next.state);
  }, []);

  useEffect(() => {
    let cancelled = false;
    setState("LOADING");
    (async () => {
      const next = await getPrivateRecords(view);
      if (cancelled) return;
      if (next.state === "LOCKED") lockOfficeLocally();
      setResult(next);
      setState(next.state === "READY" && next.records.length === 0 ? "EMPTY" : next.state);
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

  const move = useCallback(
    async (record: PrivateRecord, status: string) => {
      setMoving(null);
      const answer = await setPrivateRecordStatus(view, record.id, status);
      if (answer.state === "LOCKED") lockOfficeLocally();
      if (answer.state === "REJECTED") {
        setWriteError(answer.message || t("premium:privateOffice.operations.writeFailed"));
        return;
      }
      if (answer.state !== "OK") {
        setWriteError(t("premium:privateOffice.operations.writeFailed"));
        return;
      }
      setWriteError("");
      await load(view);
    },
    [load, t, view]
  );

  const openComposer = useCallback(() => {
    setDraftPrimary("");
    setDraftToken("");
    setDraftLong("");
    setDraftDue("");
    setWriteError("");
    setComposing(true);
  }, []);

  const submit = useCallback(async () => {
    const shape = FORM_FIELDS[view];
    const draft: PrivateRecordDraft = {};
    draft[shape.primary] = draftPrimary.trim();
    if (shape.token) draft[shape.token] = asToken(draftToken);
    if (shape.long && draftLong.trim()) draft[shape.long] = draftLong.trim();
    if (shape.due && draftDue.trim()) draft.due_at = asDueAt(draftDue);
    setSaving(true);
    try {
      const answer = await createPrivateRecord(view, draft);
      if (answer.state === "LOCKED") lockOfficeLocally();
      if (answer.state === "REJECTED") {
        setWriteError(answer.message || t("premium:privateOffice.operations.writeFailed"));
        return;
      }
      if (answer.state !== "OK") {
        setWriteError(t("premium:privateOffice.operations.writeFailed"));
        return;
      }
      setWriteError("");
      setComposing(false);
      await load(view);
    } finally {
      setSaving(false);
    }
  }, [draftDue, draftLong, draftPrimary, draftToken, load, t, view]);

  const minimumTier = result && result.state === "NOT_ENTITLED" ? result.minimumTier : "";
  const statuses = result && result.state === "READY" ? result.statuses : [];
  const records = result && result.state === "READY" ? result.records : [];
  const shape = FORM_FIELDS[view];

  const statusLabel = (word: string) =>
    t(`premium:privateOffice.operations.status.${word}`, { defaultValue: word });

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
      <View style={styles.header}>
        <Text style={styles.title}>{t("premium:privateOffice.operations.title")}</Text>
        <Text style={styles.subtitle}>{t("premium:privateOffice.operations.subtitle")}</Text>
      </View>

      <ScrollView horizontal showsHorizontalScrollIndicator={false} contentContainerStyle={styles.chips}>
        {RECORD_VIEWS.map((candidate) => (
          <Pressable
            key={candidate}
            style={[styles.chip, candidate === view ? styles.chipActive : null]}
            onPress={() => setView(candidate)}
            accessibilityRole="button"
            accessibilityState={{ selected: candidate === view }}
          >
            <Text style={[styles.chipText, candidate === view ? styles.chipTextActive : null]}>
              {t(`premium:privateOffice.operations.views.${candidate}`)}
            </Text>
          </Pressable>
        ))}
      </ScrollView>

      {state === "READY" || state === "EMPTY" ? (
        <Pressable style={styles.addButton} onPress={openComposer} accessibilityRole="button">
          <Ionicons name="add-circle-outline" size={18} color={colors.accentStrong} />
          <Text style={styles.addText}>
            {t(`premium:privateOffice.operations.add.${view}`)}
          </Text>
        </Pressable>
      ) : null}

      {writeError && !composing ? (
        <View style={styles.rejection}>
          <Ionicons name="alert-circle-outline" size={16} color={colors.danger} />
          <Text style={styles.rejectionText}>{writeError}</Text>
        </View>
      ) : null}

      {state === "LOADING" ? (
        <View style={styles.panel} accessibilityRole="progressbar">
          <ActivityIndicator color={colors.accent} />
          <Text style={styles.panelText}>{t("premium:privateOffice.operations.loading")}</Text>
        </View>
      ) : null}

      {state === "EMPTY"
        ? notice(
            "file-tray-outline",
            colors.muted,
            t("premium:privateOffice.operations.empty.title"),
            t(`premium:privateOffice.operations.emptyBody.${view}`),
            false
          )
        : null}

      {state === "NOT_ENTITLED"
        ? notice(
            "lock-closed-outline",
            colors.warning,
            t("premium:privateOffice.operations.notEntitled.title"),
            minimumTier
              ? t("premium:privateOffice.operations.notEntitled.body", { tier: minimumTier })
              : t("premium:privateOffice.operations.notEntitled.bodyGeneric"),
            false
          )
        : null}

      {state === "FEATURE_DISABLED"
        ? notice(
            "pause-circle-outline",
            colors.warning,
            t("premium:privateOffice.operations.disabled.title"),
            t("premium:privateOffice.operations.disabled.body"),
            true
          )
        : null}

      {state === "NOT_IMPLEMENTED"
        ? notice(
            "construct-outline",
            colors.muted,
            t("premium:privateOffice.operations.notImplemented.title"),
            t("premium:privateOffice.operations.notImplemented.body"),
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
            t("premium:privateOffice.operations.unavailable.title"),
            t("premium:privateOffice.operations.unavailable.body"),
            true
          )
        : null}

      {state === "ERROR"
        ? notice(
            "alert-circle-outline",
            colors.danger,
            t("premium:privateOffice.operations.error.title"),
            t("premium:privateOffice.operations.error.body"),
            true
          )
        : null}

      {state === "READY"
        ? records.map((record) => (
            <View key={record.id} style={styles.recordRow}>
              <View style={styles.recordHead}>
                <Text style={styles.recordTitle}>
                  {record.title || record.question}
                </Text>
                <Text
                  style={[
                    styles.statusMark,
                    record.effectiveStatus === "OVERDUE" ? styles.statusOverdue : null,
                    record.effectiveStatus === "DUE_SOON" ? styles.statusDueSoon : null
                  ]}
                >
                  {statusLabel(record.effectiveStatus)}
                </Text>
              </View>
              {record.body ? <Text style={styles.recordBody}>{record.body}</Text> : null}
              {record.outcome ? (
                <Text style={styles.recordOutcome}>
                  {t("premium:privateOffice.operations.outcome", { outcome: record.outcome })}
                </Text>
              ) : null}
              <View style={styles.recordMeta}>
                {record.dueAt ? (
                  <Text style={styles.metaText}>
                    {t("premium:privateOffice.operations.due", { date: shortDate(record.dueAt) })}
                  </Text>
                ) : null}
                {record.occurredAt ? (
                  <Text style={styles.metaText}>
                    {t("premium:privateOffice.operations.occurred", {
                      date: shortDate(record.occurredAt)
                    })}
                  </Text>
                ) : null}
                {record.amount ? <Text style={styles.metaText}>{record.amount}</Text> : null}
              </View>
              {statuses.length > 1 ? (
                <Pressable
                  style={styles.moveButton}
                  onPress={() => setMoving(record)}
                  accessibilityRole="button"
                >
                  <Ionicons name="swap-horizontal-outline" size={15} color={colors.accentStrong} />
                  <Text style={styles.moveText}>
                    {t("premium:privateOffice.operations.move")}
                  </Text>
                </Pressable>
              ) : null}
            </View>
          ))
        : null}

      <Modal
        visible={moving !== null}
        transparent
        animationType="slide"
        onRequestClose={() => setMoving(null)}
      >
        <Pressable style={styles.sheetBackdrop} onPress={() => setMoving(null)}>
          <Pressable style={styles.sheet} onPress={() => undefined}>
            <Text style={styles.sheetTitle}>{t("premium:privateOffice.operations.move")}</Text>
            {moving
              ? statuses
                  .filter((word) => word !== moving.status)
                  .map((word) => (
                    <Pressable
                      key={word}
                      style={styles.statusOption}
                      onPress={() => move(moving, word)}
                      accessibilityRole="button"
                    >
                      <Text style={styles.statusOptionText}>{statusLabel(word)}</Text>
                    </Pressable>
                  ))
              : null}
            <Pressable
              style={styles.retry}
              onPress={() => setMoving(null)}
              accessibilityRole="button"
            >
              <Text style={styles.retryText}>{t("premium:privateOffice.operations.cancel")}</Text>
            </Pressable>
          </Pressable>
        </Pressable>
      </Modal>

      <Modal
        visible={composing}
        transparent
        animationType="slide"
        onRequestClose={() => setComposing(false)}
      >
        <Pressable style={styles.sheetBackdrop} onPress={() => setComposing(false)}>
          <Pressable style={styles.sheet} onPress={() => undefined}>
            <Text style={styles.sheetTitle}>
              {t(`premium:privateOffice.operations.add.${view}`)}
            </Text>
            <TextInput
              style={styles.input}
              value={draftPrimary}
              onChangeText={setDraftPrimary}
              placeholder={t(
                shape.primary === "question"
                  ? "premium:privateOffice.operations.form.question"
                  : "premium:privateOffice.operations.form.title"
              )}
              placeholderTextColor={colors.muted}
            />
            {shape.token ? (
              <TextInput
                style={styles.input}
                value={draftToken}
                onChangeText={setDraftToken}
                autoCapitalize="characters"
                placeholder={t("premium:privateOffice.operations.form.kind")}
                placeholderTextColor={colors.muted}
              />
            ) : null}
            {shape.long ? (
              <TextInput
                style={[styles.input, styles.inputLong]}
                value={draftLong}
                onChangeText={setDraftLong}
                multiline
                placeholder={t("premium:privateOffice.operations.form.details")}
                placeholderTextColor={colors.muted}
              />
            ) : null}
            {shape.due ? (
              <TextInput
                style={styles.input}
                value={draftDue}
                onChangeText={setDraftDue}
                placeholder={t("premium:privateOffice.operations.form.due")}
                placeholderTextColor={colors.muted}
              />
            ) : null}
            {writeError ? (
              <View style={styles.rejection}>
                <Ionicons name="alert-circle-outline" size={16} color={colors.danger} />
                <Text style={styles.rejectionText}>{writeError}</Text>
              </View>
            ) : null}
            <View style={styles.sheetActions}>
              <Pressable
                style={styles.retry}
                onPress={() => setComposing(false)}
                accessibilityRole="button"
              >
                <Text style={styles.retryText}>
                  {t("premium:privateOffice.operations.cancel")}
                </Text>
              </Pressable>
              <Pressable
                style={[styles.save, saving ? styles.saveDisabled : null]}
                onPress={submit}
                disabled={saving}
                accessibilityRole="button"
              >
                {saving ? (
                  <ActivityIndicator color={colors.background} size="small" />
                ) : (
                  <Text style={styles.saveText}>
                    {t("premium:privateOffice.operations.save")}
                  </Text>
                )}
              </Pressable>
            </View>
          </Pressable>
        </Pressable>
      </Modal>
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
  addButton: { flexDirection: "row", alignItems: "center", gap: 6 },
  addText: { color: colors.accentStrong, fontSize: 13, fontWeight: "700" },
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
  recordRow: {
    backgroundColor: colors.surface,
    borderColor: colors.border,
    borderWidth: 1,
    borderRadius: 14,
    padding: 14,
    gap: 6
  },
  recordHead: {
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "space-between",
    gap: 10
  },
  recordTitle: { color: colors.text, fontSize: 15, fontWeight: "700", flexShrink: 1 },
  statusMark: { color: colors.muted, fontSize: 10, fontWeight: "800", letterSpacing: 0.8 },
  statusOverdue: { color: colors.danger },
  statusDueSoon: { color: colors.warning },
  recordBody: { color: colors.muted, fontSize: 13, lineHeight: 19 },
  recordOutcome: { color: colors.text, fontSize: 13, lineHeight: 19, fontWeight: "600" },
  recordMeta: { flexDirection: "row", flexWrap: "wrap", gap: 12 },
  metaText: { color: colors.muted, fontSize: 11 },
  moveButton: { flexDirection: "row", alignItems: "center", gap: 5, marginTop: 2 },
  moveText: { color: colors.accentStrong, fontSize: 12, fontWeight: "700" },
  rejection: { flexDirection: "row", alignItems: "flex-start", gap: 6 },
  rejectionText: { color: colors.danger, fontSize: 12, lineHeight: 17, flexShrink: 1 },
  sheetBackdrop: { flex: 1, backgroundColor: "rgba(0,0,0,0.6)", justifyContent: "flex-end" },
  sheet: {
    backgroundColor: colors.surfaceRaised,
    borderTopLeftRadius: 20,
    borderTopRightRadius: 20,
    padding: 20,
    gap: 12
  },
  sheetTitle: { color: colors.text, fontSize: 16, fontWeight: "800" },
  statusOption: {
    paddingVertical: 11,
    paddingHorizontal: 14,
    borderRadius: 12,
    backgroundColor: colors.surface,
    borderColor: colors.border,
    borderWidth: 1
  },
  statusOptionText: { color: colors.text, fontSize: 14, fontWeight: "600" },
  input: {
    backgroundColor: colors.surface,
    borderColor: colors.border,
    borderWidth: 1,
    borderRadius: 12,
    paddingHorizontal: 13,
    paddingVertical: 10,
    color: colors.text,
    fontSize: 14
  },
  inputLong: { minHeight: 74, textAlignVertical: "top" },
  sheetActions: { flexDirection: "row", justifyContent: "flex-end", gap: 10 },
  save: {
    marginTop: 6,
    paddingHorizontal: 18,
    paddingVertical: 8,
    borderRadius: 999,
    backgroundColor: colors.accentStrong,
    minWidth: 74,
    alignItems: "center"
  },
  saveDisabled: { opacity: 0.6 },
  saveText: { color: colors.background, fontSize: 13, fontWeight: "800" }
});

export default PrivateOperationsScreen;
