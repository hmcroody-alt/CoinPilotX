import { NativeStackScreenProps } from "@react-navigation/native-stack";
import * as Clipboard from "expo-clipboard";
import { useEffect, useRef, useState } from "react";
import {
  ActivityIndicator,
  Image,
  Pressable,
  ScrollView,
  StyleSheet,
  Text,
  TextInput,
  View
} from "react-native";
import QRCode from "react-native-qrcode-svg";
import {
  MessengerUserSearchResult,
  openDirectConversation,
  searchMessengerUsers,
  sendConversationMessage
} from "../api/messenger";
import { RootStackParamList } from "../navigation/types";
import { buildNativeSharePayload, openSystemShare } from "../sharing/nativeShare";
import { colors } from "../theme/colors";

type Props = NativeStackScreenProps<RootStackParamList, "PulseShare">;

export function PulseShareScreen({ route, navigation }: Props) {
  const metadata = route.params;
  const payload = buildNativeSharePayload(metadata);
  const [query, setQuery] = useState("");
  const [people, setPeople] = useState<MessengerUserSearchResult[]>([]);
  const [searching, setSearching] = useState(false);
  const [sendingUserId, setSendingUserId] = useState(0);
  const [showPeople, setShowPeople] = useState(false);
  const [showQr, setShowQr] = useState(false);
  const [notice, setNotice] = useState("");
  const sequence = useRef(0);

  useEffect(() => {
    const clean = query.trim();
    const current = ++sequence.current;
    if (!showPeople || clean.length < 2) {
      setPeople([]);
      setSearching(false);
      return;
    }
    setSearching(true);
    const timer = setTimeout(() => {
      searchMessengerUsers(clean)
        .then((results) => {
          if (sequence.current === current) setPeople(results);
        })
        .catch((error) => {
          if (sequence.current === current) {
            setPeople([]);
            setNotice(error instanceof Error ? error.message : "People search could not load.");
          }
        })
        .finally(() => {
          if (sequence.current === current) setSearching(false);
        });
    }, 280);
    return () => clearTimeout(timer);
  }, [query, showPeople]);

  async function copyLink() {
    await Clipboard.setStringAsync(metadata.url);
    setNotice("Link copied.");
  }

  async function sendToPerson(person: MessengerUserSearchResult) {
    if (sendingUserId) return;
    setSendingUserId(person.user_id);
    setNotice("");
    try {
      const conversation = await openDirectConversation(person);
      await sendConversationMessage(conversation.conversation_id, {
        body: payload.message,
        message_type: "text",
        client_message_id: shareClientId(metadata.kind, metadata.url, person.user_id),
        local_created_at: new Date().toISOString()
      });
      setNotice(`Sent to ${person.display_name}.`);
    } catch (error) {
      setNotice(error instanceof Error ? error.message : "PulseSoc share could not be sent.");
    } finally {
      setSendingUserId(0);
    }
  }

  return (
    <ScrollView style={styles.root} contentContainerStyle={styles.content} keyboardShouldPersistTaps="handled">
      <View style={styles.preview}>
        {metadata.previewImageUrl ? <Image source={{ uri: metadata.previewImageUrl }} style={styles.previewImage} /> : null}
        <Text style={styles.eyebrow}>PULSESOC {metadata.kind.toUpperCase()}</Text>
        <Text style={styles.title}>{payload.title}</Text>
        {metadata.author ? <Text style={styles.author}>By {metadata.author}</Text> : null}
        {metadata.description ? <Text style={styles.description} numberOfLines={4}>{metadata.description}</Text> : null}
        <Text style={styles.url} numberOfLines={2}>{metadata.url}</Text>
      </View>

      {notice ? <Text accessibilityLiveRegion="polite" style={styles.notice}>{notice}</Text> : null}

      <View style={styles.actionGrid}>
        <ShareAction label="Send in PulseSoc" detail="Messenger" onPress={() => {
          setShowPeople((value) => !value);
          setShowQr(false);
          setNotice("");
        }} />
        <ShareAction label="Copy link" detail="Canonical URL" onPress={() => copyLink().catch(() => setNotice("Link could not be copied."))} />
        <ShareAction label="QR code" detail="Scan to open" onPress={() => {
          setShowQr((value) => !value);
          setShowPeople(false);
          setNotice("");
        }} />
        <ShareAction label="More apps" detail="AirDrop, SMS, email" onPress={() => openSystemShare(metadata).catch(() => setNotice("System share sheet could not open."))} />
      </View>

      {showQr ? (
        <View style={styles.qrPanel}>
          <View testID="pulse-share-qr" style={styles.qrSurface}>
            <QRCode value={metadata.url} size={220} backgroundColor="#FFFFFF" color="#06101B" />
          </View>
          <Text style={styles.qrTitle}>Scan to open in PulseSoc</Text>
          <Text style={styles.qrText}>This QR code contains only the canonical PulseSoc link.</Text>
        </View>
      ) : null}

      {showPeople ? (
        <View style={styles.peoplePanel}>
          <Text style={styles.sectionTitle}>Send with Messenger</Text>
          <TextInput
            accessibilityLabel="Search PulseSoc recipients"
            autoCapitalize="none"
            autoCorrect={false}
            placeholder="Name, username, or Pulse ID"
            placeholderTextColor={colors.muted}
            value={query}
            onChangeText={setQuery}
            style={styles.searchInput}
          />
          {searching ? <View style={styles.searching}><ActivityIndicator color={colors.accent} /><Text style={styles.helper}>Searching PulseSoc</Text></View> : null}
          {!searching && query.trim().length >= 2 && !people.length ? <Text style={styles.helper}>No recipients found.</Text> : null}
          {people.map((person) => (
            <Pressable
              key={person.user_id}
              accessibilityRole="button"
              accessibilityLabel={`Send to ${person.display_name}`}
              disabled={sendingUserId > 0}
              onPress={() => sendToPerson(person)}
              style={[styles.person, sendingUserId > 0 && sendingUserId !== person.user_id ? styles.disabled : undefined]}
            >
              {person.avatar_url ? <Image source={{ uri: person.avatar_url }} style={styles.avatar} /> : <View style={styles.avatarFallback}><Text style={styles.avatarText}>{initials(person.display_name)}</Text></View>}
              <View style={styles.personCopy}>
                <Text style={styles.personName}>{person.display_name}</Text>
                <Text style={styles.personHandle}>{person.public_pulse_id || person.public_player_id || "PulseSoc member"}</Text>
              </View>
              {sendingUserId === person.user_id ? <ActivityIndicator color={colors.accent} /> : <Text style={styles.sendLabel}>Send</Text>}
            </Pressable>
          ))}
        </View>
      ) : null}

      <Pressable accessibilityRole="button" style={styles.done} onPress={() => navigation.goBack()}>
        <Text style={styles.doneText}>Done</Text>
      </Pressable>
    </ScrollView>
  );
}

function ShareAction({ label, detail, onPress }: { label: string; detail: string; onPress: () => void }) {
  return (
    <Pressable accessibilityRole="button" accessibilityLabel={`${label}, ${detail}`} onPress={onPress} style={styles.action}>
      <Text style={styles.actionLabel}>{label}</Text>
      <Text style={styles.actionDetail}>{detail}</Text>
    </Pressable>
  );
}

function shareClientId(kind: string, url: string, recipientId: number) {
  const clean = url.replace(/[^a-z0-9]/gi, "").slice(-36);
  return `native-share-${kind}-${recipientId}-${clean}-${Date.now()}`.slice(0, 120);
}

function initials(value: string) {
  return value.split(/\s+/).filter(Boolean).slice(0, 2).map((part) => part[0]).join("").toUpperCase() || "P";
}

const styles = StyleSheet.create({
  root: { backgroundColor: colors.background, flex: 1 },
  content: { gap: 16, padding: 16, paddingBottom: 44 },
  preview: { backgroundColor: colors.surface, borderColor: colors.border, borderRadius: 20, borderWidth: 1, gap: 7, overflow: "hidden", padding: 16 },
  previewImage: { borderRadius: 14, height: 180, marginBottom: 6, width: "100%" },
  eyebrow: { color: colors.accent, fontSize: 10, fontWeight: "900", letterSpacing: 1.3 },
  title: { color: colors.text, fontSize: 22, fontWeight: "900" },
  author: { color: colors.accentStrong, fontSize: 13, fontWeight: "800" },
  description: { color: colors.muted, fontSize: 14, lineHeight: 20 },
  url: { color: colors.disabled, fontSize: 11, lineHeight: 16 },
  notice: { backgroundColor: colors.signalDim, borderColor: colors.accentStrong, borderRadius: 12, borderWidth: 1, color: colors.text, fontSize: 13, padding: 11 },
  actionGrid: { flexDirection: "row", flexWrap: "wrap", gap: 10 },
  action: { backgroundColor: colors.surfaceRaised, borderColor: colors.border, borderRadius: 16, borderWidth: 1, flexBasis: "47%", flexGrow: 1, gap: 4, minHeight: 82, padding: 14 },
  actionLabel: { color: colors.text, fontSize: 14, fontWeight: "900" },
  actionDetail: { color: colors.muted, fontSize: 11, lineHeight: 16 },
  qrPanel: { alignItems: "center", backgroundColor: colors.surface, borderColor: colors.border, borderRadius: 20, borderWidth: 1, gap: 8, padding: 18 },
  qrSurface: { backgroundColor: "#FFFFFF", borderRadius: 16, padding: 14 },
  qrTitle: { color: colors.text, fontSize: 16, fontWeight: "900" },
  qrText: { color: colors.muted, fontSize: 12, textAlign: "center" },
  peoplePanel: { backgroundColor: colors.surface, borderColor: colors.border, borderRadius: 20, borderWidth: 1, gap: 10, padding: 14 },
  sectionTitle: { color: colors.text, fontSize: 17, fontWeight: "900" },
  searchInput: { backgroundColor: colors.surfaceRaised, borderColor: colors.accentStrong, borderRadius: 13, borderWidth: 1, color: colors.text, minHeight: 48, paddingHorizontal: 13 },
  searching: { alignItems: "center", flexDirection: "row", gap: 8 },
  helper: { color: colors.muted, fontSize: 12 },
  person: { alignItems: "center", borderColor: colors.border, borderRadius: 14, borderWidth: 1, flexDirection: "row", gap: 10, minHeight: 64, padding: 9 },
  disabled: { opacity: 0.45 },
  avatar: { borderRadius: 21, height: 42, width: 42 },
  avatarFallback: { alignItems: "center", backgroundColor: colors.signalDim, borderRadius: 21, height: 42, justifyContent: "center", width: 42 },
  avatarText: { color: colors.accent, fontSize: 13, fontWeight: "900" },
  personCopy: { flex: 1, gap: 3 },
  personName: { color: colors.text, fontSize: 14, fontWeight: "900" },
  personHandle: { color: colors.muted, fontSize: 11 },
  sendLabel: { color: colors.accent, fontSize: 12, fontWeight: "900" },
  done: { alignItems: "center", backgroundColor: colors.accent, borderRadius: 14, minHeight: 50, justifyContent: "center" },
  doneText: { color: colors.background, fontSize: 14, fontWeight: "900" }
});
