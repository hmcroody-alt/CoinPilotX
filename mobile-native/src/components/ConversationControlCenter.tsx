import AsyncStorage from "@react-native-async-storage/async-storage";
import { useCallback, useEffect, useMemo, useState } from "react";
import { ActivityIndicator, Alert, Modal, Pressable, ScrollView, Share, StyleSheet, Switch, Text, TextInput, View } from "react-native";
import {
  archiveConversation,
  ConversationControlData,
  ConversationControlMediaItem,
  ConversationControlMember,
  ConversationControlSettings,
  exportConversationControlData,
  getConversationControlCenter,
  listConversationControlLinks,
  listConversationControlMedia,
  listConversationMembers,
  listConversationPinnedMessages,
  markConversationUnread,
  MessengerMessage,
  muteConversation,
  pinConversation,
  runConversationControlAction,
  searchConversationMessages,
  updateConversationControlSetting
} from "../api/messenger";
import { PULSE_AI_CONVERSATION_ID, PULSE_AI_DISPLAY_NAME } from "../api/messenger";
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
  // Server-authoritative peer presence label, supplied by the screen that owns
  // the live presence subscription. When present it is the truth for the
  // activity line; the local `connected` flag only ever describes our own link
  // and must never be used to claim the other person is online.
  activityStatus?: string;
  assistantConversation?: boolean;
  onClose: () => void;
  onOpenSafety: (section: "reports" | "blocks") => void;
  onStartCall?: (callType: "audio" | "video") => void;
};

type Section = "conversation" | "notifications" | "appearance" | "privacy" | "media" | "security" | "productivity" | "storage" | "accessibility" | "danger";
type ControlAction =
  | "members"
  | "shared-media"
  | "shared-files"
  | "shared-links"
  | "pinned-messages"
  | "search-chat"
  | "message-stats"
  | "storage"
  | "export-chat"
  | "start-audio-call"
  | "start-video-call"
  | "mute"
  | "pin"
  | "archive"
  | "mark-unread"
  | "clear-cache"
  | "security-status"
  | "verify-contact"
  | "report-conversation"
  | "block-user"
  | "clear-conversation"
  | "delete-conversation"
  | "leave-group"
  | "delete-media"
  | "reset-settings"
  | "create-note"
  | "create-task"
  | "unavailable";

type SelectOption = [string, string];
type SettingSpec = { section: Section; key: string; kind: "toggle" | "select"; options?: SelectOption[] };
type RowSpec = {
  label: string;
  icon: string;
  detail?: string;
  value?: string;
  action?: ControlAction;
  setting?: SettingSpec;
  dangerConfirm?: string;
  danger?: boolean;
  disabled?: boolean;
  disabledReason?: string;
  destructive?: boolean;
};
type DetailPanel = { title: string; subtitle?: string; lines?: string[]; kind?: "search" };

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

const OPTIONS: Record<string, SelectOption[]> = {
  mute_choice: [["off", "Off"], ["1_hour", "1 hour"], ["8_hours", "8 hours"], ["today", "Today"], ["1_week", "1 week"], ["forever", "Forever"]],
  sound: [["pulse_beam", "Pulse Beam"], ["soft_orbit", "Soft Orbit"], ["deep_signal", "Deep Signal"], ["crystal_ping", "Crystal Ping"], ["silent", "Silent"]],
  theme: [["dark_galaxy", "Dark Galaxy"], ["pulse_green", "Pulse Green"], ["deep_space", "Deep Space"], ["nebula", "Nebula"], ["cyber_night", "Cyber Night"], ["solar_flame", "Solar Flame"], ["ocean_signal", "Ocean Signal"], ["royal_purple", "Royal Purple"], ["haiti_night", "Haiti Night"], ["creator_gold", "Creator Gold"]],
  wallpaper: [["deep_space", "Deep Space"], ["neon_planet", "Neon Planet"], ["galaxy_grid", "Galaxy Grid"], ["pulse_horizon", "Pulse Horizon"], ["alien_city", "Alien City"], ["cosmic_ocean", "Cosmic Ocean"], ["aurora_signal", "Aurora Signal"], ["dark_nebula", "Dark Nebula"], ["star_tunnel", "Star Tunnel"], ["minimal_black", "Minimal Black"]],
  bubble_color: [["cyan", "Cyan"], ["purple", "Purple"], ["rose", "Rose"], ["orange", "Orange"], ["green", "Green"], ["gold", "Gold"], ["blue", "Blue"]],
  font_size: [["small", "Small"], ["medium", "Medium"], ["large", "Large"], ["extra_large", "Extra Large"]],
  density: [["compact", "Compact"], ["balanced", "Balanced"], ["relaxed", "Relaxed"]],
  animation_level: [["full", "Full"], ["balanced", "Balanced"], ["reduced", "Reduced"], ["off", "Off"]],
  upload_quality: [["standard", "Standard"], ["high", "High"], ["original", "Original"]],
  disappearing_messages: [["off", "Off"], ["24_hours", "24 hours"], ["7_days", "7 days"], ["30_days", "30 days"]],
  reminder: [["off", "Off"], ["today", "Today"], ["tomorrow", "Tomorrow"], ["next_week", "Next week"]]
};

const OFFLINE_REASON = "Connect to PulseSoc to sync this control.";

function createAssistantControlData(messageCount: number, connected: boolean): ConversationControlData {
  return {
    ok: true,
    conversation: {
      id: PULSE_AI_CONVERSATION_ID,
      conversation_id: PULSE_AI_CONVERSATION_ID,
      title: PULSE_AI_DISPLAY_NAME,
      name: PULSE_AI_DISPLAY_NAME,
      conversation_type: "ai",
      member_count: 2,
      pinned: true,
      muted: false,
      trust_state: "intelligence",
      verified: true,
      capabilities: {
        search: true,
        members: true,
        shared_media: false,
        message_stats: true,
        pin: true,
        archive: false,
        mark_unread: false,
        mute: false,
        report: true,
        block: false,
        voice_call: false,
        video_call: false,
        export_chat: true
      }
    },
    stats: {
      messages: messageCount,
      media_files: 0,
      photos: 0,
      videos: 0,
      voice: 0,
      files: 0,
      links: 0,
      storage_used_bytes: 0,
      unread: 0,
      members: 2,
      connection: connected ? "Connected" : "Reconnecting",
      security_label: "PulseSoc intelligence conversation",
      activity_status: "Always available",
      muted: false,
      pinned: true
    },
    settings: {},
    capabilities: {
      search: true,
      members: true,
      shared_media: false,
      message_stats: true,
      pin: true,
      archive: false,
      mark_unread: false,
      mute: false,
      report: true,
      block: false,
      voice_call: false,
      video_call: false,
      export_chat: true
    }
  };
}

export function ConversationControlCenter({ visible, conversationId, title, messages, connected = true, activityStatus = "", assistantConversation = false, onClose, onOpenSafety, onStartCall }: Props) {
  const [query, setQuery] = useState("");
  const [open, setOpen] = useState<Section[]>(["conversation"]);
  const [notice, setNotice] = useState("");
  const [loading, setLoading] = useState(false);
  const [savingKey, setSavingKey] = useState("");
  const [controlData, setControlData] = useState<ConversationControlData | null>(null);
  const [detail, setDetail] = useState<DetailPanel | null>(null);
  const [chatSearch, setChatSearch] = useState("");
  const [chatSearchLoading, setChatSearchLoading] = useState(false);
  const media = useMemo(() => messages.filter((message) => Boolean(message.media_url)), [messages]);
  const images = useMemo(() => media.filter((message) => ["image", "gif"].includes(String(message.message_type || "").toLowerCase())), [media]);
  const videos = useMemo(() => media.filter((message) => String(message.message_type || "").toLowerCase() === "video"), [media]);
  const voices = useMemo(() => media.filter((message) => ["voice", "audio"].includes(String(message.message_type || "").toLowerCase())), [media]);
  const files = useMemo(() => media.filter((message) => !["image", "gif", "video", "voice", "audio"].includes(String(message.message_type || "").toLowerCase())), [media]);
  const localMediaBytes = useMemo(() => media.reduce((sum, message) => sum + Number(message.file_size || 0), 0), [media]);
  const localUnread = useMemo(() => messages.filter((message) => !message.is_mine && !message.seen_at).length, [messages]);
  const localParticipantCount = useMemo(() => new Set(messages.map((message) => message.sender_user_id || message.sender_id).filter(Boolean)).size + (messages.some((message) => message.is_mine) ? 1 : 0), [messages]);

  const refreshControlCenter = useCallback(async () => {
    if (!visible || !conversationId) return;
    setLoading(true);
    try {
      if (assistantConversation) {
        setControlData(createAssistantControlData(messages.length, connected));
        setNotice("");
        return;
      }
      const next = await getConversationControlCenter(conversationId);
      setControlData(next);
      setNotice("");
    } catch (error) {
      setNotice(errorMessage(error, "Conversation controls could not load."));
    } finally {
      setLoading(false);
    }
  }, [assistantConversation, connected, conversationId, messages.length, visible]);

  useEffect(() => {
    if (!visible) return;
    setDetail(null);
    setQuery("");
    refreshControlCenter().catch(() => undefined);
  }, [refreshControlCenter, visible]);

  const conversation = controlData?.conversation;
  const stats = useMemo(() => ({
    messages: Number(controlData?.stats?.messages || messages.length || 0),
    media_files: Number(controlData?.stats?.media_files || media.length || 0),
    photos: Number(controlData?.stats?.photos || images.length || 0),
    videos: Number(controlData?.stats?.videos || videos.length || 0),
    voice: Number(controlData?.stats?.voice || voices.length || 0),
    files: Number(controlData?.stats?.files || files.length || 0),
    links: Number(controlData?.stats?.links || 0),
    storage_used_bytes: Number(controlData?.stats?.storage_used_bytes || localMediaBytes || 0),
    unread: Number(controlData?.stats?.unread || localUnread || 0),
    members: Number(controlData?.stats?.members || conversation?.member_count || localParticipantCount || 0),
    connection: String(controlData?.stats?.connection || (connected ? "Connected" : "Reconnecting")),
    security_label: String(controlData?.stats?.security_label || "Protected channel"),
    // Only the server may say someone is online. This previously fell back to
    // `connected ? "Online" : "Reconnecting"`, which reported *our own* socket
    // health as the other person's presence -- so anyone with a working network
    // saw every peer as online regardless of whether they were connected.
    // `activityStatus` is the live label from the owning screen's presence
    // subscription. It wins when present because it is fresher than the
    // control-centre fetch, which is only made when the sheet is opened.
    activity_status: String(activityStatus || controlData?.stats?.activity_status || ""),
    muted: Boolean(controlData?.stats?.muted || conversation?.muted),
    pinned: Boolean(controlData?.stats?.pinned || conversation?.pinned)
  }), [activityStatus, connected, controlData?.stats, conversation?.member_count, conversation?.muted, conversation?.pinned, files.length, images.length, localMediaBytes, localParticipantCount, localUnread, media.length, messages.length, videos.length, voices.length]);
  const settings = controlData?.settings || {};
  const capabilities = controlData?.capabilities || conversation?.capabilities || {};
  const isGroup = Boolean(conversation?.is_group || ["group", "room", "community_channel"].includes(String(conversation?.conversation_type || "").toLowerCase()));
  const can = useCallback((key: string, fallback = true) => {
    const value = capabilities[key];
    return typeof value === "boolean" ? value : fallback;
  }, [capabilities]);
  const rows = useMemo(() => buildRows({
    stats,
    settings,
    isGroup,
    can,
    connected,
    audioCallAvailable: Boolean(onStartCall),
    videoCallAvailable: Boolean(onStartCall)
  }), [can, connected, isGroup, onStartCall, settings, stats]);
  const normalizedQuery = query.trim().toLowerCase();
  const sections = SECTION_META.map((section) => ({ ...section, rows: rows[section.key].filter((row) => !normalizedQuery || `${section.label} ${section.subtitle} ${row.label} ${row.detail || ""} ${row.value || ""}`.toLowerCase().includes(normalizedQuery)) }))
    .filter((section) => !normalizedQuery || section.rows.length > 0);
  const statusText = assistantConversation
    ? "Always available · PulseSoc Intelligence"
    : conversation?.conversation_type === "direct"
    ? `${stats.activity_status || "Presence unavailable"} · Direct Conversation`
    : `${stats.members || "Unknown"} members · ${conversation?.conversation_type || "Conversation"}`;

  async function loadMembers() {
    setSavingKey("members");
    try {
      const members = await listConversationMembers(conversationId);
      setDetail({ title: "Members", subtitle: `${members.length} participant${members.length === 1 ? "" : "s"}`, lines: members.map(memberLine) });
      setNotice("");
    } catch (error) {
      setNotice(errorMessage(error, "Members could not load."));
    } finally {
      setSavingKey("");
    }
  }

  async function loadSharedMedia(kind: "all" | "files" = "all") {
    setSavingKey(kind === "files" ? "shared-files" : "shared-media");
    try {
      const data = await listConversationControlMedia(conversationId, kind, kind === "files" ? 100 : 60);
      const items = data.items || [];
      setDetail({
        title: kind === "files" ? "Shared Files" : "Shared Media",
        subtitle: `${items.length} item${items.length === 1 ? "" : "s"}`,
        lines: items.map(mediaLine)
      });
      setNotice("");
    } catch (error) {
      setNotice(errorMessage(error, "Shared media could not load."));
    } finally {
      setSavingKey("");
    }
  }

  async function loadLinks() {
    setSavingKey("shared-links");
    try {
      const data = await listConversationControlLinks(conversationId);
      const items = data.items || [];
      setDetail({
        title: "Shared Links",
        subtitle: `${items.length} link${items.length === 1 ? "" : "s"}`,
        lines: items.map((item) => `${String(item.domain || "Link")} · ${String(item.url || "")}`)
      });
      setNotice("");
    } catch (error) {
      setNotice(errorMessage(error, "Shared links could not load."));
    } finally {
      setSavingKey("");
    }
  }

  async function loadPinnedMessages() {
    setSavingKey("pinned-messages");
    try {
      const data = await listConversationPinnedMessages(conversationId);
      const items = data.items || [];
      setDetail({
        title: "Pinned Messages",
        subtitle: `${items.length} pinned`,
        lines: items.map((message) => `${message.sender_display_name || "Pulse member"}: ${message.body || `[${message.message_type || "attachment"}]`}`)
      });
      setNotice("");
    } catch (error) {
      setNotice(errorMessage(error, "Pinned messages could not load."));
    } finally {
      setSavingKey("");
    }
  }

  async function runSearch() {
    const clean = chatSearch.trim();
    if (!clean) {
      setNotice("Enter a phrase to search this conversation.");
      return;
    }
    setChatSearchLoading(true);
    try {
      if (assistantConversation) {
        const results = messages.filter((message) => {
          const haystack = `${message.sender_display_name || ""} ${message.body || ""} ${message.content || ""} ${message.text || ""}`.toLowerCase();
          return haystack.includes(clean.toLowerCase());
        });
        setDetail({
          title: "Search Chat",
          subtitle: `${results.length} local result${results.length === 1 ? "" : "s"} for “${clean}”`,
          kind: "search",
          lines: results.map((message) => `${message.is_mine ? "You" : PULSE_AI_DISPLAY_NAME}: ${message.body || message.content || message.text || "[message]"}`)
        });
        setNotice("");
        return;
      }
      const results = await searchConversationMessages(conversationId, clean);
      setDetail({
        title: "Search Chat",
        subtitle: `${results.length} result${results.length === 1 ? "" : "s"} for “${clean}”`,
        kind: "search",
        lines: results.map((message) => `${message.sender_display_name || (message.is_mine ? "You" : "Pulse member")}: ${message.body || `[${message.message_type || "attachment"}]`}`)
      });
      setNotice("");
    } catch (error) {
      setNotice(errorMessage(error, "Search could not run."));
    } finally {
      setChatSearchLoading(false);
    }
  }

  async function exportChat() {
    if (assistantConversation) {
      const payload = JSON.stringify({
        conversation_id: PULSE_AI_CONVERSATION_ID,
        title: PULSE_AI_DISPLAY_NAME,
        exported_at: new Date().toISOString(),
        messages: messages.map((message) => ({
          id: message.id,
          sender: message.is_mine ? "You" : PULSE_AI_DISPLAY_NAME,
          body: message.body || message.content || message.text || "",
          created_at: message.created_at
        }))
      }, null, 2);
      await Share.share({ title: "UNDX conversation export", message: payload });
      setNotice("UNDX conversation export opened in the native share sheet.");
      return;
    }
    setSavingKey("export-chat");
    try {
      const data = await exportConversationControlData(conversationId);
      const payload = JSON.stringify(data.export || {}, null, 2);
      await Share.share({ title: data.filename || `${title} export`, message: payload || "No export data returned." });
      setNotice("Conversation export opened in the native share sheet.");
    } catch (error) {
      setNotice(errorMessage(error, "Conversation export could not open."));
    } finally {
      setSavingKey("");
    }
  }

  async function clearLocalCache() {
    await AsyncStorage.removeItem(`pulsesoc.native.messenger.v2.messages.${conversationId}`);
    setNotice("Local cache cleared. Server messages and remote media were not deleted.");
  }

  async function saveSetting(row: RowSpec, nextValue: boolean | string) {
    if (!row.setting) return;
    if (assistantConversation) {
      setNotice("UNDX uses the shared Messenger composer. These per-chat settings stay with production human and group conversations.");
      return;
    }
    const key = `${row.setting.section}.${row.setting.key}`;
    setSavingKey(key);
    try {
      const data = await updateConversationControlSetting(conversationId, row.setting.section, row.setting.key, nextValue);
      setControlData((current) => ({ ...(current || {}), ...data, settings: data.settings || current?.settings }));
      setNotice(`${row.label} saved.`);
    } catch (error) {
      setNotice(errorMessage(error, `${row.label} could not be saved.`));
    } finally {
      setSavingKey("");
    }
  }

  async function promptServerAction(action: "create-note" | "create-task") {
    const label = action === "create-note" ? "Conversation note" : "Conversation task";
    const fallback = () => setNotice(`${label} needs text input; use the web control center if this device does not show the native prompt.`);
    const prompt = (Alert as unknown as { prompt?: (title: string, message?: string, callbackOrButtons?: ((text: string) => void) | Array<Record<string, unknown>>, type?: string, defaultValue?: string, keyboardType?: string) => void }).prompt;
    if (!prompt) return fallback();
    prompt(label, "Saved.", async (body: string) => {
      if (!body?.trim()) return;
      setSavingKey(action);
      try {
        const data = await runConversationControlAction(conversationId, action, body.trim());
        setNotice(data.message || `${label} saved.`);
      } catch (error) {
        setNotice(errorMessage(error, `${label} could not be saved.`));
      } finally {
        setSavingKey("");
      }
    });
  }

  async function executeAction(row: RowSpec) {
    if (row.disabled) {
      setNotice(row.disabledReason || `${row.label} is unavailable in this conversation.`);
      return;
    }
    if (!connected && row.action !== "clear-cache" && row.action !== "search-chat") {
      setNotice(OFFLINE_REASON);
      return;
    }
    const run = async () => {
      switch (row.action) {
        case "members":
          if (assistantConversation) {
            setDetail({ title: "Participants", subtitle: "PulseSoc intelligence chat", lines: ["You", "UNDX · PulseSoc Intelligence"] });
            return;
          }
          return loadMembers();
        case "shared-media":
          if (assistantConversation) {
            setNotice("UNDX native chat is text-first right now. Attachments are disabled until the assistant media contract is available.");
            return;
          }
          return loadSharedMedia("all");
        case "shared-files":
          if (assistantConversation) {
            setNotice("UNDX native chat is text-first right now. Files are disabled until the assistant media contract is available.");
            return;
          }
          return loadSharedMedia("files");
        case "shared-links":
          if (assistantConversation) {
            const links = messages
              .map((message) => message.body || message.content || message.text || "")
              .flatMap((body) => body.match(/https?:\/\/\S+/g) || []);
            setDetail({ title: "Shared Links", subtitle: `${links.length} local link${links.length === 1 ? "" : "s"}`, lines: links });
            return;
          }
          return loadLinks();
        case "pinned-messages":
          return loadPinnedMessages();
        case "search-chat":
          setDetail({ title: "Search Chat", subtitle: "Search only messages this account can access.", kind: "search", lines: [] });
          return;
        case "message-stats":
          setDetail({ title: "Message Stats", subtitle: "Live counts from this conversation.", lines: statsLines(stats) });
          return;
        case "storage":
          setOpen((current) => Array.from(new Set([...current, "storage"])));
          setDetail({ title: "Media Storage", subtitle: "Server attachment metadata for this conversation.", lines: statsLines(stats).filter((line) => /Media|Photos|Videos|Voice|Files|Storage/i.test(line)) });
          return;
        case "export-chat":
          return exportChat();
        case "start-audio-call":
          if (!onStartCall) {
            setNotice("Audio calls are not enabled for this conversation.");
            return;
          }
          onClose();
          return onStartCall("audio");
        case "start-video-call":
          if (!onStartCall) {
            setNotice("Video calls are not enabled for this conversation.");
            return;
          }
          onClose();
          return onStartCall("video");
        case "mute": {
          if (assistantConversation) {
            setNotice("UNDX remains pinned and available in Messenger.");
            return;
          }
          setSavingKey("mute");
          const data = await muteConversation(conversationId);
          setNotice(data.message || (data.muted ? "Conversation muted." : "Conversation unmuted."));
          await refreshControlCenter();
          setSavingKey("");
          return;
        }
        case "pin": {
          if (assistantConversation) {
            setNotice("UNDX is pinned in Messenger.");
            return;
          }
          setSavingKey("pin");
          const data = await pinConversation(conversationId, !stats.pinned);
          setNotice(data.message || (data.pinned ? "Conversation pinned." : "Conversation unpinned."));
          await refreshControlCenter();
          setSavingKey("");
          return;
        }
        case "archive": {
          if (assistantConversation) {
            setNotice("UNDX cannot be archived because it is the canonical PulseSoc intelligence conversation.");
            return;
          }
          setSavingKey("archive");
          const data = await archiveConversation(conversationId);
          setNotice(data.message || "Conversation archived.");
          setSavingKey("");
          onClose();
          return;
        }
        case "mark-unread": {
          if (assistantConversation) {
            setNotice("UNDX unread state is managed by the assistant conversation history.");
            return;
          }
          setSavingKey("mark-unread");
          const data = await markConversationUnread(conversationId);
          setNotice(data.message || "Conversation marked unread.");
          await refreshControlCenter();
          setSavingKey("");
          return;
        }
        case "clear-cache":
          return Alert.alert("Clear local media cache?", "This removes cached conversation data from this device. It does not delete server messages or remote media.", [{ text: "Cancel", style: "cancel" }, { text: "Clear cache", style: "destructive", onPress: () => clearLocalCache().catch(() => setNotice("Local cache could not be cleared.")) }]);
        case "security-status":
          setDetail({
            title: "Security Status",
            subtitle: stats.security_label,
            lines: [
              "Authenticated PulseSoc access and participant checks are enforced by the server.",
              "Deleted messages and hidden media are excluded from server control-center lists.",
              "True end-to-end encryption is not claimed for this conversation."
            ]
          });
          return;
        case "verify-contact": {
          const member = (controlData?.members || []).find((item) => item.display_name && item.user_id);
          setDetail({ title: "Verify Contact", subtitle: "Protected participant", lines: [member ? `${member.display_name} is a server-authorized participant in this conversation.` : "No direct peer was returned by the server for this conversation."] });
          return;
        }
        case "report-conversation":
        case "block-user":
          onOpenSafety(row.action === "block-user" ? "blocks" : "reports");
          setSavingKey(row.action);
          try {
            const data = await runConversationControlAction(conversationId, row.action);
            setNotice(data.message || "Safety action sent.");
          } catch (error) {
            setNotice(errorMessage(error, "Safety action could not complete."));
          } finally {
            setSavingKey("");
          }
          return;
        case "clear-conversation":
        case "delete-conversation":
        case "leave-group":
        case "delete-media":
        case "reset-settings": {
          setSavingKey(row.action);
          const data = await runConversationControlAction(conversationId, row.action);
          setNotice(data.message || "Conversation action completed.");
          if (row.action === "reset-settings") await refreshControlCenter();
          if (row.action === "delete-conversation" || row.action === "leave-group") onClose();
          setSavingKey("");
          return;
        }
        case "create-note":
        case "create-task":
          return promptServerAction(row.action);
        case "unavailable":
        default:
          setNotice(row.disabledReason || `${row.label} is not enabled for this conversation.`);
      }
    };
    if (row.dangerConfirm) {
      Alert.alert(row.label, row.dangerConfirm, [{ text: "Cancel", style: "cancel" }, { text: row.destructive ? "Continue" : "Confirm", style: row.destructive ? "destructive" : "default", onPress: () => run().catch((error) => { setSavingKey(""); setNotice(errorMessage(error, `${row.label} could not complete.`)); }) }]);
      return;
    }
    try {
      await run();
    } catch (error) {
      setSavingKey("");
      setNotice(errorMessage(error, `${row.label} could not complete.`));
    }
  }

  return (
    <Modal transparent animationType="slide" visible={visible} onRequestClose={onClose} statusBarTranslucent>
      <View style={styles.backdrop}>
        <PulseCommandPanel style={styles.sheet}>
          <View style={styles.handle} />
          <View style={styles.header}>
            <View style={styles.gear}><Text style={styles.gearText}>⚙</Text></View>
            <View style={styles.headerCopy}><Text style={styles.title}>Conversation Control Center</Text><Text style={styles.subtitle}>Production Messenger controls for this chat.</Text></View>
            <Pressable accessibilityRole="button" accessibilityLabel="Refresh Conversation Control Center" style={styles.headerButton} onPress={refreshControlCenter} disabled={loading}><Text style={styles.headerButtonText}>↻</Text></Pressable>
            <Pressable accessibilityRole="button" accessibilityLabel="Close Conversation Control Center" style={styles.close} onPress={onClose}><Text style={styles.closeText}>×</Text></Pressable>
          </View>
          <ScrollView contentContainerStyle={styles.content} keyboardShouldPersistTaps="handled" stickyHeaderIndices={[1]}>
            <View style={styles.dashboard}>
              <View style={styles.contactLine}>
                <View style={styles.avatar}><Text style={styles.avatarText}>{initials(title)}</Text></View>
                <View style={styles.contactCopy}>
                  <Text style={styles.contactName} numberOfLines={1}>{assistantConversation ? PULSE_AI_DISPLAY_NAME : conversation?.title || title}</Text>
                  <Text style={styles.online}>{statusText}</Text>
                </View>
                {loading ? <ActivityIndicator color={colors.accent} /> : null}
              </View>
              <View style={styles.quickGrid}>
                <Quick label="Search Chat" icon="⌕" disabled={!can("search")} onPress={() => executeAction({ label: "Search Chat", icon: "⌕", action: "search-chat", disabled: !can("search"), disabledReason: "Search is not enabled for this conversation." })} />
                <Quick label="Shared Media" icon="▧" disabled={!can("shared_media")} onPress={() => executeAction({ label: "Shared Media", icon: "▧", action: "shared-media", disabled: !can("shared_media"), disabledReason: "Shared media is not enabled for this conversation." })} />
                <Quick label={assistantConversation ? "Identity" : "Members"} icon="♟" disabled={!can("members")} onPress={() => executeAction({ label: assistantConversation ? "Identity" : "Members", icon: "♟", action: "members", disabled: !can("members"), disabledReason: "Members are not available for this conversation." })} />
              </View>
              <View style={styles.actionGrid}>
                <Quick label="Audio Call" icon="☎" disabled={!can("voice_call") || !onStartCall} onPress={() => executeAction({ label: "Audio Call", icon: "☎", action: "start-audio-call", disabled: !can("voice_call") || !onStartCall, disabledReason: "Audio calls are not enabled for this conversation." })} />
                <Quick label="Video Call" icon="▣" disabled={!can("video_call") || !onStartCall} onPress={() => executeAction({ label: "Video Call", icon: "▣", action: "start-video-call", disabled: !can("video_call") || !onStartCall, disabledReason: "Video calls are not enabled for this conversation." })} />
                <Quick label={stats.muted ? "Unmute" : "Mute"} icon="🔕" busy={savingKey === "mute"} disabled={!can("mute")} onPress={() => executeAction({ label: stats.muted ? "Unmute Conversation" : "Mute Conversation", icon: "🔕", action: "mute", disabled: !can("mute"), disabledReason: "Mute is not enabled for this conversation." })} />
                <Quick label={stats.pinned ? "Unpin" : "Pin"} icon="📌" busy={savingKey === "pin"} disabled={!can("pin")} onPress={() => executeAction({ label: stats.pinned ? "Unpin Conversation" : "Pin Conversation", icon: "📌", action: "pin", disabled: !can("pin"), disabledReason: "Pin is not enabled for this conversation." })} />
                <Quick label="Archive" icon="▤" busy={savingKey === "archive"} disabled={!can("archive")} onPress={() => executeAction({ label: "Archive Conversation", icon: "▤", action: "archive", dangerConfirm: "Archive this conversation from your inbox?", disabled: !can("archive"), disabledReason: "Archive is not enabled for this conversation." })} />
              </View>
              <View style={styles.metrics}>
                <DashboardMetric label="Protection" value={stats.security_label} />
                <DashboardMetric label="Members" value={stats.members ? String(stats.members) : "Unavailable"} />
                <DashboardMetric label="Media Files" value={String(stats.media_files)} />
                <DashboardMetric label="Known Storage" value={formatFileSize(stats.storage_used_bytes)} />
                <DashboardMetric label="Unread" value={String(stats.unread)} />
                <DashboardMetric label="Connection" value={stats.connection} />
              </View>
            </View>
            <View style={styles.searchWrap}><Text style={styles.searchIcon}>⌕</Text><TextInput accessibilityLabel="Search conversation settings" value={query} onChangeText={setQuery} placeholder="Search settings..." placeholderTextColor={colors.muted} style={styles.search} /></View>
            {detail ? <DetailPanelView detail={detail} chatSearch={chatSearch} onChatSearchChange={setChatSearch} chatSearchLoading={chatSearchLoading} onRunSearch={runSearch} onClose={() => setDetail(null)} /> : null}
            {sections.length ? sections.map((section) => {
              const expanded = normalizedQuery ? true : open.includes(section.key);
              return <View key={section.key} style={[styles.section, section.key === "danger" && styles.dangerSection]}>
                <Pressable accessibilityRole="button" accessibilityState={{ expanded }} style={styles.sectionHeader} onPress={() => setOpen((current) => current.includes(section.key) ? current.filter((item) => item !== section.key) : [...current, section.key])}>
                  <View style={styles.sectionIcon}><Text style={styles.sectionIconText}>{section.icon}</Text></View><View style={styles.sectionCopy}><Text style={styles.sectionTitle}>{section.label}</Text><Text style={styles.sectionSubtitle}>{section.subtitle}</Text></View><Text style={styles.chevron}>{expanded ? "⌃" : "⌄"}</Text>
                </Pressable>
                {expanded ? section.rows.map((row) => <SettingRow key={`${section.key}-${row.label}`} row={row} settings={settings} saving={savingKey === settingKey(row.setting) || savingKey === row.action} onSaveSetting={saveSetting} onPress={() => executeAction(row)} />) : null}
              </View>;
            }) : <Text style={styles.empty}>No settings match “{query}”.</Text>}
            {notice ? <Pressable accessibilityRole="button" accessibilityLabel="Dismiss notice" onPress={() => setNotice("")}><Text accessibilityLiveRegion="polite" style={styles.notice}>{notice}</Text></Pressable> : null}
          </ScrollView>
        </PulseCommandPanel>
      </View>
    </Modal>
  );
}

function buildRows(input: {
  stats: ReturnType<typeof statsFallback>;
  settings: ConversationControlSettings;
  isGroup: boolean;
  can: (key: string, fallback?: boolean) => boolean;
  connected: boolean;
  audioCallAvailable: boolean;
  videoCallAvailable: boolean;
}): Record<Section, RowSpec[]> {
  const { stats, isGroup, can, connected, audioCallAvailable, videoCallAvailable } = input;
  const unavailable = (reason: string): Pick<RowSpec, "disabled" | "disabledReason"> => ({ disabled: true, disabledReason: connected ? reason : OFFLINE_REASON });
  return {
    conversation: [
      action("View Members", "👥", "members", `${stats.members || "Unknown"} server-visible participant${stats.members === 1 ? "" : "s"}`, !can("members") ? unavailable("Members are not available for this conversation.") : undefined),
      action("Shared Media", "🖼", "shared-media", `${stats.media_files} shared media/file item${stats.media_files === 1 ? "" : "s"}`, !can("shared_media") ? unavailable("Shared media is not available for this conversation.") : undefined),
      action("Pinned Messages", "📌", "pinned-messages", "Server-filtered pinned messages for this account."),
      action("Search Chat", "⌕", "search-chat", "Search messages this account can access.", !can("search") ? unavailable("Search is not available for this conversation.") : undefined),
      action("Message Stats", "▥", "message-stats", `${stats.messages} server-visible message${stats.messages === 1 ? "" : "s"}`),
      action("Media Storage", "◉", "storage", `${formatFileSize(stats.storage_used_bytes)} known server attachment metadata`),
      action("Export Chat", "⇧", "export-chat", "Exports only server-authorized, non-deleted messages.", !can("export_chat") ? unavailable("Export is not enabled for this conversation.") : undefined, "Export this conversation to the native share sheet?")
    ],
    notifications: [
      setting("Mute Conversation", "🔕", "notifications", "mute_choice", "select", "Syncs to the production mute preference.", OPTIONS.mute_choice),
      setting("Notification Sound", "♪", "notifications", "sound", "select", "Server-backed per-chat sound preference.", OPTIONS.sound),
      setting("Show on Lock Screen", "▣", "notifications", "lock_screen", "toggle", "Server-backed notification visibility."),
      setting("Show Message Preview", "◉", "notifications", "message_preview", "toggle", "Synced with privacy preview setting."),
      setting("Mention Notifications", "@", "notifications", "mentions", "toggle", "Server-backed mention alerts."),
      setting("Reaction Notifications", "✦", "notifications", "reactions", "toggle", "Server-backed reaction alerts."),
      setting("Typing Notifications", "…", "notifications", "typing", "toggle", "Server-backed typing alerts."),
      setting("Read Receipt Notifications", "✓", "notifications", "read_receipts", "toggle", "Synced with read receipt privacy.")
    ],
    appearance: [
      setting("Theme", "◌", "appearance", "theme", "select", "Server-backed chat theme.", OPTIONS.theme),
      setting("Wallpaper", "▧", "appearance", "wallpaper", "select", "Server-backed chat wallpaper.", OPTIONS.wallpaper),
      setting("Bubble Color", "●", "appearance", "bubble_color", "select", "Server-backed bubble color.", OPTIONS.bubble_color),
      setting("Font Size", "Aa", "appearance", "font_size", "select", "Server-backed text density.", OPTIONS.font_size),
      setting("Chat Density", "↕", "appearance", "density", "select", "Server-backed layout density.", OPTIONS.density),
      setting("Animation Level", "✺", "appearance", "animation_level", "select", "Server-backed animation preference.", OPTIONS.animation_level),
      setting("Reduce Particles", "·", "appearance", "reduce_particles", "toggle", "Server-backed motion preference."),
      setting("High Contrast", "◐", "appearance", "high_contrast", "toggle", "Server-backed contrast preference.")
    ],
    privacy: [
      setting("Read Receipts", "✓✓", "privacy", "read_receipts", "toggle", "Synced with PulseSoc privacy settings."),
      setting("Typing Indicator", "…", "privacy", "typing_indicator", "toggle", "Server-backed typing visibility."),
      setting("Online Status", "●", "privacy", "online_status", "toggle", "Server-backed presence visibility."),
      setting("Last Seen", "◷", "privacy", "last_seen", "toggle", "Server-backed last-seen visibility."),
      setting("Show Message Preview", "◉", "privacy", "message_preview", "toggle", "Synced with notification preview setting."),
      setting("Disappearing Messages", "⌛", "privacy", "disappearing_messages", "select", "Server-backed retention preference.", OPTIONS.disappearing_messages),
      setting("Privacy Lock", "▣", "privacy", "privacy_lock", "toggle", "Stored only when the server accepts this setting."),
      setting("Hidden Conversation", "◌", "privacy", "hidden_conversation", "toggle", "Stored only when the server accepts this setting.")
    ],
    media: [
      setting("Auto Download Photos", "▧", "media", "auto_download_photos", "toggle", "Server-backed media download preference."),
      setting("Auto Download Videos", "▶", "media", "auto_download_videos", "toggle", "Server-backed video download preference."),
      setting("Auto Download Voice Messages", "🎙", "media", "auto_download_voice", "toggle", "Server-backed voice download preference."),
      setting("Upload Quality", "HD", "media", "upload_quality", "select", "Server-backed upload preference.", OPTIONS.upload_quality),
      setting("Auto Save Camera Photos", "◎", "media", "auto_save_camera", "toggle", "Server-backed camera save preference."),
      action("Clear Media Cache", "⌫", "clear-cache", "Local cache only; server messages remain.", undefined, "Clear local cache for this conversation?"),
      action("Shared Links", "↗", "shared-links", `${stats.links} detected link${stats.links === 1 ? "" : "s"}`),
      action("Shared Files", "▤", "shared-files", `${stats.files} file${stats.files === 1 ? "" : "s"}`)
    ],
    security: [
      action("Encryption Status", "🛡", "security-status", stats.security_label),
      action("Verify Contact", "◇", "verify-contact", isGroup ? "Direct-contact verification is available in one-to-one conversations." : "Verify the server-authorized direct participant.", isGroup ? unavailable("Verify Contact is direct-conversation only.") : undefined),
      action("Trusted Devices", "▣", "unavailable", "Open account security from the main Settings surface.", unavailable("Trusted Devices are managed in account security, not per conversation.")),
      action("Active Sessions", "◉", "unavailable", "Open account security from the main Settings surface.", unavailable("Active Sessions are managed in account security, not per conversation.")),
      action("Security Log", "▤", "unavailable", "Open account security from the main Settings surface.", unavailable("Security Log is managed in account security, not per conversation.")),
      action("Report Conversation", "!", "report-conversation", "Routes to PulseSoc Trust & Safety.", !can("report") ? unavailable("Reporting is not enabled for this conversation.") : undefined, "Send this conversation to moderation review?"),
      action("Block User", "⊘", "block-user", "Direct conversations only.", (!can("block") || isGroup) ? unavailable("Block is available from direct conversations only.") : undefined, "Block this member?")
    ],
    productivity: [
      action(stats.pinned ? "Unpin Conversation" : "Pin Conversation", "📌", "pin", "Syncs to production conversation pin state.", !can("pin") ? unavailable("Pin is not enabled for this conversation.") : undefined),
      action("Archive Conversation", "▤", "archive", "Archives this conversation for this account.", !can("archive") ? unavailable("Archive is not enabled for this conversation.") : undefined, "Archive this conversation?"),
      action("Mark Unread", "◌", "mark-unread", "Server-backed unread state.", !can("mark_unread") ? unavailable("Mark unread is not enabled for this conversation.") : undefined),
      setting("Favorite Conversation", "★", "productivity", "favorite", "toggle", "Server-backed favorite state."),
      setting("Reminder", "⏱", "productivity", "reminder", "select", "Server-backed reminder preference.", OPTIONS.reminder),
      action("Create Note", "✎", "create-note", "Applies to this conversation right away."),
      action("Create Task", "☑", "create-task", "Applies to this conversation right away.")
    ],
    storage: [
      valueRow("Conversation Size", "◉", formatFileSize(stats.storage_used_bytes), "Known server attachment bytes."),
      valueRow("Photos", "▧", String(stats.photos), "Server-visible photos."),
      valueRow("Videos", "▶", String(stats.videos), "Server-visible videos."),
      valueRow("Voice Messages", "🎙", String(stats.voice), "Server-visible voice messages."),
      valueRow("Files", "▤", String(stats.files), "Server-visible files."),
      action("Links", "↗", "shared-links", `${stats.links} detected link${stats.links === 1 ? "" : "s"}`),
      action("Clear Cache", "⌫", "clear-cache", "Local cache only; no remote deletion.", undefined, "Clear local cache for this conversation?")
    ],
    accessibility: [
      setting("Large Text", "Aa", "accessibility", "large_text", "toggle", "Server-backed accessibility preference."),
      setting("Reduce Motion", "↘", "accessibility", "reduce_motion", "toggle", "Server-backed accessibility preference."),
      setting("High Contrast", "◐", "accessibility", "high_contrast", "toggle", "Server-backed accessibility preference."),
      setting("Voice Reader", "🔊", "accessibility", "voice_reader", "toggle", "Server-backed accessibility preference."),
      setting("Speech to Text", "🎙", "accessibility", "speech_to_text", "toggle", "Server-backed accessibility preference."),
      setting("Text to Speech", "Aa", "accessibility", "text_to_speech", "toggle", "Server-backed accessibility preference."),
      setting("Haptic Feedback", "✦", "accessibility", "haptic_feedback", "toggle", "Server-backed haptic preference.")
    ],
    danger: [
      danger("Clear Conversation", "⌫", "clear-conversation", "Clear this conversation from your view? Other members keep their messages."),
      danger("Delete Conversation", "×", "delete-conversation", "Remove this conversation from your inbox? Other members keep their messages."),
      danger("Leave Group", "⇠", "leave-group", "Leave this group conversation?", isGroup ? undefined : unavailable("Leave Group is available only in group conversations.")),
      danger("Block User", "⊘", "block-user", "Block this member?", (!can("block") || isGroup) ? unavailable("Block is available from direct conversations only.") : undefined),
      danger("Report Spam", "!", "report-conversation", "Send this conversation to moderation review?", !can("report") ? unavailable("Reporting is not enabled for this conversation.") : undefined),
      danger("Delete Media", "⌧", "delete-media", "Hide shared media from your view in this conversation."),
      danger("Reset Conversation Settings", "↺", "reset-settings", "Reset this conversation's controls to defaults.")
    ]
  };
}

function statsFallback() {
  return {
    messages: 0,
    media_files: 0,
    photos: 0,
    videos: 0,
    voice: 0,
    files: 0,
    links: 0,
    storage_used_bytes: 0,
    unread: 0,
    members: 0,
    connection: "Unavailable",
    security_label: "Protected channel",
    activity_status: "Unavailable",
    muted: false,
    pinned: false
  };
}

function action(label: string, icon: string, controlAction: ControlAction, detail: string, availability?: Pick<RowSpec, "disabled" | "disabledReason">, dangerConfirm?: string): RowSpec {
  return { label, icon, action: controlAction, detail, dangerConfirm, ...availability };
}

function danger(label: string, icon: string, controlAction: ControlAction, confirm: string, availability?: Pick<RowSpec, "disabled" | "disabledReason">): RowSpec {
  return { ...action(label, icon, controlAction, "Server-confirmed destructive action.", availability, confirm), danger: true, destructive: true };
}

function setting(label: string, icon: string, section: Section, key: string, kind: "toggle" | "select", detail: string, options?: SelectOption[]): RowSpec {
  return { label, icon, detail, setting: { section, key, kind, options } };
}

function valueRow(label: string, icon: string, value: string, detail: string): RowSpec {
  return { label, icon, value, detail };
}

function settingKey(setting?: SettingSpec) {
  return setting ? `${setting.section}.${setting.key}` : "";
}

function readSetting(settings: ConversationControlSettings, setting: SettingSpec) {
  return settings?.[setting.section]?.[setting.key];
}

function nextSelectValue(setting: SettingSpec, current: unknown) {
  const options = setting.options || [];
  if (!options.length) return String(current || "");
  const index = Math.max(0, options.findIndex(([value]) => value === String(current)));
  return options[(index + 1) % options.length][0];
}

function optionLabel(setting: SettingSpec, current: unknown) {
  const value = String(current ?? "");
  return setting.options?.find(([item]) => item === value)?.[1] || humanize(value || "Unset");
}

function memberLine(member: ConversationControlMember) {
  const role = member.role ? ` · ${humanize(member.role)}` : "";
  const presence = member.presence ? ` · ${humanize(member.presence)}` : "";
  return `${member.display_name || "Pulse member"}${role}${presence}`;
}

function mediaLine(item: ConversationControlMediaItem) {
  const size = formatFileSize(Number(item.file_size_bytes || 0));
  const type = humanize(item.media_type || item.mime_type || "file");
  const sender = item.sender_display_name || "Pulse member";
  return `${type} · ${size} · ${sender}${item.body_preview ? ` · ${item.body_preview}` : ""}`;
}

function statsLines(stats: ReturnType<typeof statsFallback>) {
  return [
    `Messages: ${stats.messages}`,
    `Media files: ${stats.media_files}`,
    `Photos: ${stats.photos}`,
    `Videos: ${stats.videos}`,
    `Voice messages: ${stats.voice}`,
    `Files: ${stats.files}`,
    `Links: ${stats.links}`,
    `Storage: ${formatFileSize(stats.storage_used_bytes)}`,
    `Unread: ${stats.unread}`,
    `Connection: ${stats.connection}`
  ];
}

function errorMessage(error: unknown, fallback: string) {
  return error instanceof Error && error.message ? error.message : fallback;
}

function humanize(value: string) {
  return value.replace(/[_-]+/g, " ").replace(/\b\w/g, (letter) => letter.toUpperCase());
}

function initials(value: string) {
  return value.split(/\s+/).filter(Boolean).slice(0, 2).map((part) => part[0]?.toUpperCase()).join("") || "PC";
}

function Quick({ label, icon, onPress, disabled = false, busy = false }: { label: string; icon: string; onPress?: () => void; disabled?: boolean; busy?: boolean }) {
  return <Pressable accessibilityRole="button" accessibilityLabel={label} accessibilityState={{ disabled, busy }} style={({ pressed }) => [styles.quick, disabled && styles.disabled, pressed && !disabled && styles.pressed]} disabled={disabled || busy} onPress={onPress}>{busy ? <ActivityIndicator color={colors.accent} /> : <Text style={styles.quickIcon}>{icon}</Text>}<Text style={styles.quickLabel}>{label}</Text></Pressable>;
}

function DashboardMetric({ label, value }: { label: string; value: string }) {
  return <View style={styles.dashboardMetric}><Text style={styles.metricLabel}>{label}</Text><Text style={styles.metricValue} numberOfLines={1}>{value}</Text></View>;
}

function DetailPanelView({ detail, chatSearch, onChatSearchChange, chatSearchLoading, onRunSearch, onClose }: { detail: DetailPanel; chatSearch: string; onChatSearchChange: (value: string) => void; chatSearchLoading: boolean; onRunSearch: () => void; onClose: () => void }) {
  return (
    <View style={styles.detailPanel}>
      <View style={styles.detailHeader}><View><Text style={styles.detailTitle}>{detail.title}</Text>{detail.subtitle ? <Text style={styles.detailSubtitle}>{detail.subtitle}</Text> : null}</View><Pressable accessibilityRole="button" accessibilityLabel={`Close ${detail.title}`} onPress={onClose}><Text style={styles.detailClose}>×</Text></Pressable></View>
      {detail.kind === "search" ? <View style={styles.chatSearchRow}><TextInput accessibilityLabel="Search this conversation" placeholder="Search this chat..." placeholderTextColor={colors.muted} value={chatSearch} onChangeText={onChatSearchChange} style={styles.chatSearchInput} onSubmitEditing={onRunSearch} /><Pressable accessibilityRole="button" accessibilityLabel="Run conversation search" style={styles.chatSearchButton} onPress={onRunSearch} disabled={chatSearchLoading}>{chatSearchLoading ? <ActivityIndicator color="#001118" /> : <Text style={styles.chatSearchButtonText}>Search</Text>}</Pressable></View> : null}
      {detail.lines?.length ? detail.lines.slice(0, 80).map((line, index) => <Text key={`${detail.title}-${index}`} style={styles.detailLine}>{line}</Text>) : <Text style={styles.detailEmpty}>No items returned by the server.</Text>}
    </View>
  );
}

function SettingRow({ row, settings, saving, onSaveSetting, onPress }: { row: RowSpec; settings: ConversationControlSettings; saving: boolean; onSaveSetting: (row: RowSpec, value: boolean | string) => void; onPress: () => void }) {
  const current = row.setting ? readSetting(settings, row.setting) : undefined;
  const isToggle = row.setting?.kind === "toggle";
  const isSelect = row.setting?.kind === "select";
  const rightValue = row.value || (isSelect && row.setting ? optionLabel(row.setting, current) : "");
  const accessibilityState = { disabled: row.disabled, checked: isToggle ? Boolean(current) : undefined, busy: saving };
  const rowPress = () => {
    if (row.disabled) return onPress();
    if (row.setting && isToggle) return onSaveSetting(row, !Boolean(current));
    if (row.setting && isSelect) return onSaveSetting(row, nextSelectValue(row.setting, current));
    return onPress();
  };
  return (
    <Pressable accessibilityRole={isToggle ? "switch" : "button"} accessibilityLabel={row.label} accessibilityHint={row.disabled ? row.disabledReason : row.detail} accessibilityState={accessibilityState} style={({ pressed }) => [styles.row, pressed && !row.disabled && styles.pressed, row.disabled && styles.rowDisabled]} onPress={rowPress}>
      <Text style={styles.rowIcon}>{row.icon}</Text>
      <View style={styles.rowCopy}>
        <Text style={[styles.rowLabel, row.danger && styles.dangerText]}>{row.label}</Text>
        <Text style={styles.rowDetail} numberOfLines={2}>{row.disabled ? row.disabledReason || "Unavailable for this conversation." : row.detail || "Production-backed control."}</Text>
      </View>
      {saving ? <ActivityIndicator color={colors.accent} /> : isToggle ? <Switch value={Boolean(current)} onValueChange={(value) => onSaveSetting(row, value)} disabled={row.disabled} trackColor={{ false: "#183044", true: "#118e79" }} thumbColor={current ? colors.accent : "#cfe0f5"} /> : rightValue ? <Text style={styles.rowValue}>{rightValue}</Text> : <Text style={styles.chevron}>{row.disabled ? "Unavailable" : "›"}</Text>}
    </Pressable>
  );
}

const styles = StyleSheet.create({
  backdrop: { backgroundColor: "rgba(0,0,0,0.64)", flex: 1, justifyContent: "flex-end" },
  sheet: { backgroundColor: "#030b19", borderBottomLeftRadius: 0, borderBottomRightRadius: 0, height: "92%", padding: 0 },
  handle: { alignSelf: "center", backgroundColor: "#60759a", borderRadius: 3, height: 5, marginTop: 10, width: 54 },
  header: { alignItems: "center", backgroundColor: "#0d1734", borderBottomColor: "#18395a", borderBottomWidth: 1, flexDirection: "row", gap: 10, padding: 14 },
  gear: { alignItems: "center", backgroundColor: "#10233a", borderColor: "#1e6176", borderRadius: 23, borderWidth: 1, height: 46, justifyContent: "center", width: 46 },
  gearText: { color: "#63e8f5", fontSize: 23 },
  headerCopy: { flex: 1 },
  title: { color: colors.text, fontSize: 18, fontWeight: "900" },
  subtitle: { color: colors.muted, fontSize: 12, marginTop: 2 },
  headerButton: { alignItems: "center", backgroundColor: "#10233a", borderColor: "#1e6176", borderRadius: 17, borderWidth: 1, height: 42, justifyContent: "center", width: 42 },
  headerButtonText: { color: colors.accent, fontSize: 19, fontWeight: "900" },
  close: { alignItems: "center", backgroundColor: "#171d3b", borderColor: "#6f4c9c", borderRadius: 17, borderWidth: 1, height: 42, justifyContent: "center", width: 42 },
  closeText: { color: colors.text, fontSize: 24 },
  content: { gap: 10, paddingBottom: 38 },
  dashboard: { borderColor: "#17485d", borderRadius: 18, borderWidth: 1, gap: 10, margin: 12, padding: 12 },
  contactLine: { alignItems: "center", flexDirection: "row", gap: 10 },
  avatar: { alignItems: "center", backgroundColor: "#164259", borderColor: "#55e8f3", borderRadius: 26, borderWidth: 2, height: 52, justifyContent: "center", width: 52 },
  avatarText: { color: colors.text, fontSize: 15, fontWeight: "900" },
  contactCopy: { flex: 1 },
  contactName: { color: colors.text, fontSize: 20, fontWeight: "900" },
  online: { color: colors.accent, fontSize: 12, marginTop: 3 },
  quickGrid: { flexDirection: "row", gap: 7 },
  actionGrid: { flexDirection: "row", flexWrap: "wrap", gap: 7 },
  quick: { alignItems: "center", borderColor: "#1a4b61", borderRadius: 12, borderWidth: 1, flexGrow: 1, justifyContent: "center", minHeight: 58, minWidth: "29%", padding: 7 },
  quickIcon: { color: "#65eafb", fontSize: 19 },
  quickLabel: { color: colors.text, fontSize: 10, fontWeight: "800", marginTop: 4, textAlign: "center" },
  metrics: { flexDirection: "row", flexWrap: "wrap", gap: 7 },
  dashboardMetric: { backgroundColor: "#071326", borderColor: "#173c50", borderRadius: 12, borderWidth: 1, minHeight: 58, padding: 9, width: "48.8%" },
  metricLabel: { color: colors.text, fontSize: 11, fontWeight: "800" },
  metricValue: { color: colors.muted, fontSize: 11, marginTop: 5 },
  searchWrap: { alignItems: "center", backgroundColor: "#030b19", borderColor: "#1c4c63", borderRadius: 15, borderWidth: 1, flexDirection: "row", marginHorizontal: 12, paddingHorizontal: 12 },
  searchIcon: { color: "#67eafb", fontSize: 18 },
  search: { color: colors.text, flex: 1, minHeight: 48, paddingHorizontal: 10 },
  detailPanel: { backgroundColor: "#071326", borderColor: "#1f6076", borderRadius: 16, borderWidth: 1, gap: 9, marginHorizontal: 12, padding: 12 },
  detailHeader: { alignItems: "center", flexDirection: "row", justifyContent: "space-between" },
  detailTitle: { color: colors.text, fontSize: 17, fontWeight: "900" },
  detailSubtitle: { color: colors.muted, fontSize: 11, marginTop: 3 },
  detailClose: { color: colors.text, fontSize: 23, paddingHorizontal: 10 },
  detailLine: { backgroundColor: "rgba(255,255,255,0.035)", borderRadius: 10, color: colors.text, fontSize: 12, lineHeight: 17, padding: 9 },
  detailEmpty: { color: colors.muted, fontSize: 12, paddingVertical: 4 },
  chatSearchRow: { alignItems: "center", flexDirection: "row", gap: 8 },
  chatSearchInput: { borderColor: "#1c4c63", borderRadius: 13, borderWidth: 1, color: colors.text, flex: 1, minHeight: 46, paddingHorizontal: 12 },
  chatSearchButton: { alignItems: "center", backgroundColor: colors.accent, borderRadius: 13, justifyContent: "center", minHeight: 46, paddingHorizontal: 14 },
  chatSearchButtonText: { color: "#001118", fontWeight: "900" },
  section: { borderColor: "#143a4e", borderRadius: 16, borderWidth: 1, marginHorizontal: 12, overflow: "hidden" },
  dangerSection: { borderColor: "#702642" },
  sectionHeader: { alignItems: "center", backgroundColor: "#0c1730", flexDirection: "row", gap: 10, minHeight: 70, padding: 12 },
  sectionIcon: { alignItems: "center", backgroundColor: "#10233a", borderColor: "#1d5268", borderRadius: 12, borderWidth: 1, height: 46, justifyContent: "center", width: 46 },
  sectionIconText: { color: "#61e9f6", fontSize: 20 },
  sectionCopy: { flex: 1 },
  sectionTitle: { color: colors.text, fontSize: 17, fontWeight: "900" },
  sectionSubtitle: { color: colors.muted, fontSize: 11, marginTop: 3 },
  row: { alignItems: "center", borderTopColor: "#10283a", borderTopWidth: StyleSheet.hairlineWidth, flexDirection: "row", gap: 10, minHeight: 64, padding: 11 },
  rowDisabled: { opacity: 0.7 },
  rowIcon: { color: "#61e9f6", fontSize: 17, textAlign: "center", width: 27 },
  rowCopy: { flex: 1 },
  rowLabel: { color: colors.text, fontSize: 14, fontWeight: "800" },
  rowDetail: { color: colors.muted, fontSize: 9, lineHeight: 13, marginTop: 3 },
  rowValue: { color: "#82f5d5", fontSize: 12, fontWeight: "800", maxWidth: 98, textAlign: "right" },
  chevron: { color: colors.muted, fontSize: 12 },
  dangerText: { color: "#ff7897" },
  pressed: { backgroundColor: "rgba(97,234,246,0.09)" },
  disabled: { opacity: 0.46 },
  empty: { color: colors.muted, padding: 20, textAlign: "center" },
  notice: { backgroundColor: "#0e2b31", borderRadius: logiNexus.radius.panel, color: colors.accent, fontSize: 11, marginHorizontal: 12, padding: 12, textAlign: "center" }
});
