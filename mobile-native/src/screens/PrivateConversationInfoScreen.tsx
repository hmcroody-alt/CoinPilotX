/**
 * Private Conversation info — the Office classification of one canonical thread.
 *
 * ## What belongs here, and what deliberately does not
 *
 * This screen owns the three things that exist only because the Office exists:
 * the thread's scope, its sensitivity, and its links to documents, records,
 * facts, meetings, organization nodes and projects. It owns nothing else.
 *
 * Participants, mute, pin, media and the thread itself are canonical Messenger
 * concerns with a canonical screen already, and duplicating even one of them
 * here would create a second place the member can change something — with two
 * answers the moment one of the two forgets to refresh.
 *
 * ## Links are references, never copies
 *
 * Linking a document does not move bytes into this conversation, and linking a
 * fact does not assert it. The owning domain stays the only writer of the
 * linked object; this screen only records that the two are related, and
 * unlinking removes the relation and nothing else. The copy says so, because a
 * member who believes "unlink" deletes their document will not use the feature.
 *
 * ## Encryption (Stage 53)
 *
 * The security row states what is true of this transport. It reaches the
 * "encrypted" string only when the server sets `end_to_end_encrypted: true`,
 * which today it never does. There is no default, no inference and no
 * aspirational copy — see `src/api/privateConversations.ts`.
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
  PRIVATE_CONVERSATION_SENSITIVITIES,
  type PrivateConversationDetailResult,
  type PrivateConversationLink,
  type PrivateConversationLinkType,
  type PrivateConversationSensitivity,
  getPrivateConversation,
  setPrivateConversationSensitivity,
  unlinkPrivateConversation
} from "../api/privateConversations";
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

type Props = NativeStackScreenProps<RootStackParamList, "PrivateConversationInfo">;

const LINK_ICONS: Record<PrivateConversationLinkType, keyof typeof Ionicons.glyphMap> = {
  DOCUMENT: "document-text-outline",
  RECORD: "folder-open-outline",
  FACT: "sparkles-outline",
  MEETING: "videocam-outline",
  ORGANIZATION_NODE: "business-outline",
  PROJECT: "briefcase-outline"
};

export function PrivateConversationInfoScreen(props: Props) {
  return (
    <PrivateOfficeLockGate
      onDismiss={() => props.navigation.goBack()}
      onRenew={() => props.navigation.navigate("Premium")}
    >
      <PrivateConversationInfoBody {...props} />
    </PrivateOfficeLockGate>
  );
}

function PrivateConversationInfoBody({ navigation, route }: Props) {
  const { t } = useTranslation();
  const insets = useSafeAreaInsets();
  const conversationId = route.params?.conversationId || 0;
  const [result, setResult] = useState<PrivateConversationDetailResult | null>(null);
  const [refreshing, setRefreshing] = useState(false);
  const [savingSensitivity, setSavingSensitivity] = useState(false);
  const [busyLink, setBusyLink] = useState("");

  const load = useCallback(async () => {
    const next = await getPrivateConversation(conversationId);
    if (next.state === "LOCKED") lockOfficeLocally();
    setResult(next);
  }, [conversationId]);

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

  const chooseSensitivity = useCallback(
    async (level: PrivateConversationSensitivity) => {
      setSavingSensitivity(true);
      try {
        const written = await setPrivateConversationSensitivity(conversationId, level);
        if (written.state === "LOCKED") {
          lockOfficeLocally();
          return;
        }
        if (written.state === "SAVED") {
          // Re-read rather than patching state locally: the server may have
          // normalized the value, and a screen that shows what it *sent* rather
          // than what was *stored* is a screen that can display a setting the
          // backend refused.
          await load();
          return;
        }
        Alert.alert(
          t("premium:privateOffice.conversations.sensitivity.failed"),
          written.state === "REJECTED" && written.message
            ? written.message
            : t("premium:privateOffice.feature.error.body")
        );
      } finally {
        setSavingSensitivity(false);
      }
    },
    [conversationId, load, t]
  );

  const removeLink = useCallback(
    async (link: PrivateConversationLink) => {
      if (!link.linkType) return;
      const key = `${link.linkType}:${link.targetId}`;
      setBusyLink(key);
      try {
        const written = await unlinkPrivateConversation(
          conversationId,
          link.linkType,
          link.targetId
        );
        if (written.state === "LOCKED") {
          lockOfficeLocally();
          return;
        }
        if (written.state === "SAVED") {
          await load();
          return;
        }
        Alert.alert(
          t("premium:privateOffice.conversations.links.failed"),
          written.state === "REJECTED" && written.message
            ? written.message
            : t("premium:privateOffice.feature.error.body")
        );
      } finally {
        setBusyLink("");
      }
    },
    [conversationId, load, t]
  );

  const ready = result && result.state === "READY" ? result : null;

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
          {ready?.conversation?.title ||
            ready?.conversation?.name ||
            t("premium:privateOffice.conversations.info.title")}
        </Text>
        <Text style={styles.subtitle}>
          {t("premium:privateOffice.conversations.info.subtitle")}
        </Text>
      </View>

      {result === null ? <FeatureLoadingPanel /> : null}

      {result && result.state === "NOT_FOUND" ? (
        <FeatureEmptyPanel
          title={t("premium:privateOffice.conversations.info.notFound.title")}
          body={t("premium:privateOffice.conversations.info.notFound.body")}
        />
      ) : null}

      {ready ? (
        <>
          <Pressable
            style={styles.openThread}
            onPress={() =>
              navigation.navigate("Chat", {
                conversationId,
                title: ready.conversation?.title || ready.conversation?.name
              })
            }
            accessibilityRole="button"
            accessibilityLabel={t("premium:privateOffice.conversations.info.openThread")}
          >
            <Ionicons name="chatbubbles-outline" size={18} color={colors.accentStrong} />
            <Text style={styles.openThreadText}>
              {t("premium:privateOffice.conversations.info.openThread")}
            </Text>
          </Pressable>

          <Section title={t("premium:privateOffice.conversations.info.classification")}>
            <Row
              label={t("premium:privateOffice.conversations.info.scope")}
              value={
                ready.classification.officeScope
                  ? t(
                      `premium:privateOffice.conversations.scopes.${ready.classification.officeScope}`
                    )
                  : t("premium:privateOffice.unknown")
              }
            />
            {ready.classification.organizationNodeId ? (
              <Row
                label={t("premium:privateOffice.conversations.linkTypes.ORGANIZATION_NODE")}
                value={`#${ready.classification.organizationNodeId}`}
              />
            ) : null}
            {ready.classification.operationsProjectId ? (
              <Row
                label={t("premium:privateOffice.conversations.linkTypes.PROJECT")}
                value={`#${ready.classification.operationsProjectId}`}
              />
            ) : null}
            {ready.classification.meetingId ? (
              <Row
                label={t("premium:privateOffice.conversations.linkTypes.MEETING")}
                value={`#${ready.classification.meetingId}`}
              />
            ) : null}
            {ready.classification.archived ? (
              <Row
                label={t("premium:privateOffice.conversations.archived")}
                value={t("premium:privateOffice.conversations.info.archivedYes")}
              />
            ) : null}
          </Section>

          <Section title={t("premium:privateOffice.conversations.sensitivity.title")}>
            <Text style={styles.sectionNote}>
              {t("premium:privateOffice.conversations.sensitivity.note")}
            </Text>
            <View style={styles.levels}>
              {PRIVATE_CONVERSATION_SENSITIVITIES.map((level) => {
                const on = ready.classification.sensitivity === level;
                const label = t(
                  `premium:privateOffice.conversations.sensitivity.${level}`,
                  { defaultValue: level }
                );
                return (
                  <Pressable
                    key={level}
                    style={[styles.level, on ? styles.levelOn : null]}
                    onPress={() => chooseSensitivity(level)}
                    disabled={savingSensitivity || on}
                    accessibilityRole="button"
                    accessibilityState={{ selected: on, disabled: savingSensitivity }}
                    accessibilityLabel={label}
                  >
                    <Text style={[styles.levelText, on ? styles.levelTextOn : null]}>
                      {label}
                    </Text>
                  </Pressable>
                );
              })}
            </View>
            {/*
              A value the server stored that this build does not know about is
              shown rather than hidden. The picker lagging the server is a
              cosmetic gap; silently rendering a thread as unclassified when it
              is RESTRICTED is not.
            */}
            {ready.classification.sensitivity &&
            !(PRIVATE_CONVERSATION_SENSITIVITIES as readonly string[]).includes(
              ready.classification.sensitivity
            ) ? (
              <Row
                label={t("premium:privateOffice.conversations.sensitivity.unknownLabel")}
                value={ready.classification.sensitivity}
              />
            ) : null}
            {savingSensitivity ? <ActivityIndicator color={colors.accent} /> : null}
          </Section>

          <Section title={t("premium:privateOffice.conversations.links.title")}>
            <Text style={styles.sectionNote}>
              {t("premium:privateOffice.conversations.links.note")}
            </Text>
            {ready.links.length === 0 ? (
              <Text style={styles.sectionNote}>
                {t("premium:privateOffice.conversations.links.empty")}
              </Text>
            ) : null}
            {ready.links.map((link) => {
              const key = `${link.linkType}:${link.targetId}`;
              return (
                <View key={key} style={styles.link}>
                  <Ionicons
                    name={link.linkType ? LINK_ICONS[link.linkType] : "link-outline"}
                    size={18}
                    color={colors.accent}
                  />
                  <View style={styles.linkBody}>
                    <Text style={styles.linkLabel} numberOfLines={1}>
                      {link.label || `#${link.targetId}`}
                    </Text>
                    <Text style={styles.linkType}>
                      {link.linkType
                        ? t(
                            `premium:privateOffice.conversations.linkTypes.${link.linkType}`
                          )
                        : ""}
                    </Text>
                  </View>
                  <Pressable
                    onPress={() => removeLink(link)}
                    disabled={busyLink === key}
                    hitSlop={8}
                    accessibilityRole="button"
                    accessibilityLabel={t("premium:privateOffice.conversations.links.remove")}
                  >
                    {busyLink === key ? (
                      <ActivityIndicator color={colors.muted} />
                    ) : (
                      <Ionicons name="close-outline" size={18} color={colors.muted} />
                    )}
                  </Pressable>
                </View>
              );
            })}
          </Section>

          <Section title={t("premium:privateOffice.conversations.security.title")}>
            <Text style={styles.sectionNote}>
              {ready.capabilities.endToEndEncrypted
                ? t("premium:privateOffice.conversations.security.encrypted")
                : ready.capabilities.encryptionNote ||
                  t("premium:privateOffice.conversations.security.notEncrypted")}
            </Text>
            {/*
              Naming the ledger and the RTC provider is not decoration. It is the
              claim "there is exactly one of each" made checkable by a member
              looking at the screen, and by a test reading these values.
            */}
            {ready.capabilities.messageLedger ? (
              <Row
                label={t("premium:privateOffice.conversations.security.ledger")}
                value={ready.capabilities.messageLedger}
              />
            ) : null}
            {ready.capabilities.rtcProvider ? (
              <Row
                label={t("premium:privateOffice.conversations.security.rtc")}
                value={ready.capabilities.rtcProvider}
              />
            ) : null}
          </Section>
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

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <View style={styles.section}>
      <Text style={styles.sectionTitle}>{title}</Text>
      {children}
    </View>
  );
}

function Row({ label, value }: { label: string; value: string }) {
  return (
    <View style={styles.row}>
      <Text style={styles.rowLabel}>{label}</Text>
      <Text style={styles.rowValue} numberOfLines={1}>
        {value}
      </Text>
    </View>
  );
}

const styles = StyleSheet.create({
  root: { flex: 1, backgroundColor: colors.background },
  content: { padding: 18, gap: 14 },
  header: { gap: 6 },
  title: { color: colors.text, fontSize: 22, fontWeight: "800", letterSpacing: 0.6 },
  subtitle: { color: colors.muted, fontSize: 13, lineHeight: 19 },
  openThread: {
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
  openThreadText: { color: colors.accentStrong, fontSize: 14, fontWeight: "700" },
  section: {
    gap: 10,
    padding: 14,
    backgroundColor: colors.surface,
    borderColor: colors.border,
    borderWidth: 1,
    borderRadius: 14
  },
  sectionTitle: {
    color: colors.muted,
    fontSize: 11,
    fontWeight: "800",
    letterSpacing: 1.2
  },
  sectionNote: { color: colors.muted, fontSize: 12, lineHeight: 18 },
  row: { flexDirection: "row", alignItems: "center", gap: 12 },
  rowLabel: { color: colors.muted, fontSize: 12, flex: 1 },
  rowValue: { color: colors.text, fontSize: 13, fontWeight: "600", flexShrink: 1 },
  levels: { flexDirection: "row", flexWrap: "wrap", gap: 8 },
  level: {
    paddingHorizontal: 12,
    paddingVertical: 8,
    borderRadius: 999,
    backgroundColor: colors.surfaceRaised,
    borderColor: colors.border,
    borderWidth: 1
  },
  levelOn: { borderColor: colors.accent },
  levelText: { color: colors.muted, fontSize: 12, fontWeight: "700" },
  levelTextOn: { color: colors.accentStrong },
  link: {
    flexDirection: "row",
    alignItems: "center",
    gap: 10,
    backgroundColor: colors.surfaceRaised,
    borderRadius: 10,
    padding: 12
  },
  linkBody: { flex: 1, gap: 2 },
  linkLabel: { color: colors.text, fontSize: 14, fontWeight: "600" },
  linkType: { color: colors.muted, fontSize: 10, fontWeight: "800", letterSpacing: 0.6 }
});

export default PrivateConversationInfoScreen;
