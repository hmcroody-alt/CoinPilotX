/**
 * Private Conversations — the Office view of the member's canonical threads.
 *
 * ## This is a view, not a second messenger
 *
 * Every row here is an ordinary `comm_v2` conversation that has been
 * *classified* as an Office thread. There is no second ledger, no Office-side
 * copy of a message, and no separate unread count — the numbers on these rows
 * are the same numbers Messenger shows, because they come from the same
 * conversation object.
 *
 * ## Why opening a row goes to `Chat` and not to an Office thread screen
 *
 * The obvious build here is a Private-Office-flavoured thread view. It is also
 * the wrong one. A second thread screen means a second composer, a second
 * attachment picker, a second read-receipt call and a second set of bugs, all
 * rendering the same ledger — and the two would drift on the first feature
 * that only got added to one of them.
 *
 * The foundation map (§12.7, Stage 60) already settled the underlying
 * question: an Office conversation is a canonical conversation and *does*
 * appear in ordinary Messenger. If the thread is legitimately reachable there,
 * then re-implementing it here buys nothing but divergence. So this screen
 * routes into the canonical `Chat` screen and confines itself to the part that
 * genuinely does not exist elsewhere: the Office classification, and the
 * cross-domain links, which live on `PrivateConversationInfo`.
 *
 * ## On the security line at the bottom (Stage 53)
 *
 * The footer states what is actually true of this transport, and the string it
 * picks is driven by `capabilities.endToEndEncrypted`, which the client parses
 * with a strict `=== true`. There is no code path that reaches the encrypted
 * copy while the server says false, and no path that reaches it when the field
 * is missing. That is deliberate and tested: a product that prints
 * "end-to-end encrypted" over a transport that is not is worse than a product
 * that says nothing, because the member changes what they are willing to type.
 *
 * ## Two states that must never appear together
 *
 * "No conversations yet" is a claim about data, and it is only ever rendered
 * when the read succeeded. A failed read renders a refusal panel and nothing
 * else. Rendering both is how a member reads "you have no threads" when the
 * truth is "we could not look" — and then stops looking.
 */

import { NativeStackScreenProps } from "@react-navigation/native-stack";
import { useCallback, useEffect, useMemo, useState } from "react";
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
  PRIVATE_CONVERSATION_SCOPES,
  type PrivateConversationCapabilities,
  type PrivateConversationListResult,
  type PrivateConversationScope,
  type PrivateConversationSummary,
  createPrivateConversation,
  listPrivateConversations
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

type Props = NativeStackScreenProps<RootStackParamList, "PrivateConversations">;

const SCOPE_ICONS: Record<PrivateConversationScope, keyof typeof Ionicons.glyphMap> = {
  DIRECT: "person-outline",
  GROUP: "people-outline",
  ORGANIZATION_ROOM: "business-outline",
  PROJECT_ROOM: "briefcase-outline"
};

/**
 * The scopes this screen can create on its own.
 *
 * `ORGANIZATION_ROOM` and `PROJECT_ROOM` require a binding to a node or a
 * project, and the server refuses without one. Offering them here with nothing
 * to bind would produce a button whose only outcome is an error, so they are
 * offered as filters but not as create verbs, and the screen says where they
 * are created instead.
 */
const CREATABLE_SCOPES: readonly PrivateConversationScope[] = ["DIRECT", "GROUP"];

export function PrivateConversationsScreen(props: Props) {
  return (
    <PrivateOfficeLockGate
      onDismiss={() => props.navigation.goBack()}
      onRenew={() => props.navigation.navigate("Premium")}
    >
      <PrivateConversationsBody {...props} />
    </PrivateOfficeLockGate>
  );
}

function PrivateConversationsBody({ navigation, route }: Props) {
  const { t } = useTranslation();
  const insets = useSafeAreaInsets();
  const [result, setResult] = useState<PrivateConversationListResult | null>(null);
  const [refreshing, setRefreshing] = useState(false);
  const [scope, setScope] = useState<PrivateConversationScope | null>(
    normalizeScopeParam(route.params?.scope)
  );
  const [creatingScope, setCreatingScope] = useState<PrivateConversationScope | null>(null);
  const [composerOpen, setComposerOpen] = useState(false);

  const load = useCallback(async () => {
    const next = await listPrivateConversations(scope ?? undefined);
    if (next.state === "LOCKED") lockOfficeLocally();
    setResult(next);
  }, [scope]);

  useEffect(() => {
    // A scope change is a new read, and the old rows must not survive it: the
    // list is re-fetched, so showing the previous scope's threads under the
    // newly selected chip would be a lie for the duration of the request.
    setResult(null);
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

  const create = useCallback(
    async (officeScope: PrivateConversationScope) => {
      setCreatingScope(officeScope);
      try {
        const written = await createPrivateConversation({ officeScope });
        if (written.state === "LOCKED") {
          lockOfficeLocally();
          return;
        }
        if (written.state === "CREATED") {
          setComposerOpen(false);
          await load();
          navigation.navigate("Chat", {
            conversationId: written.conversationId,
            title: written.conversation?.title || written.conversation?.name
          });
          return;
        }
        Alert.alert(
          t("premium:privateOffice.conversations.create.failed"),
          written.state === "REJECTED" && written.message
            ? written.message
            : t("premium:privateOffice.feature.error.body")
        );
      } finally {
        setCreatingScope(null);
      }
    },
    [load, navigation, t]
  );

  const ready = result && result.state === "READY" ? result : null;
  const capabilities = ready?.capabilities ?? null;

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
        <Text style={styles.title}>{t("premium:privateOffice.conversations.title")}</Text>
        <Text style={styles.subtitle}>{t("premium:privateOffice.conversations.subtitle")}</Text>
      </View>

      <ScopeFilter selected={scope} onSelect={setScope} />

      {result === null ? <FeatureLoadingPanel /> : null}

      {ready ? (
        <Composer
          open={composerOpen}
          busyScope={creatingScope}
          onToggle={() => setComposerOpen((open) => !open)}
          onCreate={create}
        />
      ) : null}

      {/*
        Empty is a claim about data and is gated on a successful read. The
        refusal panels below are the mutually exclusive alternative — `ready` is
        null in every one of those branches, so the two can never co-render.
      */}
      {ready && ready.conversations.length === 0 ? (
        <FeatureEmptyPanel
          title={t("premium:privateOffice.conversations.empty.title")}
          body={t("premium:privateOffice.conversations.empty.body")}
        />
      ) : null}

      {ready
        ? ready.conversations.map((summary) => (
            <ConversationRow
              key={summary.conversation.conversation_id || summary.conversation.id}
              summary={summary}
              onOpen={() =>
                navigation.navigate("Chat", {
                  conversationId:
                    summary.conversation.conversation_id || summary.conversation.id,
                  title: summary.conversation.title || summary.conversation.name
                })
              }
              onInfo={() =>
                navigation.navigate("PrivateConversationInfo", {
                  conversationId:
                    summary.conversation.conversation_id || summary.conversation.id
                })
              }
            />
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

      {capabilities ? <SecurityFootnote capabilities={capabilities} /> : null}
    </ScrollView>
  );
}

function normalizeScopeParam(value: unknown): PrivateConversationScope | null {
  const word = typeof value === "string" ? value.trim().toUpperCase() : "";
  return (PRIVATE_CONVERSATION_SCOPES as readonly string[]).includes(word)
    ? (word as PrivateConversationScope)
    : null;
}

function ScopeFilter({
  selected,
  onSelect
}: {
  selected: PrivateConversationScope | null;
  onSelect: (scope: PrivateConversationScope | null) => void;
}) {
  const { t } = useTranslation();
  return (
    <ScrollView
      horizontal
      showsHorizontalScrollIndicator={false}
      contentContainerStyle={styles.chips}
    >
      <Pressable
        style={[styles.chip, selected === null ? styles.chipOn : null]}
        onPress={() => onSelect(null)}
        accessibilityRole="button"
        accessibilityState={{ selected: selected === null }}
        accessibilityLabel={t("premium:privateOffice.conversations.filters.all")}
      >
        <Text style={[styles.chipText, selected === null ? styles.chipTextOn : null]}>
          {t("premium:privateOffice.conversations.filters.all")}
        </Text>
      </Pressable>
      {PRIVATE_CONVERSATION_SCOPES.map((value) => {
        const on = selected === value;
        const label = t(`premium:privateOffice.conversations.scopes.${value}`);
        return (
          <Pressable
            key={value}
            style={[styles.chip, on ? styles.chipOn : null]}
            onPress={() => onSelect(on ? null : value)}
            accessibilityRole="button"
            accessibilityState={{ selected: on }}
            accessibilityLabel={label}
          >
            <Ionicons
              name={SCOPE_ICONS[value]}
              size={13}
              color={on ? colors.accentStrong : colors.muted}
            />
            <Text style={[styles.chipText, on ? styles.chipTextOn : null]}>{label}</Text>
          </Pressable>
        );
      })}
    </ScrollView>
  );
}

function Composer({
  open,
  busyScope,
  onToggle,
  onCreate
}: {
  open: boolean;
  busyScope: PrivateConversationScope | null;
  onToggle: () => void;
  onCreate: (scope: PrivateConversationScope) => void;
}) {
  const { t } = useTranslation();
  return (
    <View style={styles.composer}>
      <Pressable
        style={styles.primary}
        onPress={onToggle}
        accessibilityRole="button"
        accessibilityState={{ expanded: open }}
        accessibilityLabel={t("premium:privateOffice.conversations.create.open")}
      >
        <Ionicons
          name={open ? "close-outline" : "create-outline"}
          size={18}
          color={colors.accentStrong}
        />
        <Text style={styles.primaryText}>
          {t("premium:privateOffice.conversations.create.open")}
        </Text>
      </Pressable>
      {open ? (
        <View style={styles.composerBody}>
          {CREATABLE_SCOPES.map((value) => (
            <Pressable
              key={value}
              style={styles.composerChoice}
              onPress={() => onCreate(value)}
              disabled={busyScope !== null}
              accessibilityRole="button"
              accessibilityLabel={t(`premium:privateOffice.conversations.scopes.${value}`)}
            >
              {busyScope === value ? (
                <ActivityIndicator color={colors.accent} />
              ) : (
                <Ionicons name={SCOPE_ICONS[value]} size={18} color={colors.accent} />
              )}
              <Text style={styles.composerChoiceText}>
                {t(`premium:privateOffice.conversations.scopes.${value}`)}
              </Text>
            </Pressable>
          ))}
          <Text style={styles.composerNote}>
            {t("premium:privateOffice.conversations.create.boundScopeNote")}
          </Text>
        </View>
      ) : null}
    </View>
  );
}

function ConversationRow({
  summary,
  onOpen,
  onInfo
}: {
  summary: PrivateConversationSummary;
  onOpen: () => void;
  onInfo: () => void;
}) {
  const { t } = useTranslation();
  const { conversation, classification, links } = summary;
  const preview =
    conversation.last_message_preview || conversation.latest_message || "";
  const unread = conversation.unread_count || 0;
  const scopeLabel = classification.officeScope
    ? t(`premium:privateOffice.conversations.scopes.${classification.officeScope}`)
    : "";

  return (
    <Pressable
      style={styles.card}
      onPress={onOpen}
      accessibilityRole="button"
      accessibilityLabel={conversation.title || conversation.name || scopeLabel}
    >
      <Ionicons
        name={
          classification.officeScope
            ? SCOPE_ICONS[classification.officeScope]
            : "chatbubbles-outline"
        }
        size={20}
        color={colors.accent}
      />
      <View style={styles.cardBody}>
        <View style={styles.cardTitleRow}>
          <Text style={styles.cardTitle} numberOfLines={1}>
            {conversation.title || conversation.name || scopeLabel}
          </Text>
          {classification.archived ? (
            <Text style={styles.badge}>
              {t("premium:privateOffice.conversations.archived")}
            </Text>
          ) : null}
        </View>
        {preview ? (
          <Text style={styles.cardHint} numberOfLines={1}>
            {preview}
          </Text>
        ) : null}
        <View style={styles.metaRow}>
          {scopeLabel ? <Text style={styles.meta}>{scopeLabel}</Text> : null}
          {classification.sensitivity ? (
            <Text style={styles.meta}>
              {t(
                `premium:privateOffice.conversations.sensitivity.${classification.sensitivity}`,
                { defaultValue: classification.sensitivity }
              )}
            </Text>
          ) : null}
          {/*
            The link count is a number, not a sentence. Rendering it as a glyph
            plus a digit keeps it out of the catalogs entirely — a translated
            "{{n}} linked" would need a plural rule in every language for a
            badge nobody reads as prose. Screen readers get the word.
          */}
          {links.length ? (
            <View
              style={styles.linkMeta}
              accessibilityLabel={t("premium:privateOffice.conversations.links.title")}
            >
              <Ionicons name="link-outline" size={11} color={colors.muted} />
              <Text style={styles.meta}>{String(links.length)}</Text>
            </View>
          ) : null}
        </View>
      </View>
      {unread > 0 ? (
        <View
          style={styles.unread}
          accessibilityLabel={t("premium:privateOffice.conversations.unread")}
        >
          <Text style={styles.unreadText}>{unread > 99 ? "99+" : String(unread)}</Text>
        </View>
      ) : null}
      {/*
        A separate target, not a long-press: the classification is the one thing
        on this row that ordinary Messenger cannot show, so it needs an
        affordance a member can find without being told it exists.
      */}
      <Pressable
        onPress={onInfo}
        hitSlop={10}
        accessibilityRole="button"
        accessibilityLabel={t("premium:privateOffice.conversations.info.open")}
      >
        <Ionicons name="information-circle-outline" size={20} color={colors.muted} />
      </Pressable>
    </Pressable>
  );
}

/**
 * The honest security line.
 *
 * `encrypted` is reachable only when the server sets `end_to_end_encrypted:
 * true`. Nothing here infers, defaults, or upgrades that answer — see the
 * module docstring, and `privateConversations.asStrictTrue`.
 */
function SecurityFootnote({
  capabilities
}: {
  capabilities: PrivateConversationCapabilities;
}) {
  const { t } = useTranslation();
  const body = useMemo(() => {
    if (capabilities.endToEndEncrypted) {
      return t("premium:privateOffice.conversations.security.encrypted");
    }
    // The server's own sentence when it sent one; our own only as a fallback.
    return (
      capabilities.encryptionNote ||
      t("premium:privateOffice.conversations.security.notEncrypted")
    );
  }, [capabilities.encryptionNote, capabilities.endToEndEncrypted, t]);

  return (
    <View style={styles.footnote}>
      <Ionicons name="information-circle-outline" size={16} color={colors.muted} />
      <Text style={styles.footnoteText}>{body}</Text>
    </View>
  );
}

const styles = StyleSheet.create({
  root: { flex: 1, backgroundColor: colors.background },
  content: { padding: 18, gap: 14 },
  header: { gap: 6 },
  title: { color: colors.text, fontSize: 24, fontWeight: "800", letterSpacing: 1 },
  subtitle: { color: colors.muted, fontSize: 13, lineHeight: 19 },
  chips: { gap: 8, paddingVertical: 2 },
  chip: {
    flexDirection: "row",
    alignItems: "center",
    gap: 6,
    paddingHorizontal: 14,
    paddingVertical: 8,
    borderRadius: 999,
    backgroundColor: colors.surface,
    borderColor: colors.border,
    borderWidth: 1
  },
  chipOn: { backgroundColor: colors.surfaceRaised, borderColor: colors.accent },
  chipText: { color: colors.muted, fontSize: 12, fontWeight: "700" },
  chipTextOn: { color: colors.accentStrong },
  composer: { gap: 10 },
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
  composerBody: {
    gap: 8,
    backgroundColor: colors.surface,
    borderColor: colors.border,
    borderWidth: 1,
    borderRadius: 14,
    padding: 14
  },
  composerChoice: {
    flexDirection: "row",
    alignItems: "center",
    gap: 10,
    backgroundColor: colors.surfaceRaised,
    borderRadius: 10,
    paddingHorizontal: 12,
    paddingVertical: 11
  },
  composerChoiceText: { color: colors.text, fontSize: 14, fontWeight: "600" },
  composerNote: { color: colors.muted, fontSize: 12, lineHeight: 18 },
  card: {
    flexDirection: "row",
    alignItems: "center",
    gap: 12,
    padding: 14,
    backgroundColor: colors.surface,
    borderColor: colors.border,
    borderWidth: 1,
    borderRadius: 14
  },
  cardBody: { flex: 1, gap: 3 },
  cardTitleRow: { flexDirection: "row", alignItems: "center", gap: 8 },
  cardTitle: { color: colors.text, fontSize: 15, fontWeight: "700", flexShrink: 1 },
  cardHint: { color: colors.muted, fontSize: 12 },
  metaRow: { flexDirection: "row", gap: 10, flexWrap: "wrap" },
  meta: { color: colors.muted, fontSize: 10, fontWeight: "800", letterSpacing: 0.6 },
  linkMeta: { flexDirection: "row", alignItems: "center", gap: 3 },
  badge: {
    color: colors.muted,
    fontSize: 9,
    fontWeight: "800",
    letterSpacing: 0.8,
    paddingHorizontal: 6,
    paddingVertical: 2,
    borderRadius: 6,
    backgroundColor: colors.surfaceRaised
  },
  unread: {
    minWidth: 24,
    paddingHorizontal: 7,
    paddingVertical: 3,
    borderRadius: 999,
    backgroundColor: colors.accent,
    alignItems: "center"
  },
  unreadText: { color: colors.background, fontSize: 11, fontWeight: "800" },
  footnote: {
    flexDirection: "row",
    alignItems: "flex-start",
    gap: 8,
    paddingHorizontal: 4,
    paddingTop: 4
  },
  footnoteText: { color: colors.muted, fontSize: 11, lineHeight: 17, flex: 1 }
});

export default PrivateConversationsScreen;
