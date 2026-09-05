/**
 * Human Concierge — a request desk that never fakes the human.
 *
 * The desk banner renders the server's `staffed` read and its note verbatim.
 * When no operator is on the roster the screen says so before the member
 * writes a word, and nothing here ever synthesizes an operator reply: the
 * thread shows exactly the messages the server stored, attributed to whoever
 * the server says wrote them. A submitted request into an unstaffed desk is
 * an honest queue entry, not a promise.
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
  CONCIERGE_CATEGORIES,
  CONCIERGE_PRIORITIES,
  ConciergeDesk,
  ConciergeHomeResult,
  ConciergeMessage,
  ConciergeRequest,
  cancelConciergeRequest,
  getConciergeHome,
  getConciergeRequest,
  sendConciergeMessage,
  submitConciergeRequest
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

type Props = NativeStackScreenProps<RootStackParamList, "PrivateConcierge">;

const OPEN_STATUSES = new Set(["OPEN", "IN_PROGRESS", "WAITING_ON_USER", "WAITING_ON_PROVIDER"]);

export function PrivateConciergeScreen(props: Props) {
  return (
    <PrivateOfficeLockGate
      onDismiss={() => props.navigation.goBack()}
      onRenew={() => props.navigation.navigate("Premium")}
    >
      <PrivateConciergeBody {...props} />
    </PrivateOfficeLockGate>
  );
}

function PrivateConciergeBody(_props: Props) {
  const { t } = useTranslation();
  const insets = useSafeAreaInsets();
  const [result, setResult] = useState<ConciergeHomeResult | null>(null);
  const [refreshing, setRefreshing] = useState(false);
  const [composing, setComposing] = useState(false);
  const [openRequestId, setOpenRequestId] = useState(0);

  const load = useCallback(async () => {
    const next = await getConciergeHome();
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
        <Text style={styles.title}>{t("premium:privateOffice.features.humanConcierge.label")}</Text>
        <Text style={styles.subtitle}>{t("premium:privateOffice.concierge.subtitle")}</Text>
      </View>

      {result === null ? <FeatureLoadingPanel /> : null}

      {result && result.state === "READY" ? (
        <>
          <DeskBanner desk={result.desk} />

          {!composing ? (
            <Pressable
              style={styles.primary}
              onPress={() => setComposing(true)}
              accessibilityRole="button"
              accessibilityLabel={t("premium:privateOffice.concierge.newRequest")}
            >
              <Ionicons name="create-outline" size={18} color={colors.accentStrong} />
              <Text style={styles.primaryText}>
                {t("premium:privateOffice.concierge.newRequest")}
              </Text>
            </Pressable>
          ) : (
            <RequestForm
              onDone={async () => {
                setComposing(false);
                await load();
              }}
              onCancel={() => setComposing(false)}
            />
          )}

          {result.requests.length === 0 ? (
            <FeatureEmptyPanel
              title={t("premium:privateOffice.concierge.empty.title")}
              body={t("premium:privateOffice.concierge.empty.body")}
            />
          ) : null}

          {result.requests.map((request) => (
            <View key={request.id} style={styles.card}>
              <Pressable
                style={styles.cardHead}
                onPress={() => setOpenRequestId(openRequestId === request.id ? 0 : request.id)}
                accessibilityRole="button"
                accessibilityLabel={request.title}
              >
                <Ionicons
                  name={OPEN_STATUSES.has(request.status) ? "ellipse" : "ellipse-outline"}
                  size={10}
                  color={OPEN_STATUSES.has(request.status) ? colors.accent : colors.muted}
                />
                <View style={styles.cardBody}>
                  <Text style={styles.cardTitle} numberOfLines={1}>
                    {request.title}
                  </Text>
                  <Text style={styles.cardHint}>
                    {t(`premium:privateOffice.concierge.statusWords.${request.status}`, {
                      defaultValue: request.status
                    })}
                  </Text>
                </View>
                <Ionicons
                  name={openRequestId === request.id ? "chevron-up" : "chevron-down"}
                  size={16}
                  color={colors.muted}
                />
              </Pressable>
              {openRequestId === request.id ? (
                <RequestThread requestId={request.id} onChanged={load} />
              ) : null}
            </View>
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

function DeskBanner({ desk }: { desk: ConciergeDesk }) {
  const { t } = useTranslation();
  return (
    <View style={[styles.desk, desk.staffed ? styles.deskStaffed : styles.deskUnstaffed]}>
      <Ionicons
        name={desk.staffed ? "people-outline" : "moon-outline"}
        size={18}
        color={desk.staffed ? colors.accent : colors.warning}
      />
      <View style={styles.deskBody}>
        <Text style={styles.deskTitle}>
          {desk.staffed
            ? t("premium:privateOffice.concierge.desk.staffed", { count: desk.operatorCount })
            : t("premium:privateOffice.concierge.desk.unstaffed")}
        </Text>
        {desk.note ? <Text style={styles.deskNote}>{desk.note}</Text> : null}
      </View>
    </View>
  );
}

function RequestForm({ onDone, onCancel }: { onDone: () => void; onCancel: () => void }) {
  const { t } = useTranslation();
  const [title, setTitle] = useState("");
  const [description, setDescription] = useState("");
  const [category, setCategory] = useState<string>("GENERAL");
  const [priority, setPriority] = useState<string>("NORMAL");
  const [saving, setSaving] = useState(false);

  const submit = useCallback(async () => {
    if (!title.trim()) return;
    setSaving(true);
    try {
      const written = await submitConciergeRequest({
        title: title.trim(),
        description: description.trim() || undefined,
        category,
        priority
      });
      if (written.state === "LOCKED") {
        lockOfficeLocally();
        return;
      }
      if (written.state === "SAVED") {
        onDone();
        return;
      }
      Alert.alert(
        t("premium:privateOffice.concierge.submitFailed"),
        written.state === "REJECTED" && written.message
          ? written.message
          : t("premium:privateOffice.feature.error.body")
      );
    } finally {
      setSaving(false);
    }
  }, [title, description, category, priority, onDone, t]);

  return (
    <View style={styles.form}>
      <TextInput
        style={styles.input}
        value={title}
        onChangeText={setTitle}
        placeholder={t("premium:privateOffice.concierge.form.title")}
        placeholderTextColor={colors.muted}
        accessibilityLabel={t("premium:privateOffice.concierge.form.title")}
      />
      <TextInput
        style={[styles.input, styles.inputTall]}
        value={description}
        onChangeText={setDescription}
        placeholder={t("premium:privateOffice.concierge.form.description")}
        placeholderTextColor={colors.muted}
        multiline
        accessibilityLabel={t("premium:privateOffice.concierge.form.description")}
      />
      <Text style={styles.formLabel}>{t("premium:privateOffice.concierge.form.category")}</Text>
      <View style={styles.chips}>
        {CONCIERGE_CATEGORIES.map((value) => (
          <Pressable
            key={value}
            style={[styles.chip, category === value ? styles.chipActive : null]}
            onPress={() => setCategory(value)}
            accessibilityRole="button"
            accessibilityState={{ selected: category === value }}
          >
            <Text style={[styles.chipText, category === value ? styles.chipTextActive : null]}>
              {t(`premium:privateOffice.concierge.categories.${value}`, { defaultValue: value })}
            </Text>
          </Pressable>
        ))}
      </View>
      <Text style={styles.formLabel}>{t("premium:privateOffice.concierge.form.priority")}</Text>
      <View style={styles.chips}>
        {CONCIERGE_PRIORITIES.map((value) => (
          <Pressable
            key={value}
            style={[styles.chip, priority === value ? styles.chipActive : null]}
            onPress={() => setPriority(value)}
            accessibilityRole="button"
            accessibilityState={{ selected: priority === value }}
          >
            <Text style={[styles.chipText, priority === value ? styles.chipTextActive : null]}>
              {t(`premium:privateOffice.concierge.priorities.${value}`, { defaultValue: value })}
            </Text>
          </Pressable>
        ))}
      </View>
      <View style={styles.formActions}>
        <Pressable style={styles.formCancel} onPress={onCancel} accessibilityRole="button">
          <Text style={styles.formCancelText}>
            {t("premium:privateOffice.concierge.form.cancel")}
          </Text>
        </Pressable>
        <Pressable
          style={[styles.formSave, !title.trim() ? styles.formSaveDisabled : null]}
          onPress={submit}
          disabled={saving || !title.trim()}
          accessibilityRole="button"
        >
          {saving ? (
            <ActivityIndicator color={colors.background} />
          ) : (
            <Text style={styles.formSaveText}>
              {t("premium:privateOffice.concierge.form.submit")}
            </Text>
          )}
        </Pressable>
      </View>
    </View>
  );
}

function RequestThread({ requestId, onChanged }: { requestId: number; onChanged: () => void }) {
  const { t } = useTranslation();
  const [request, setRequest] = useState<ConciergeRequest | null>(null);
  const [thread, setThread] = useState<ConciergeMessage[]>([]);
  const [failed, setFailed] = useState(false);
  const [draft, setDraft] = useState("");
  const [sending, setSending] = useState(false);
  const [canceling, setCanceling] = useState(false);

  const loadThread = useCallback(async () => {
    const next = await getConciergeRequest(requestId);
    if (next.state === "LOCKED") lockOfficeLocally();
    if (next.state === "READY") {
      setRequest(next.request);
      setThread(next.thread);
      setFailed(false);
    } else {
      setFailed(true);
    }
  }, [requestId]);

  useEffect(() => {
    loadThread();
  }, [loadThread]);

  const send = useCallback(async () => {
    if (!draft.trim()) return;
    setSending(true);
    try {
      const sent = await sendConciergeMessage(requestId, draft.trim());
      if (sent.state === "LOCKED") {
        lockOfficeLocally();
        return;
      }
      if (sent.state === "SENT") {
        setDraft("");
        await loadThread();
        return;
      }
      Alert.alert(
        t("premium:privateOffice.concierge.sendFailed"),
        sent.state === "REJECTED" && sent.message
          ? sent.message
          : t("premium:privateOffice.feature.error.body")
      );
    } finally {
      setSending(false);
    }
  }, [draft, requestId, loadThread, t]);

  const cancel = useCallback(async () => {
    setCanceling(true);
    try {
      const canceled = await cancelConciergeRequest(requestId);
      if (canceled.state === "LOCKED") {
        lockOfficeLocally();
        return;
      }
      if (canceled.state === "OK") {
        await loadThread();
        onChanged();
        return;
      }
      Alert.alert(
        t("premium:privateOffice.concierge.cancelFailed"),
        canceled.state === "REJECTED" && canceled.message
          ? canceled.message
          : t("premium:privateOffice.feature.error.body")
      );
    } finally {
      setCanceling(false);
    }
  }, [requestId, loadThread, onChanged, t]);

  if (failed) {
    return (
      <View style={styles.detail}>
        <Text style={styles.note}>{t("premium:privateOffice.feature.error.body")}</Text>
      </View>
    );
  }
  if (request === null) {
    return (
      <View style={styles.detail}>
        <ActivityIndicator color={colors.accent} />
      </View>
    );
  }

  const active = OPEN_STATUSES.has(request.status);

  return (
    <View style={styles.detail}>
      {request.description ? <Text style={styles.description}>{request.description}</Text> : null}

      {thread.length === 0 ? (
        <Text style={styles.note}>{t("premium:privateOffice.concierge.thread.none")}</Text>
      ) : null}
      {thread.map((message) => (
        <View
          key={message.id}
          style={[
            styles.message,
            message.author === "OPERATOR" ? styles.messageOperator : styles.messageMember
          ]}
        >
          <Text style={styles.messageAuthor}>
            {message.author === "OPERATOR"
              ? t("premium:privateOffice.concierge.thread.operator")
              : t("premium:privateOffice.concierge.thread.you")}
          </Text>
          <Text style={styles.messageBody}>{message.body}</Text>
        </View>
      ))}

      {active ? (
        <>
          <View style={styles.composer}>
            <TextInput
              style={[styles.input, styles.composerInput]}
              value={draft}
              onChangeText={setDraft}
              placeholder={t("premium:privateOffice.concierge.thread.placeholder")}
              placeholderTextColor={colors.muted}
              multiline
              accessibilityLabel={t("premium:privateOffice.concierge.thread.placeholder")}
            />
            <Pressable
              style={[styles.send, !draft.trim() ? styles.formSaveDisabled : null]}
              onPress={send}
              disabled={sending || !draft.trim()}
              accessibilityRole="button"
              accessibilityLabel={t("premium:privateOffice.concierge.thread.send")}
            >
              {sending ? (
                <ActivityIndicator color={colors.background} />
              ) : (
                <Ionicons name="send" size={16} color={colors.background} />
              )}
            </Pressable>
          </View>
          <Pressable
            style={styles.cancelRequest}
            onPress={cancel}
            disabled={canceling}
            accessibilityRole="button"
          >
            {canceling ? (
              <ActivityIndicator color={colors.muted} />
            ) : (
              <Text style={styles.cancelRequestText}>
                {t("premium:privateOffice.concierge.cancelRequest")}
              </Text>
            )}
          </Pressable>
        </>
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
  desk: {
    flexDirection: "row",
    alignItems: "flex-start",
    gap: 10,
    borderWidth: 1,
    borderRadius: 14,
    padding: 14
  },
  deskStaffed: { backgroundColor: colors.surface, borderColor: colors.border },
  deskUnstaffed: { backgroundColor: colors.warningSoft, borderColor: colors.warning },
  deskBody: { flex: 1, gap: 3 },
  deskTitle: { color: colors.text, fontSize: 13, fontWeight: "700" },
  deskNote: { color: colors.muted, fontSize: 12, lineHeight: 18 },
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
  inputTall: { minHeight: 72, textAlignVertical: "top" },
  formLabel: { color: colors.muted, fontSize: 11, fontWeight: "800", letterSpacing: 1.2 },
  chips: { flexDirection: "row", flexWrap: "wrap", gap: 8 },
  chip: {
    paddingHorizontal: 12,
    paddingVertical: 7,
    borderRadius: 999,
    backgroundColor: colors.surfaceRaised,
    borderColor: colors.border,
    borderWidth: 1
  },
  chipActive: { borderColor: colors.accent, backgroundColor: colors.surface },
  chipText: { color: colors.muted, fontSize: 12, fontWeight: "600" },
  chipTextActive: { color: colors.accentStrong },
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
  detail: { borderTopColor: colors.border, borderTopWidth: 1, padding: 14, gap: 12 },
  description: { color: colors.muted, fontSize: 13, lineHeight: 19 },
  message: { borderRadius: 12, padding: 10, gap: 3, maxWidth: "92%" },
  messageMember: { backgroundColor: colors.surfaceRaised, alignSelf: "flex-end" },
  messageOperator: {
    backgroundColor: colors.surface,
    borderColor: colors.border,
    borderWidth: 1,
    alignSelf: "flex-start"
  },
  messageAuthor: { color: colors.muted, fontSize: 10, fontWeight: "800", letterSpacing: 1 },
  messageBody: { color: colors.text, fontSize: 13, lineHeight: 19 },
  composer: { flexDirection: "row", alignItems: "flex-end", gap: 8 },
  composerInput: { flex: 1, minHeight: 42, maxHeight: 110 },
  send: {
    width: 40,
    height: 40,
    borderRadius: 20,
    backgroundColor: colors.accent,
    alignItems: "center",
    justifyContent: "center"
  },
  cancelRequest: { alignSelf: "flex-start", paddingVertical: 6 },
  cancelRequestText: { color: colors.muted, fontSize: 12, fontWeight: "700" },
  note: { color: colors.muted, fontSize: 12, lineHeight: 18 }
});

export default PrivateConciergeScreen;
