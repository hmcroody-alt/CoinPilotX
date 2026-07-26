import { NativeStackScreenProps } from "@react-navigation/native-stack";
import { useEffect, useRef, useState } from "react";
import {
  ActivityIndicator,
  FlatList,
  Image,
  KeyboardAvoidingView,
  Platform,
  Pressable,
  StyleSheet,
  Text,
  TextInput,
  View
} from "react-native";
import { MessengerUserSearchResult, openDirectConversation, searchMessengerUsers } from "../api/messenger";
import { PulseApiError } from "../api/pulseApi";
import { LogiNexusScreenShell, LogiNexusStatePanel } from "../components/Screen";
import { translate, useTranslation } from "../i18n";
import { RootStackParamList } from "../navigation/types";
import { useAuth } from "../session/auth";
import { colors } from "../theme/colors";

type Props = NativeStackScreenProps<RootStackParamList, "NewChat">;

export function NewChatScreen({ route, navigation }: Props) {
  const { t } = useTranslation();
  const { authState, requestReauthentication } = useAuth();
  const [query, setQuery] = useState(String(route.params?.initialQuery || ""));
  const [results, setResults] = useState<MessengerUserSearchResult[]>([]);
  const [loading, setLoading] = useState(false);
  const [openingUserId, setOpeningUserId] = useState(0);
  const [error, setError] = useState("");
  const debounce = useRef<ReturnType<typeof setTimeout> | null>(null);
  const searchSequence = useRef(0);

  useEffect(() => {
    const clean = query.trim();
    if (debounce.current) clearTimeout(debounce.current);
    if (!clean) {
      searchSequence.current += 1;
      setResults([]);
      setError("");
      setLoading(false);
      return;
    }
    debounce.current = setTimeout(() => runSearch(clean), 320);
    return () => {
      if (debounce.current) clearTimeout(debounce.current);
    };
  }, [query]);

  async function runSearch(value = query.trim()) {
    const clean = value.trim();
    if (!clean) return;
    const sequence = ++searchSequence.current;
    setLoading(true);
    setError("");
    try {
      const next = await searchMessengerUsers(clean);
      if (sequence !== searchSequence.current) return;
      setResults(next);
    } catch (searchError) {
      if (sequence !== searchSequence.current) return;
      setResults([]);
      setError(messageForError(searchError, t("messaging:newChat.searchFailed")));
    } finally {
      if (sequence === searchSequence.current) setLoading(false);
    }
  }

  async function openRecipient(recipient: MessengerUserSearchResult) {
    if (openingUserId) return;
    setOpeningUserId(recipient.user_id);
    setError("");
    try {
      const result = await openDirectConversation(recipient);
      navigation.replace("Chat", { conversationId: result.conversation_id, title: recipient.display_name });
    } catch (openError) {
      if (openError instanceof PulseApiError && openError.status === 401) {
        requestReauthentication("/pulse/messages");
        return;
      }
      setError(messageForError(openError, t("messaging:newChat.openFailed")));
    } finally {
      setOpeningUserId(0);
    }
  }

  if (authState.status !== "signedIn") {
    return (
      <LogiNexusScreenShell>
        <View style={styles.page}>
          <LogiNexusStatePanel state="permission" title={t("messaging:newChat.signInTitle")} body={t("messaging:newChat.signInBody")}>
            <Pressable accessibilityRole="button" style={styles.retryButton} onPress={() => requestReauthentication("/pulse/messages")}>
              <Text style={styles.retryText}>{t("auth:signIn.submit")}</Text>
            </Pressable>
          </LogiNexusStatePanel>
        </View>
      </LogiNexusScreenShell>
    );
  }

  const hasQuery = Boolean(query.trim());
  return (
    <LogiNexusScreenShell>
      <KeyboardAvoidingView style={styles.keyboard} behavior={Platform.OS === "ios" ? "padding" : undefined} keyboardVerticalOffset={88}>
        <View style={styles.page}>
          <View style={styles.intro}>
            <Text style={styles.eyebrow}>{t("messaging:newChat.eyebrow")}</Text>
            <Text style={styles.title}>{t("messaging:newChat.title")}</Text>
            <Text style={styles.subtitle}>{t("messaging:newChat.subtitle")}</Text>
          </View>
          <View style={styles.searchShell}>
            <TextInput
              testID="new-chat-search-input"
              accessibilityLabel={t("messaging:newChat.a11ySearch")}
              autoFocus
              autoCapitalize="none"
              autoCorrect={false}
              returnKeyType="search"
              value={query}
              onChangeText={setQuery}
              onSubmitEditing={() => runSearch()}
              placeholder={t("messaging:newChat.searchPlaceholder")}
              placeholderTextColor={colors.muted}
              style={styles.searchInput}
            />
            {query ? <Pressable accessibilityRole="button" accessibilityLabel={t("messaging:newChat.a11yClearSearch")} onPress={() => setQuery("")} style={styles.clearButton}><Text style={styles.clearText}>{t("common:actions.clear")}</Text></Pressable> : null}
          </View>
          {error ? (
            <View accessibilityLiveRegion="polite" style={styles.errorPanel}>
              <Text style={styles.errorText}>{error}</Text>
              <Pressable accessibilityRole="button" style={styles.retryButton} onPress={() => runSearch()}><Text style={styles.retryText}>{t("common:actions.retry")}</Text></Pressable>
            </View>
          ) : null}
          <FlatList
            keyboardShouldPersistTaps="handled"
            data={results}
            keyExtractor={(item) => `recipient-${item.user_id}`}
            contentContainerStyle={styles.results}
            ListHeaderComponent={loading ? <View style={styles.loading}><ActivityIndicator color={colors.accent} /><Text style={styles.loadingText}>{t("messaging:newChat.searching")}</Text></View> : null}
            ListEmptyComponent={!loading && !error ? (
              <LogiNexusStatePanel
                state="empty"
                title={hasQuery ? t("messaging:newChat.emptyNoResultsTitle") : t("messaging:newChat.emptyPromptTitle")}
                body={hasQuery ? t("messaging:newChat.emptyNoResultsBody") : t("messaging:newChat.emptyPromptBody")}
              />
            ) : null}
            renderItem={({ item }) => (
              <RecipientRow item={item} opening={openingUserId === item.user_id} disabled={openingUserId > 0} onPress={() => openRecipient(item)} />
            )}
          />
        </View>
      </KeyboardAvoidingView>
    </LogiNexusScreenShell>
  );
}

function RecipientRow({ item, opening, disabled, onPress }: { item: MessengerUserSearchResult; opening: boolean; disabled: boolean; onPress: () => void }) {
  const { t } = useTranslation();
  const handle = item.public_pulse_id || (item.public_player_id ? `@${item.public_player_id}` : t("common:identity.member"));
  return (
    <Pressable
      testID={`new-chat-recipient-${item.user_id}`}
      accessibilityRole="button"
      accessibilityLabel={t("messaging:newChat.a11yMessagePerson", { name: item.display_name, handle })}
      disabled={disabled}
      onPress={onPress}
      style={({ pressed }) => [styles.recipient, pressed && styles.recipientPressed, disabled && !opening && styles.disabled]}
    >
      {item.avatar_url ? <Image source={{ uri: item.avatar_url }} style={styles.avatar} /> : <View style={styles.avatarFallback}><Text style={styles.avatarText}>{initials(item.display_name)}</Text></View>}
      <View style={styles.recipientCopy}>
        <View style={styles.nameRow}><Text style={styles.name} numberOfLines={1}>{item.display_name}</Text>{item.premium ? <Text style={styles.premium}>PRO</Text> : null}</View>
        <Text style={styles.handle} numberOfLines={1}>{handle}{item.label ? ` · ${item.label}` : ""}</Text>
      </View>
      {opening ? <ActivityIndicator color={colors.accent} /> : <Text style={styles.messageAction}>{t("messaging:newChat.messageAction")}</Text>}
    </Pressable>
  );
}

function initials(value: string) {
  return value.split(/\s+/).filter(Boolean).slice(0, 2).map((part) => part[0]).join("").toUpperCase() || "P";
}

function messageForError(error: unknown, fallback: string) {
  if (error instanceof PulseApiError) {
    if (error.status === 403) return error.message || translate("messaging:newChat.errorCannotReceive");
    if (error.status === 429) return translate("messaging:newChat.errorRateLimited");
    if (error.status >= 500) return translate("messaging:newChat.errorUnavailable");
    return error.message || fallback;
  }
  return error instanceof Error ? error.message : fallback;
}

const styles = StyleSheet.create({
  keyboard: { flex: 1 },
  page: { flex: 1, gap: 14, padding: 14 },
  intro: { gap: 5 },
  eyebrow: { color: colors.accent, fontSize: 11, fontWeight: "900", letterSpacing: 1.1, textTransform: "uppercase" },
  title: { color: colors.text, fontSize: 25, fontWeight: "900" },
  subtitle: { color: colors.muted, fontSize: 13, lineHeight: 19 },
  searchShell: { alignItems: "center", backgroundColor: colors.surfaceRaised, borderColor: colors.accentStrong, borderRadius: 14, borderWidth: 1, flexDirection: "row", minHeight: 52 },
  searchInput: { color: colors.text, flex: 1, fontSize: 16, minHeight: 50, paddingHorizontal: 15 },
  clearButton: { minHeight: 44, justifyContent: "center", paddingHorizontal: 13 },
  clearText: { color: colors.accent, fontSize: 12, fontWeight: "900" },
  results: { flexGrow: 1, gap: 8, paddingBottom: 30 },
  loading: { alignItems: "center", flexDirection: "row", gap: 9, paddingVertical: 12 },
  loadingText: { color: colors.muted, fontSize: 13, fontWeight: "700" },
  recipient: { alignItems: "center", backgroundColor: colors.surface, borderColor: colors.border, borderRadius: 14, borderWidth: 1, flexDirection: "row", gap: 11, minHeight: 72, padding: 11 },
  recipientPressed: { backgroundColor: colors.signalDim, borderColor: colors.accent },
  disabled: { opacity: 0.55 },
  avatar: { borderRadius: 24, height: 48, width: 48 },
  avatarFallback: { alignItems: "center", backgroundColor: colors.signalSoft, borderColor: colors.accentStrong, borderRadius: 24, borderWidth: 1, height: 48, justifyContent: "center", width: 48 },
  avatarText: { color: colors.accentStrong, fontSize: 15, fontWeight: "900" },
  recipientCopy: { flex: 1, gap: 4, minWidth: 0 },
  nameRow: { alignItems: "center", flexDirection: "row", gap: 7 },
  name: { color: colors.text, flexShrink: 1, fontSize: 15, fontWeight: "900" },
  premium: { backgroundColor: colors.warningSoft, borderRadius: 8, color: colors.warning, fontSize: 9, fontWeight: "900", overflow: "hidden", paddingHorizontal: 6, paddingVertical: 3 },
  handle: { color: colors.muted, fontSize: 12 },
  messageAction: { color: colors.accent, fontSize: 12, fontWeight: "900" },
  errorPanel: { alignItems: "center", backgroundColor: colors.dangerSoft, borderColor: colors.danger, borderRadius: 12, borderWidth: 1, flexDirection: "row", gap: 8, padding: 10 },
  errorText: { color: colors.text, flex: 1, fontSize: 12, lineHeight: 17 },
  retryButton: { alignSelf: "center", backgroundColor: colors.signalDim, borderColor: colors.accent, borderRadius: 10, borderWidth: 1, marginTop: 8, paddingHorizontal: 12, paddingVertical: 8 },
  retryText: { color: colors.accent, fontSize: 12, fontWeight: "900" }
});
