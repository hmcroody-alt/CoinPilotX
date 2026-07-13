import AsyncStorage from "@react-native-async-storage/async-storage";
import { useEffect, useMemo, useState } from "react";
import { Alert, Modal, Pressable, ScrollView, Share, StyleSheet, Switch, Text, TextInput, View } from "react-native";
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
  connected?: boolean;
  onClose: () => void;
  onOpenSafety: (section: "reports" | "blocks") => void;
};

type Section = "conversation" | "notifications" | "appearance" | "privacy" | "media" | "security" | "productivity" | "storage" | "accessibility" | "danger";
type LocalPreferences = { reduceMotion: boolean; highContrast: boolean; haptics: boolean; largeText: boolean };
type RowSpec = { label: string; icon: string; detail?: string; value?: string; action?: "export" | "clear-cache" | "report" | "block" | "local"; localKey?: keyof LocalPreferences; danger?: boolean; destructive?: boolean; disabled?: boolean };

const SECTION_META: Array<{ key: Section; label: string; icon: string; subtitle: string }> = [
  { key: "conversation", label: "Conversation", icon: "💬", subtitle: "Info, members, media and search." },
  { key: "notifications", label: "Notifications", icon: "🔔", subtitle: "Alerts, sounds, previews and badges." },
  { key: "appearance", label: "Appearance", icon: "🖌", subtitle: "Themes, colors, density and motion." },
  { key: "privacy", label: "Privacy", icon: "🔒", subtitle: "Visibility and private conversation behavior." },
  { key: "media", label: "Media", icon: "🖼", subtitle: "Downloads, uploads, links and files." },
  { key: "security", label: "Security", icon: "🛡", subtitle: "Safety, reports, sessions and trust." },
  { key: "productivity", label: "Productivity", icon: "✓", subtitle: "Pins, archive, reminders and tasks." },
  { key: "storage", label: "Storage", icon: "◉", subtitle: "Conversation size, media and cache." },
  { key: "accessibility", label: "Accessibility", icon: "♿", subtitle: "Display, motion, audio and haptics." },
  { key: "danger", label: "Danger Zone", icon: "!", subtitle: "Destructive actions require confirmation." }
];

const DEFAULT_PREFS: LocalPreferences = { reduceMotion: false, highContrast: false, haptics: true, largeText: false };

export function ConversationControlCenter({ visible, conversationId, title, messages, connected = true, onClose, onOpenSafety }: Props) {
  const [query, setQuery] = useState("");
  const [open, setOpen] = useState<Section[]>(["conversation"]);
  const [notice, setNotice] = useState("");
  const [prefs, setPrefs] = useState<LocalPreferences>(DEFAULT_PREFS);
  const preferenceKey = `pulsesoc.native.messenger.control-center.${conversationId}`;
  const media = useMemo(() => messages.filter((message) => Boolean(message.media_url)), [messages]);
  const images = useMemo(() => media.filter((message) => ["image", "gif"].includes(String(message.message_type || "").toLowerCase())), [media]);
  const videos = useMemo(() => media.filter((message) => String(message.message_type || "").toLowerCase() === "video"), [media]);
  const voices = useMemo(() => media.filter((message) => ["voice", "audio"].includes(String(message.message_type || "").toLowerCase())), [media]);
  const files = useMemo(() => media.filter((message) => !["image", "gif", "video", "voice", "audio"].includes(String(message.message_type || "").toLowerCase())), [media]);
  const mediaBytes = useMemo(() => media.reduce((sum, message) => sum + Number(message.file_size || 0), 0), [media]);
  const unread = useMemo(() => messages.filter((message) => !message.is_mine && !message.seen_at).length, [messages]);
  const participantCount = useMemo(() => new Set(messages.map((message) => message.sender_user_id || message.sender_id).filter(Boolean)).size + (messages.some((message) => message.is_mine) ? 1 : 0), [messages]);

  useEffect(() => {
    if (!visible) return;
    AsyncStorage.getItem(preferenceKey).then((saved) => saved && setPrefs({ ...DEFAULT_PREFS, ...JSON.parse(saved) })).catch(() => undefined);
  }, [preferenceKey, visible]);

  const rows = useMemo(() => buildRows({ messages: messages.length, media: media.length, images: images.length, videos: videos.length, voices: voices.length, files: files.length, mediaBytes, unread }), [files.length, images.length, media.length, mediaBytes, messages.length, unread, videos.length, voices.length]);
  const normalizedQuery = query.trim().toLowerCase();
  const sections = SECTION_META.map((section) => ({ ...section, rows: rows[section.key].filter((row) => !normalizedQuery || `${section.label} ${section.subtitle} ${row.label} ${row.detail || ""}`.toLowerCase().includes(normalizedQuery)) }))
    .filter((section) => !normalizedQuery || section.rows.length > 0);

  async function exportConversation() {
    const transcript = messages.filter((message) => !message.deleted_at).map((message) => `${message.is_mine ? "You" : message.sender_display_name || "PulseSoc member"}: ${message.body || `[${message.message_type || "attachment"}]`}`).join("\n");
    await Share.share({ title: `${title} conversation`, message: transcript || "No messages to export." });
  }

  async function clearLocalCache() {
    await AsyncStorage.removeItem(`pulsesoc.native.messenger.messages.${conversationId}`);
    setNotice("Local cache cleared. Server messages and remote media were not deleted.");
  }

  async function setLocalPreference(key: keyof LocalPreferences, value: boolean) {
    const next = { ...prefs, [key]: value };
    setPrefs(next);
    await AsyncStorage.setItem(preferenceKey, JSON.stringify(next));
    setNotice(`${preferenceLabel(key)} saved on this device.`);
  }

  function perform(row: RowSpec) {
    if (row.disabled) return setNotice(`${row.label} is unavailable because the inspected Messenger API does not expose a safe production contract.`);
    if (row.action === "export") return exportConversation().catch(() => setNotice("Conversation export could not open."));
    if (row.action === "clear-cache") return Alert.alert("Clear local media cache?", "This removes cached conversation data from this device. It does not delete messages or remote media.", [{ text: "Cancel", style: "cancel" }, { text: "Clear cache", style: "destructive", onPress: () => clearLocalCache().catch(() => setNotice("Local cache could not be cleared.")) }]);
    if (row.action === "report") return onOpenSafety("reports");
    if (row.action === "block") return onOpenSafety("blocks");
    setNotice(row.detail || `${row.label} is not available.`);
  }

  return (
    <Modal transparent animationType="slide" visible={visible} onRequestClose={onClose} statusBarTranslucent>
      <View style={styles.backdrop}>
        <PulseCommandPanel style={styles.sheet}>
          <View style={styles.handle} />
          <View style={styles.header}>
            <View style={styles.gear}><Text style={styles.gearText}>⚙</Text></View>
            <View style={styles.headerCopy}><Text style={styles.title}>Conversation Control Center</Text><Text style={styles.subtitle}>Manage this chat experience.</Text></View>
            <Pressable accessibilityRole="button" accessibilityLabel="Close Conversation Control Center" style={styles.close} onPress={onClose}><Text style={styles.closeText}>×</Text></Pressable>
          </View>
          <ScrollView contentContainerStyle={styles.content} keyboardShouldPersistTaps="handled" stickyHeaderIndices={[1]}>
            <View style={styles.dashboard}>
              <View style={styles.contactLine}><View style={styles.avatar}><Text style={styles.avatarText}>{initials(title)}</Text></View><View style={styles.contactCopy}><Text style={styles.contactName}>{title}</Text><Text style={styles.online}>{connected ? "Online" : "Reconnecting"} · Conversation</Text></View></View>
              <View style={styles.quickGrid}><Quick label="Search" icon="⌕" onPress={() => setQuery("message")} /><Quick label="Shared Media" icon="▧" onPress={() => setOpen(["conversation", "media"])} /><Quick label="Members" icon="♟" onPress={() => setOpen(["conversation"])} /></View>
              <View style={styles.actionGrid}><Quick label="Search" icon="⌕" onPress={() => setQuery("search")} /><Quick label="Call" icon="☎" disabled /><Quick label="Video" icon="▣" disabled /><Quick label="Mute" icon="🔕" disabled /><Quick label="Pin" icon="📌" disabled /><Quick label="Archive" icon="▤" disabled /></View>
              <View style={styles.metrics}><DashboardMetric label="Protection" value="TLS session" /><DashboardMetric label="Members" value={participantCount ? String(participantCount) : "Unknown"} /><DashboardMetric label="Media Files" value={String(media.length)} /><DashboardMetric label="Known Storage" value={formatFileSize(mediaBytes)} /><DashboardMetric label="Unread" value={String(unread)} /><DashboardMetric label="Connection" value={connected ? "Connected" : "Reconnecting"} /></View>
            </View>
            <View style={styles.searchWrap}><Text style={styles.searchIcon}>⌕</Text><TextInput accessibilityLabel="Search conversation settings" value={query} onChangeText={setQuery} placeholder="Search settings..." placeholderTextColor={colors.muted} style={styles.search} /></View>
            {sections.length ? sections.map((section) => {
              const expanded = normalizedQuery ? true : open.includes(section.key);
              return <View key={section.key} style={[styles.section, section.key === "danger" && styles.dangerSection]}>
                <Pressable accessibilityRole="button" accessibilityState={{ expanded }} style={styles.sectionHeader} onPress={() => setOpen((current) => current.includes(section.key) ? current.filter((item) => item !== section.key) : [...current, section.key])}>
                  <View style={styles.sectionIcon}><Text style={styles.sectionIconText}>{section.icon}</Text></View><View style={styles.sectionCopy}><Text style={styles.sectionTitle}>{section.label}</Text><Text style={styles.sectionSubtitle}>{section.subtitle}</Text></View><Text style={styles.chevron}>{expanded ? "⌃" : "⌄"}</Text>
                </Pressable>
                {expanded ? section.rows.map((row) => <SettingRow key={`${section.key}-${row.label}`} row={row} prefValue={row.localKey ? prefs[row.localKey] : undefined} onToggle={row.localKey ? (value) => setLocalPreference(row.localKey!, value).catch(() => setNotice("Preference could not be saved.")) : undefined} onPress={() => perform(row)} />) : null}
              </View>;
            }) : <Text style={styles.empty}>No settings match “{query}”.</Text>}
            {notice ? <Pressable accessibilityRole="button" accessibilityLabel="Dismiss notice" onPress={() => setNotice("")}><Text accessibilityLiveRegion="polite" style={styles.notice}>{notice}</Text></Pressable> : null}
          </ScrollView>
        </PulseCommandPanel>
      </View>
    </Modal>
  );
}

function buildRows(v: { messages: number; media: number; images: number; videos: number; voices: number; files: number; mediaBytes: number; unread: number }): Record<Section, RowSpec[]> {
  const unsupported = "Requires a production-backed Messenger contract.";
  return {
    conversation: [row("View Members", "👥", unsupported), row("Shared Media", "🖼", `${v.media} current conversation attachments`), row("Pinned Messages", "📌", unsupported), row("Search Chat", "⌕", unsupported), row("Message Stats", "▥", `${v.messages} loaded messages`), row("Media Storage", "◉", `${formatFileSize(v.mediaBytes)} from known attachment metadata`), { ...row("Export Chat", "⇧", "Open native share sheet"), action: "export", disabled: false }],
    notifications: [row("Mute Conversation", "🔕", unsupported), row("Notification Sound", "♪", unsupported), row("Show on Lock Screen", "▣", unsupported), row("Show Message Preview", "◉", unsupported), row("Mention Notifications", "@", unsupported), row("Reaction Notifications", "✦", unsupported), row("Typing Notifications", "…", unsupported), row("Read Receipt Notifications", "✓", unsupported), row("More Notification Settings", "⚙", "Use global PulseSoc notification settings")],
    appearance: [row("Theme", "◌", "System Pulse Command theme"), row("Wallpaper", "▧", "Production background; no per-chat API"), row("Bubble Color", "●", "Production contrast-safe colors"), row("Font Size", "Aa", "Follows Dynamic Type"), row("Chat Density", "↕", "Balanced production geometry"), row("Animation Level", "✺", "Follows system Reduce Motion"), local("Reduce Particles", "·", "reduceMotion"), local("High Contrast", "◐", "highContrast")],
    privacy: [row("Read Receipts", "✓✓", unsupported), row("Typing Indicator", "…", unsupported), row("Online Status", "●", unsupported), row("Last Seen", "◷", unsupported), row("Show Message Preview", "◉", unsupported)],
    media: [row("Auto Download Photos", "▧", unsupported), row("Auto Download Videos", "▶", unsupported), row("Auto Download Voice Messages", "🎙", unsupported), row("Upload Quality", "HD", "Existing upload pipeline is authoritative"), row("Auto Save Camera Photos", "◎", unsupported), { ...row("Clear Media Cache", "⌫", "Local cache only; server messages remain"), action: "clear-cache", disabled: false }, row("Shared Links", "↗", unsupported), row("Shared Files", "▤", `${v.files} current conversation files`)],
    security: [row("Encryption Status", "🛡", "TLS transport/session protection; end-to-end encryption is not claimed"), row("Verify Contact", "◇", unsupported), row("Trusted Devices", "▣", unsupported), row("Active Sessions", "◉", "Open account security for real sessions"), row("Security Log", "▤", unsupported), { ...row("Report Conversation", "!", "Open Trust & Safety"), action: "report", disabled: false }, { ...row("Block User", "⊘", "Review and confirm in Safety Hub"), action: "block", disabled: false }],
    productivity: [row("Pin Conversation", "📌", unsupported), row("Archive Conversation", "▤", unsupported), row("Mark Unread", "◌", unsupported), row("Favorite Conversation", "★", unsupported), row("Reminder", "⏱", unsupported), row("Create Note", "✎", unsupported), row("Create Task", "☑", unsupported)],
    storage: [{ ...row("Conversation Size", "◉", "Known attachment bytes only"), value: formatFileSize(v.mediaBytes), disabled: false }, { ...row("Photos", "▧", "Loaded conversation"), value: String(v.images), disabled: false }, { ...row("Videos", "▶", "Loaded conversation"), value: String(v.videos), disabled: false }, { ...row("Voice Messages", "🎙", "Loaded conversation"), value: String(v.voices), disabled: false }, { ...row("Files", "▤", "Loaded conversation"), value: String(v.files), disabled: false }, { ...row("Clear Cache", "⌫", "Local cache only; no remote deletion"), action: "clear-cache", disabled: false }],
    accessibility: [local("Large Text", "Aa", "largeText"), local("Reduce Motion", "↘", "reduceMotion"), local("High Contrast", "◐", "highContrast"), local("Haptic Feedback", "✦", "haptics")],
    danger: [{ ...row("Clear Conversation", "⌫", "Server semantics unavailable; no action performed"), danger: true, destructive: true }, { ...row("Delete Conversation", "×", "Server semantics unavailable; no action performed"), danger: true, destructive: true }, { ...row("Block User", "⊘", "Review and confirm in Safety Hub"), action: "block", danger: true, disabled: false }, { ...row("Report Spam", "!", "Open Trust & Safety"), action: "report", danger: true, disabled: false }, { ...row("Delete Media", "⌫", "Remote deletion semantics unavailable; no action performed"), danger: true, destructive: true }, { ...row("Reset Conversation Settings", "↻", "No server-backed per-chat settings to reset"), danger: true, destructive: true }]
  };
}

function row(label: string, icon: string, detail: string): RowSpec { return { label, icon, detail, disabled: true }; }
function local(label: string, icon: string, localKey: keyof LocalPreferences): RowSpec { return { label, icon, detail: "Saved on this device", action: "local", localKey, disabled: false }; }
function preferenceLabel(key: keyof LocalPreferences) { return ({ reduceMotion: "Reduce Motion", highContrast: "High Contrast", haptics: "Haptic Feedback", largeText: "Large Text" })[key]; }
function initials(value: string) { return value.split(/\s+/).filter(Boolean).slice(0, 2).map((part) => part[0]?.toUpperCase()).join("") || "PC"; }

function Quick({ label, icon, onPress, disabled = false }: { label: string; icon: string; onPress?: () => void; disabled?: boolean }) { return <Pressable accessibilityRole="button" accessibilityState={{ disabled }} style={({ pressed }) => [styles.quick, disabled && styles.disabled, pressed && !disabled && styles.pressed]} disabled={disabled} onPress={onPress}><Text style={styles.quickIcon}>{icon}</Text><Text style={styles.quickLabel}>{label}</Text></Pressable>; }
function DashboardMetric({ label, value }: { label: string; value: string }) { return <View style={styles.dashboardMetric}><Text style={styles.metricLabel}>{label}</Text><Text style={styles.metricValue} numberOfLines={1}>{value}</Text></View>; }
function SettingRow({ row, prefValue, onToggle, onPress }: { row: RowSpec; prefValue?: boolean; onToggle?: (value: boolean) => void; onPress: () => void }) { return <Pressable accessibilityRole={onToggle ? "switch" : "button"} accessibilityState={{ disabled: row.disabled, checked: prefValue }} style={({ pressed }) => [styles.row, pressed && !row.disabled && styles.pressed, row.disabled && styles.rowDisabled]} onPress={onToggle ? () => onToggle(!prefValue) : onPress}><Text style={styles.rowIcon}>{row.icon}</Text><View style={styles.rowCopy}><Text style={[styles.rowLabel, row.danger && styles.dangerText]}>{row.label}</Text>{row.detail ? <Text style={styles.rowDetail} numberOfLines={2}>{row.detail}</Text> : null}</View>{row.value ? <Text style={styles.rowValue}>{row.value}</Text> : onToggle ? <Switch value={Boolean(prefValue)} onValueChange={onToggle} trackColor={{ false: "#183044", true: "#118e79" }} thumbColor={prefValue ? colors.accent : "#cfe0f5"} /> : <Text style={styles.chevron}>{row.disabled ? "Locked" : "›"}</Text>}</Pressable>; }

const styles = StyleSheet.create({
  backdrop: { backgroundColor: "rgba(0,0,0,0.64)", flex: 1, justifyContent: "flex-end" },
  sheet: { backgroundColor: "#030b19", borderBottomLeftRadius: 0, borderBottomRightRadius: 0, height: "90%", padding: 0 },
  handle: { alignSelf: "center", backgroundColor: "#60759a", borderRadius: 3, height: 5, marginTop: 10, width: 54 },
  header: { alignItems: "center", backgroundColor: "#0d1734", borderBottomColor: "#18395a", borderBottomWidth: 1, flexDirection: "row", gap: 12, padding: 14 },
  gear: { alignItems: "center", backgroundColor: "#10233a", borderColor: "#1e6176", borderRadius: 23, borderWidth: 1, height: 46, justifyContent: "center", width: 46 }, gearText: { color: "#63e8f5", fontSize: 23 },
  headerCopy: { flex: 1 }, title: { color: colors.text, fontSize: 19, fontWeight: "900" }, subtitle: { color: colors.muted, fontSize: 12, marginTop: 2 },
  close: { alignItems: "center", backgroundColor: "#171d3b", borderColor: "#6f4c9c", borderRadius: 17, borderWidth: 1, height: 46, justifyContent: "center", width: 46 }, closeText: { color: colors.text, fontSize: 24 },
  content: { gap: 10, paddingBottom: 38 }, dashboard: { borderColor: "#17485d", borderRadius: 18, borderWidth: 1, gap: 10, margin: 12, padding: 12 },
  contactLine: { alignItems: "center", flexDirection: "row", gap: 10 }, avatar: { alignItems: "center", backgroundColor: "#164259", borderColor: "#55e8f3", borderRadius: 26, borderWidth: 2, height: 52, justifyContent: "center", width: 52 }, avatarText: { color: colors.text, fontSize: 15, fontWeight: "900" }, contactCopy: { flex: 1 }, contactName: { color: colors.text, fontSize: 20, fontWeight: "900" }, online: { color: colors.accent, fontSize: 12, marginTop: 3 },
  quickGrid: { flexDirection: "row", gap: 7 }, actionGrid: { flexDirection: "row", flexWrap: "wrap", gap: 7 }, quick: { alignItems: "center", borderColor: "#1a4b61", borderRadius: 12, borderWidth: 1, flexGrow: 1, minHeight: 55, minWidth: "29%", justifyContent: "center", padding: 6 }, quickIcon: { color: "#65eafb", fontSize: 19 }, quickLabel: { color: colors.text, fontSize: 10, marginTop: 3 },
  metrics: { flexDirection: "row", flexWrap: "wrap", gap: 7 }, dashboardMetric: { backgroundColor: "#071326", borderColor: "#173c50", borderRadius: 12, borderWidth: 1, minHeight: 58, padding: 9, width: "48.8%" }, metricLabel: { color: colors.text, fontSize: 11, fontWeight: "800" }, metricValue: { color: colors.muted, fontSize: 11, marginTop: 5 },
  searchWrap: { alignItems: "center", backgroundColor: "#030b19", borderColor: "#1c4c63", borderRadius: 15, borderWidth: 1, flexDirection: "row", marginHorizontal: 12, paddingHorizontal: 12 }, searchIcon: { color: "#67eafb", fontSize: 18 }, search: { color: colors.text, flex: 1, minHeight: 48, paddingHorizontal: 10 },
  section: { borderColor: "#143a4e", borderRadius: 16, borderWidth: 1, marginHorizontal: 12, overflow: "hidden" }, dangerSection: { borderColor: "#702642" }, sectionHeader: { alignItems: "center", backgroundColor: "#0c1730", flexDirection: "row", gap: 10, minHeight: 70, padding: 12 }, sectionIcon: { alignItems: "center", backgroundColor: "#10233a", borderColor: "#1d5268", borderRadius: 12, borderWidth: 1, height: 46, justifyContent: "center", width: 46 }, sectionIconText: { color: "#61e9f6", fontSize: 20 }, sectionCopy: { flex: 1 }, sectionTitle: { color: colors.text, fontSize: 17, fontWeight: "900" }, sectionSubtitle: { color: colors.muted, fontSize: 11, marginTop: 3 },
  row: { alignItems: "center", borderTopColor: "#10283a", borderTopWidth: StyleSheet.hairlineWidth, flexDirection: "row", gap: 10, minHeight: 62, padding: 11 }, rowDisabled: { opacity: 0.72 }, rowIcon: { color: "#61e9f6", fontSize: 17, textAlign: "center", width: 27 }, rowCopy: { flex: 1 }, rowLabel: { color: colors.text, fontSize: 14, fontWeight: "800" }, rowDetail: { color: colors.muted, fontSize: 9, lineHeight: 13, marginTop: 3 }, rowValue: { color: "#82f5d5", fontSize: 13 }, chevron: { color: colors.muted, fontSize: 12 }, dangerText: { color: "#ff7897" },
  pressed: { backgroundColor: "rgba(97,234,246,0.09)" }, disabled: { opacity: 0.46 }, empty: { color: colors.muted, padding: 20, textAlign: "center" }, notice: { backgroundColor: "#0e2b31", borderRadius: logiNexus.radius.panel, color: colors.accent, fontSize: 11, marginHorizontal: 12, padding: 12, textAlign: "center" }
});
