import AsyncStorage from "@react-native-async-storage/async-storage";
import { useMemo, useState } from "react";
import { Modal, Pressable, ScrollView, Share, StyleSheet, Text, TextInput, View } from "react-native";
import { MessengerMessage } from "../api/messenger";
import { colors } from "../theme/colors";
import { logiNexus } from "../theme/logiNexus";
import { formatFileSize } from "../utils/format";
import { PulseCommandPanel } from "./PulseCommand";

type Props = {
  visible: boolean;
  conversationId: number;
  title: string;
  messages: MessengerMessage[];
  onClose: () => void;
  onOpenSafety: (section: "reports" | "blocks") => void;
};

type Section = "conversation" | "notifications" | "appearance" | "privacy" | "media" | "productivity" | "storage" | "security" | "accessibility" | "danger";

const SECTION_LABELS: Array<{ key: Section; label: string }> = [
  { key: "conversation", label: "Conversation" },
  { key: "notifications", label: "Notifications" },
  { key: "appearance", label: "Appearance" },
  { key: "privacy", label: "Privacy" },
  { key: "media", label: "Media" },
  { key: "productivity", label: "Productivity" },
  { key: "storage", label: "Storage" },
  { key: "security", label: "Security" },
  { key: "accessibility", label: "Accessibility" },
  { key: "danger", label: "Danger Zone" }
];

export function ConversationControlCenter({ visible, conversationId, title, messages, onClose, onOpenSafety }: Props) {
  const [query, setQuery] = useState("");
  const [open, setOpen] = useState<Section>("conversation");
  const [notice, setNotice] = useState("");
  const media = useMemo(() => messages.filter((message) => Boolean(message.media_url)), [messages]);
  const files = useMemo(() => media.filter((message) => !["image", "gif", "video", "voice", "audio"].includes(String(message.message_type || "").toLowerCase())), [media]);
  const mediaBytes = useMemo(() => media.reduce((sum, message) => sum + Number(message.file_size || 0), 0), [media]);
  const filteredSections = SECTION_LABELS.filter((section) => !query.trim() || section.label.toLowerCase().includes(query.trim().toLowerCase()));

  async function exportConversation() {
    const transcript = messages
      .filter((message) => !message.deleted_at)
      .map((message) => `${message.is_mine ? "You" : message.sender_display_name || "PulseSoc member"}: ${message.body || `[${message.message_type || "attachment"}]`}`)
      .join("\n");
    await Share.share({ title: `${title} conversation`, message: transcript || "No messages to export." });
  }

  async function clearLocalCache() {
    await AsyncStorage.removeItem(`pulsesoc.native.messenger.messages.${conversationId}`);
    setNotice("Local conversation cache cleared. Server messages were not deleted.");
  }

  return (
    <Modal transparent animationType="slide" visible={visible} onRequestClose={onClose}>
      <View style={styles.backdrop}>
        <PulseCommandPanel style={styles.sheet}>
          <View style={styles.handle} />
          <View style={styles.header}>
            <View style={styles.gear}><Text style={styles.gearText}>⚙</Text></View>
            <View style={styles.headerCopy}>
              <Text style={styles.title}>Conversation Control Center</Text>
              <Text style={styles.subtitle} numberOfLines={1}>{title} · Native controls backed by current conversation state</Text>
            </View>
            <Pressable accessibilityRole="button" accessibilityLabel="Close Conversation Control Center" style={styles.close} onPress={onClose}><Text style={styles.closeText}>×</Text></Pressable>
          </View>
          <TextInput accessibilityLabel="Search conversation settings" value={query} onChangeText={setQuery} placeholder="Search settings" placeholderTextColor={colors.muted} style={styles.search} />
          <ScrollView contentContainerStyle={styles.content} keyboardShouldPersistTaps="handled">
            {filteredSections.length ? filteredSections.map((section) => (
              <View key={section.key}>
                <Pressable accessibilityRole="button" accessibilityState={{ expanded: open === section.key }} style={[styles.sectionHeader, open === section.key && styles.sectionHeaderActive]} onPress={() => setOpen(section.key)}>
                  <Text style={[styles.sectionTitle, open === section.key && styles.sectionTitleActive]}>{section.label}</Text>
                  <Text style={styles.chevron}>{open === section.key ? "⌃" : "⌄"}</Text>
                </Pressable>
                {open === section.key ? <SectionBody section={section.key} messageCount={messages.length} mediaCount={media.length} fileCount={files.length} mediaBytes={mediaBytes} onExport={() => exportConversation().catch(() => setNotice("Conversation export could not open."))} onClear={() => clearLocalCache().catch(() => setNotice("Local cache could not be cleared."))} onSafety={onOpenSafety} /> : null}
              </View>
            )) : <Text style={styles.empty}>No settings match “{query}”.</Text>}
            {notice ? <Text accessibilityLiveRegion="polite" style={styles.notice}>{notice}</Text> : null}
          </ScrollView>
        </PulseCommandPanel>
      </View>
    </Modal>
  );
}

function SectionBody({ section, messageCount, mediaCount, fileCount, mediaBytes, onExport, onClear, onSafety }: { section: Section; messageCount: number; mediaCount: number; fileCount: number; mediaBytes: number; onExport: () => void; onClear: () => void; onSafety: (section: "reports" | "blocks") => void }) {
  if (section === "conversation") return <View style={styles.rows}><Metric label="Messages" value={String(messageCount)} /><Metric label="Shared media" value={String(mediaCount)} /><Action label="Export Chat" detail="Uses the native share sheet" onPress={onExport} /></View>;
  if (section === "media") return <View style={styles.rows}><Metric label="Shared media" value={String(mediaCount)} /><Metric label="Shared files" value={String(fileCount)} /><Boundary text="Media downloads continue to use authenticated message URLs and the existing native viewer." /></View>;
  if (section === "storage") return <View style={styles.rows}><Metric label="Known attachment size" value={formatFileSize(mediaBytes)} /><Action label="Clear Local Cache" detail="Keeps server messages intact" onPress={onClear} /></View>;
  if (section === "security") return <View style={styles.rows}><Boundary text="Transport and session security remain server-authoritative. PulseSoc does not claim end-to-end encryption here." /><Action label="Report Conversation" detail="Open Trust & Safety" onPress={() => onSafety("reports")} /><Action label="Blocked Users" detail="Manage blocked accounts" onPress={() => onSafety("blocks")} /></View>;
  if (section === "danger") return <View style={styles.rows}><Action label="Report Conversation" detail="Send to Trust & Safety" danger onPress={() => onSafety("reports")} /><Action label="Block User" detail="Review and confirm in Safety Hub" danger onPress={() => onSafety("blocks")} /></View>;
  if (section === "notifications") return <Boundary text="Conversation notification overrides are not exposed by the inspected Messenger API. System and global PulseSoc notification controls remain authoritative." />;
  if (section === "privacy") return <Boundary text="Read receipts, typing, online status, and previews continue to follow production privacy and authorization rules." />;
  if (section === "appearance") return <Boundary text="This conversation uses the production Pulse Command geometry and system accessibility preferences. Unsupported per-chat themes are not simulated." />;
  if (section === "productivity") return <Boundary text="Pinning is server-backed where exposed. Archive, reminders, notes, and tasks require production contracts before native controls can be enabled." />;
  return <Boundary text="Dynamic Type, VoiceOver, increased contrast, and Reduced Motion follow native system settings." />;
}

function Action({ label, detail, onPress, danger = false }: { label: string; detail: string; onPress: () => void; danger?: boolean }) {
  return <Pressable accessibilityRole="button" style={styles.row} onPress={onPress}><View style={styles.rowCopy}><Text style={[styles.rowLabel, danger && styles.danger]}>{label}</Text><Text style={styles.rowDetail}>{detail}</Text></View><Text style={styles.chevron}>›</Text></Pressable>;
}

function Metric({ label, value }: { label: string; value: string }) {
  return <View style={styles.row}><Text style={styles.rowLabel}>{label}</Text><Text style={styles.metric}>{value}</Text></View>;
}

function Boundary({ text }: { text: string }) {
  return <View style={styles.boundary}><Text style={styles.boundaryText}>{text}</Text></View>;
}

const styles = StyleSheet.create({
  backdrop: { backgroundColor: "rgba(0,0,0,0.58)", flex: 1, justifyContent: "flex-end" },
  sheet: { borderBottomLeftRadius: 0, borderBottomRightRadius: 0, maxHeight: "88%", padding: 12 },
  handle: { alignSelf: "center", backgroundColor: colors.border, borderRadius: 3, height: 4, marginBottom: 10, width: 42 },
  header: { alignItems: "center", flexDirection: "row", gap: 9 },
  gear: { alignItems: "center", backgroundColor: "rgba(47,225,180,0.12)", borderColor: colors.accent, borderRadius: 18, borderWidth: 1, height: 36, justifyContent: "center", width: 36 },
  gearText: { color: colors.accent, fontSize: 17 },
  headerCopy: { flex: 1, minWidth: 0 },
  title: { color: colors.text, fontSize: 17, fontWeight: "900" },
  subtitle: { color: colors.muted, fontSize: 10, marginTop: 2 },
  close: { alignItems: "center", borderColor: colors.border, borderRadius: 17, borderWidth: 1, height: 34, justifyContent: "center", width: 34 },
  closeText: { color: colors.text, fontSize: 22 },
  search: { backgroundColor: "rgba(3,7,18,0.72)", borderColor: colors.border, borderRadius: 14, borderWidth: 1, color: colors.text, marginTop: 10, minHeight: 42, paddingHorizontal: 12 },
  content: { gap: 6, paddingBottom: 30, paddingTop: 9 },
  sectionHeader: { alignItems: "center", borderColor: colors.border, borderRadius: 12, borderWidth: StyleSheet.hairlineWidth, flexDirection: "row", minHeight: 44, paddingHorizontal: 12 },
  sectionHeaderActive: { backgroundColor: "rgba(47,225,180,0.08)", borderColor: colors.accent },
  sectionTitle: { color: colors.text, flex: 1, fontSize: 13, fontWeight: "900" },
  sectionTitleActive: { color: colors.accent },
  chevron: { color: colors.muted, fontSize: 17 },
  rows: { gap: 6, padding: 7 },
  row: { alignItems: "center", backgroundColor: "rgba(255,255,255,0.035)", borderRadius: 11, flexDirection: "row", minHeight: 48, paddingHorizontal: 11, paddingVertical: 8 },
  rowCopy: { flex: 1 },
  rowLabel: { color: colors.text, flex: 1, fontSize: 13, fontWeight: "800" },
  rowDetail: { color: colors.muted, fontSize: 10, marginTop: 2 },
  metric: { color: colors.accent, fontSize: 12, fontWeight: "900" },
  boundary: { backgroundColor: "rgba(97,216,255,0.06)", borderColor: "rgba(97,216,255,0.18)", borderRadius: 11, borderWidth: 1, margin: 7, padding: 10 },
  boundaryText: { color: colors.muted, fontSize: 11, lineHeight: 16 },
  danger: { color: colors.danger },
  empty: { color: colors.muted, padding: 18, textAlign: "center" },
  notice: { color: colors.accent, fontSize: 11, padding: 10, textAlign: "center" }
});
