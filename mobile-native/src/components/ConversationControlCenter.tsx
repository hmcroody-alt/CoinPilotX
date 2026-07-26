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
import { translate, useTranslation } from "../i18n";
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

// Section metadata is a function (not a module constant) so the copy is resolved
// against the active locale on every render instead of being frozen at the
// bundle-import moment, which happens before the i18n catalogs are loaded.
function sectionMeta(): Array<{ key: Section; label: string; icon: string; subtitle: string }> {
  return [
    { key: "conversation", label: translate("messaging:controls.sections.conversation.label"), icon: "💬", subtitle: translate("messaging:controls.sections.conversation.subtitle") },
    { key: "notifications", label: translate("messaging:controls.sections.notifications.label"), icon: "🔔", subtitle: translate("messaging:controls.sections.notifications.subtitle") },
    { key: "appearance", label: translate("messaging:controls.sections.appearance.label"), icon: "🖌", subtitle: translate("messaging:controls.sections.appearance.subtitle") },
    { key: "privacy", label: translate("messaging:controls.sections.privacy.label"), icon: "🔒", subtitle: translate("messaging:controls.sections.privacy.subtitle") },
    { key: "media", label: translate("messaging:controls.sections.media.label"), icon: "🖼", subtitle: translate("messaging:controls.sections.media.subtitle") },
    { key: "security", label: translate("messaging:controls.sections.security.label"), icon: "🛡", subtitle: translate("messaging:controls.sections.security.subtitle") },
    { key: "productivity", label: translate("messaging:controls.sections.productivity.label"), icon: "✓", subtitle: translate("messaging:controls.sections.productivity.subtitle") },
    { key: "storage", label: translate("messaging:controls.sections.storage.label"), icon: "◉", subtitle: translate("messaging:controls.sections.storage.subtitle") },
    { key: "accessibility", label: translate("messaging:controls.sections.accessibility.label"), icon: "♿", subtitle: translate("messaging:controls.sections.accessibility.subtitle") },
    { key: "danger", label: translate("messaging:controls.sections.danger.label"), icon: "!", subtitle: translate("messaging:controls.sections.danger.subtitle") }
  ];
}

// The second tuple slot holds a catalog key rather than English copy; it is
// resolved by `optionLabel` at render time. The first slot stays the wire value.
const OPTIONS: Record<string, SelectOption[]> = {
  mute_choice: [["off", "messaging:controls.options.muteChoice.off"], ["1_hour", "messaging:controls.options.muteChoice.hour1"], ["8_hours", "messaging:controls.options.muteChoice.hours8"], ["today", "messaging:controls.options.muteChoice.today"], ["1_week", "messaging:controls.options.muteChoice.week1"], ["forever", "messaging:controls.options.muteChoice.forever"]],
  sound: [["pulse_beam", "messaging:controls.options.sound.pulseBeam"], ["soft_orbit", "messaging:controls.options.sound.softOrbit"], ["deep_signal", "messaging:controls.options.sound.deepSignal"], ["crystal_ping", "messaging:controls.options.sound.crystalPing"], ["silent", "messaging:controls.options.sound.silent"]],
  theme: [["dark_galaxy", "messaging:controls.options.theme.darkGalaxy"], ["pulse_green", "messaging:controls.options.theme.pulseGreen"], ["deep_space", "messaging:controls.options.theme.deepSpace"], ["nebula", "messaging:controls.options.theme.nebula"], ["cyber_night", "messaging:controls.options.theme.cyberNight"], ["solar_flame", "messaging:controls.options.theme.solarFlame"], ["ocean_signal", "messaging:controls.options.theme.oceanSignal"], ["royal_purple", "messaging:controls.options.theme.royalPurple"], ["haiti_night", "messaging:controls.options.theme.haitiNight"], ["creator_gold", "messaging:controls.options.theme.creatorGold"]],
  wallpaper: [["deep_space", "messaging:controls.options.wallpaper.deepSpace"], ["neon_planet", "messaging:controls.options.wallpaper.neonPlanet"], ["galaxy_grid", "messaging:controls.options.wallpaper.galaxyGrid"], ["pulse_horizon", "messaging:controls.options.wallpaper.pulseHorizon"], ["alien_city", "messaging:controls.options.wallpaper.alienCity"], ["cosmic_ocean", "messaging:controls.options.wallpaper.cosmicOcean"], ["aurora_signal", "messaging:controls.options.wallpaper.auroraSignal"], ["dark_nebula", "messaging:controls.options.wallpaper.darkNebula"], ["star_tunnel", "messaging:controls.options.wallpaper.starTunnel"], ["minimal_black", "messaging:controls.options.wallpaper.minimalBlack"]],
  bubble_color: [["cyan", "messaging:controls.options.bubbleColor.cyan"], ["purple", "messaging:controls.options.bubbleColor.purple"], ["rose", "messaging:controls.options.bubbleColor.rose"], ["orange", "messaging:controls.options.bubbleColor.orange"], ["green", "messaging:controls.options.bubbleColor.green"], ["gold", "messaging:controls.options.bubbleColor.gold"], ["blue", "messaging:controls.options.bubbleColor.blue"]],
  font_size: [["small", "messaging:controls.options.fontSize.small"], ["medium", "messaging:controls.options.fontSize.medium"], ["large", "messaging:controls.options.fontSize.large"], ["extra_large", "messaging:controls.options.fontSize.extraLarge"]],
  density: [["compact", "messaging:controls.options.density.compact"], ["balanced", "messaging:controls.options.density.balanced"], ["relaxed", "messaging:controls.options.density.relaxed"]],
  animation_level: [["full", "messaging:controls.options.animationLevel.full"], ["balanced", "messaging:controls.options.animationLevel.balanced"], ["reduced", "messaging:controls.options.animationLevel.reduced"], ["off", "messaging:controls.options.animationLevel.off"]],
  upload_quality: [["standard", "messaging:controls.options.uploadQuality.standard"], ["high", "messaging:controls.options.uploadQuality.high"], ["original", "messaging:controls.options.uploadQuality.original"]],
  disappearing_messages: [["off", "messaging:controls.options.disappearing.off"], ["24_hours", "messaging:controls.options.disappearing.hours24"], ["7_days", "messaging:controls.options.disappearing.days7"], ["30_days", "messaging:controls.options.disappearing.days30"]],
  reminder: [["off", "messaging:controls.options.reminder.off"], ["today", "messaging:controls.options.reminder.today"], ["tomorrow", "messaging:controls.options.reminder.tomorrow"], ["next_week", "messaging:controls.options.reminder.nextWeek"]]
};

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
      connection: connected ? translate("messaging:controls.connectionConnected") : translate("messaging:controls.connectionReconnecting"),
      security_label: translate("messaging:controls.assistantSecurityLabel"),
      activity_status: translate("messaging:controls.assistantActivityStatus"),
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
  const { t } = useTranslation();
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
      setNotice(errorMessage(error, t("messaging:controls.loadFailed")));
    } finally {
      setLoading(false);
    }
  }, [assistantConversation, connected, conversationId, messages.length, t, visible]);

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
    connection: String(controlData?.stats?.connection || (connected ? t("messaging:controls.connectionConnected") : t("messaging:controls.connectionReconnecting"))),
    security_label: String(controlData?.stats?.security_label || t("messaging:controls.protectedChannel")),
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
  }), [activityStatus, connected, controlData?.stats, conversation?.member_count, conversation?.muted, conversation?.pinned, files.length, images.length, localMediaBytes, localParticipantCount, localUnread, media.length, messages.length, t, videos.length, voices.length]);
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
  }), [can, connected, isGroup, onStartCall, settings, stats, t]);
  const normalizedQuery = query.trim().toLowerCase();
  const sections = sectionMeta().map((section) => ({ ...section, rows: rows[section.key].filter((row) => !normalizedQuery || `${section.label} ${section.subtitle} ${row.label} ${row.detail || ""} ${row.value || ""}`.toLowerCase().includes(normalizedQuery)) }))
    .filter((section) => !normalizedQuery || section.rows.length > 0);
  const statusText = assistantConversation
    ? t("messaging:controls.statusAssistant")
    : conversation?.conversation_type === "direct"
    ? t("messaging:controls.statusDirect", { presence: stats.activity_status || t("messaging:controls.presenceUnavailable") })
    : t("messaging:controls.statusGroup", { members: stats.members || t("messaging:controls.membersUnknown"), type: conversation?.conversation_type || t("messaging:controls.conversationTypeFallback") });

  async function loadMembers() {
    setSavingKey("members");
    try {
      const members = await listConversationMembers(conversationId);
      setDetail({ title: t("messaging:controls.detail.membersTitle"), subtitle: t("messaging:controls.detail.participantCount", { count: members.length }), lines: members.map(memberLine) });
      setNotice("");
    } catch (error) {
      setNotice(errorMessage(error, t("messaging:controls.membersLoadFailed")));
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
        title: kind === "files" ? t("messaging:controls.detail.sharedFilesTitle") : t("messaging:controls.detail.sharedMediaTitle"),
        subtitle: t("messaging:controls.detail.itemCount", { count: items.length }),
        lines: items.map(mediaLine)
      });
      setNotice("");
    } catch (error) {
      setNotice(errorMessage(error, t("messaging:controls.sharedMediaLoadFailed")));
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
        title: t("messaging:controls.detail.sharedLinksTitle"),
        subtitle: t("messaging:controls.detail.linkCount", { count: items.length }),
        lines: items.map((item) => `${String(item.domain || t("messaging:controls.detail.linkFallback"))} · ${String(item.url || "")}`)
      });
      setNotice("");
    } catch (error) {
      setNotice(errorMessage(error, t("messaging:controls.sharedLinksLoadFailed")));
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
        title: t("messaging:controls.detail.pinnedTitle"),
        subtitle: t("messaging:controls.detail.pinnedCount", { count: items.length }),
        lines: items.map((message) => `${message.sender_display_name || t("messaging:controls.pulseMember")}: ${message.body || `[${message.message_type || t("messaging:controls.attachmentFallback")}]`}`)
      });
      setNotice("");
    } catch (error) {
      setNotice(errorMessage(error, t("messaging:controls.pinnedLoadFailed")));
    } finally {
      setSavingKey("");
    }
  }

  async function runSearch() {
    const clean = chatSearch.trim();
    if (!clean) {
      setNotice(t("messaging:controls.searchNeedsPhrase"));
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
          title: t("messaging:controls.detail.searchTitle"),
          subtitle: t("messaging:controls.detail.localResultCount", { count: results.length, query: clean }),
          kind: "search",
          lines: results.map((message) => `${message.is_mine ? t("messaging:controls.you") : PULSE_AI_DISPLAY_NAME}: ${message.body || message.content || message.text || t("messaging:controls.messageFallback")}`)
        });
        setNotice("");
        return;
      }
      const results = await searchConversationMessages(conversationId, clean);
      setDetail({
        title: t("messaging:controls.detail.searchTitle"),
        subtitle: t("messaging:controls.detail.resultCount", { count: results.length, query: clean }),
        kind: "search",
        lines: results.map((message) => `${message.sender_display_name || (message.is_mine ? t("messaging:controls.you") : t("messaging:controls.pulseMember"))}: ${message.body || `[${message.message_type || t("messaging:controls.attachmentFallback")}]`}`)
      });
      setNotice("");
    } catch (error) {
      setNotice(errorMessage(error, t("messaging:controls.searchFailed")));
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
          sender: message.is_mine ? t("messaging:controls.you") : PULSE_AI_DISPLAY_NAME,
          body: message.body || message.content || message.text || "",
          created_at: message.created_at
        }))
      }, null, 2);
      await Share.share({ title: t("messaging:controls.assistantExportTitle"), message: payload });
      setNotice(t("messaging:controls.assistantExportOpened"));
      return;
    }
    setSavingKey("export-chat");
    try {
      const data = await exportConversationControlData(conversationId);
      const payload = JSON.stringify(data.export || {}, null, 2);
      await Share.share({ title: data.filename || t("messaging:controls.exportShareTitle", { title }), message: payload || t("messaging:controls.exportEmpty") });
      setNotice(t("messaging:controls.exportOpened"));
    } catch (error) {
      setNotice(errorMessage(error, t("messaging:controls.exportFailed")));
    } finally {
      setSavingKey("");
    }
  }

  async function clearLocalCache() {
    await AsyncStorage.removeItem(`pulsesoc.native.messenger.v2.messages.${conversationId}`);
    setNotice(t("messaging:controls.cacheCleared"));
  }

  async function saveSetting(row: RowSpec, nextValue: boolean | string) {
    if (!row.setting) return;
    if (assistantConversation) {
      setNotice(t("messaging:controls.assistantSettingsUnsupported"));
      return;
    }
    const key = `${row.setting.section}.${row.setting.key}`;
    setSavingKey(key);
    try {
      const data = await updateConversationControlSetting(conversationId, row.setting.section, row.setting.key, nextValue);
      setControlData((current) => ({ ...(current || {}), ...data, settings: data.settings || current?.settings }));
      setNotice(t("messaging:controls.settingSaved", { label: row.label }));
    } catch (error) {
      setNotice(errorMessage(error, t("messaging:controls.settingSaveFailed", { label: row.label })));
    } finally {
      setSavingKey("");
    }
  }

  async function promptServerAction(action: "create-note" | "create-task") {
    const label = action === "create-note" ? t("messaging:controls.conversationNote") : t("messaging:controls.conversationTask");
    const fallback = () => setNotice(t("messaging:controls.promptUnavailable", { label }));
    const prompt = (Alert as unknown as { prompt?: (title: string, message?: string, callbackOrButtons?: ((text: string) => void) | Array<Record<string, unknown>>, type?: string, defaultValue?: string, keyboardType?: string) => void }).prompt;
    if (!prompt) return fallback();
    prompt(label, t("messaging:controls.promptBody"), async (body: string) => {
      if (!body?.trim()) return;
      setSavingKey(action);
      try {
        const data = await runConversationControlAction(conversationId, action, body.trim());
        setNotice(data.message || t("messaging:controls.settingSaved", { label }));
      } catch (error) {
        setNotice(errorMessage(error, t("messaging:controls.settingSaveFailed", { label })));
      } finally {
        setSavingKey("");
      }
    });
  }

  async function executeAction(row: RowSpec) {
    if (row.disabled) {
      setNotice(row.disabledReason || t("messaging:controls.rowUnavailable", { label: row.label }));
      return;
    }
    if (!connected && row.action !== "clear-cache" && row.action !== "search-chat") {
      setNotice(t("messaging:controls.offlineReason"));
      return;
    }
    const run = async () => {
      switch (row.action) {
        case "members":
          if (assistantConversation) {
            setDetail({ title: t("messaging:controls.detail.participantsTitle"), subtitle: t("messaging:controls.detail.assistantChatSubtitle"), lines: [t("messaging:controls.you"), t("messaging:controls.assistantParticipant")] });
            return;
          }
          return loadMembers();
        case "shared-media":
          if (assistantConversation) {
            setNotice(t("messaging:controls.assistantAttachmentsDisabled"));
            return;
          }
          return loadSharedMedia("all");
        case "shared-files":
          if (assistantConversation) {
            setNotice(t("messaging:controls.assistantFilesDisabled"));
            return;
          }
          return loadSharedMedia("files");
        case "shared-links":
          if (assistantConversation) {
            const links = messages
              .map((message) => message.body || message.content || message.text || "")
              .flatMap((body) => body.match(/https?:\/\/\S+/g) || []);
            setDetail({ title: t("messaging:controls.detail.sharedLinksTitle"), subtitle: t("messaging:controls.detail.localLinkCount", { count: links.length }), lines: links });
            return;
          }
          return loadLinks();
        case "pinned-messages":
          return loadPinnedMessages();
        case "search-chat":
          setDetail({ title: t("messaging:controls.detail.searchTitle"), subtitle: t("messaging:controls.detail.searchSubtitle"), kind: "search", lines: [] });
          return;
        case "message-stats":
          setDetail({ title: t("messaging:controls.detail.statsTitle"), subtitle: t("messaging:controls.detail.statsSubtitle"), lines: statsLines(stats) });
          return;
        case "storage":
          setOpen((current) => Array.from(new Set([...current, "storage"])));
          setDetail({ title: t("messaging:controls.detail.storageTitle"), subtitle: t("messaging:controls.detail.storageSubtitle"), lines: storageStatsLines(stats) });
          return;
        case "export-chat":
          return exportChat();
        case "start-audio-call":
          if (!onStartCall) {
            setNotice(t("messaging:controls.audioCallsDisabled"));
            return;
          }
          onClose();
          return onStartCall("audio");
        case "start-video-call":
          if (!onStartCall) {
            setNotice(t("messaging:controls.videoCallsDisabled"));
            return;
          }
          onClose();
          return onStartCall("video");
        case "mute": {
          if (assistantConversation) {
            setNotice(t("messaging:controls.assistantMuteUnsupported"));
            return;
          }
          setSavingKey("mute");
          const data = await muteConversation(conversationId);
          setNotice(data.message || (data.muted ? t("messaging:controls.conversationMuted") : t("messaging:controls.conversationUnmuted")));
          await refreshControlCenter();
          setSavingKey("");
          return;
        }
        case "pin": {
          if (assistantConversation) {
            setNotice(t("messaging:controls.assistantPinned"));
            return;
          }
          setSavingKey("pin");
          const data = await pinConversation(conversationId, !stats.pinned);
          setNotice(data.message || (data.pinned ? t("messaging:controls.conversationPinned") : t("messaging:controls.conversationUnpinned")));
          await refreshControlCenter();
          setSavingKey("");
          return;
        }
        case "archive": {
          if (assistantConversation) {
            setNotice(t("messaging:controls.assistantArchiveUnsupported"));
            return;
          }
          setSavingKey("archive");
          const data = await archiveConversation(conversationId);
          setNotice(data.message || t("messaging:controls.conversationArchived"));
          setSavingKey("");
          onClose();
          return;
        }
        case "mark-unread": {
          if (assistantConversation) {
            setNotice(t("messaging:controls.assistantUnreadUnsupported"));
            return;
          }
          setSavingKey("mark-unread");
          const data = await markConversationUnread(conversationId);
          setNotice(data.message || t("messaging:controls.conversationMarkedUnread"));
          await refreshControlCenter();
          setSavingKey("");
          return;
        }
        case "clear-cache":
          return Alert.alert(t("messaging:controls.clearCacheTitle"), t("messaging:controls.clearCacheBody"), [{ text: t("common:actions.cancel"), style: "cancel" }, { text: t("messaging:controls.clearCacheConfirm"), style: "destructive", onPress: () => clearLocalCache().catch(() => setNotice(t("messaging:controls.cacheClearFailed"))) }]);
        case "security-status":
          setDetail({
            title: t("messaging:controls.detail.securityTitle"),
            subtitle: stats.security_label,
            lines: [
              t("messaging:controls.detail.securityLine1"),
              t("messaging:controls.detail.securityLine2"),
              t("messaging:controls.detail.securityLine3")
            ]
          });
          return;
        case "verify-contact": {
          const member = (controlData?.members || []).find((item) => item.display_name && item.user_id);
          setDetail({ title: t("messaging:controls.detail.verifyTitle"), subtitle: t("messaging:controls.detail.verifySubtitle"), lines: [member ? t("messaging:controls.detail.verifyAuthorized", { name: member.display_name }) : t("messaging:controls.detail.verifyNoPeer")] });
          return;
        }
        case "report-conversation":
        case "block-user":
          onOpenSafety(row.action === "block-user" ? "blocks" : "reports");
          setSavingKey(row.action);
          try {
            const data = await runConversationControlAction(conversationId, row.action);
            setNotice(data.message || t("messaging:controls.safetyActionSent"));
          } catch (error) {
            setNotice(errorMessage(error, t("messaging:controls.safetyActionFailed")));
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
          setNotice(data.message || t("messaging:controls.actionCompleted"));
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
          setNotice(row.disabledReason || t("messaging:controls.rowNotEnabled", { label: row.label }));
      }
    };
    if (row.dangerConfirm) {
      Alert.alert(row.label, row.dangerConfirm, [{ text: t("common:actions.cancel"), style: "cancel" }, { text: row.destructive ? t("common:actions.continue") : t("common:actions.confirm"), style: row.destructive ? "destructive" : "default", onPress: () => run().catch((error) => { setSavingKey(""); setNotice(errorMessage(error, t("messaging:controls.rowFailed", { label: row.label }))); }) }]);
      return;
    }
    try {
      await run();
    } catch (error) {
      setSavingKey("");
      setNotice(errorMessage(error, t("messaging:controls.rowFailed", { label: row.label })));
    }
  }

  return (
    <Modal transparent animationType="slide" visible={visible} onRequestClose={onClose} statusBarTranslucent>
      <View style={styles.backdrop}>
        <PulseCommandPanel style={styles.sheet}>
          <View style={styles.handle} />
          <View style={styles.header}>
            <View style={styles.gear}><Text style={styles.gearText}>⚙</Text></View>
            <View style={styles.headerCopy}><Text style={styles.title}>{t("messaging:controls.title")}</Text><Text style={styles.subtitle}>{t("messaging:controls.subtitle")}</Text></View>
            <Pressable accessibilityRole="button" accessibilityLabel={t("messaging:controls.a11yRefresh")} style={styles.headerButton} onPress={refreshControlCenter} disabled={loading}><Text style={styles.headerButtonText}>↻</Text></Pressable>
            <Pressable accessibilityRole="button" accessibilityLabel={t("messaging:controls.a11yClose")} style={styles.close} onPress={onClose}><Text style={styles.closeText}>×</Text></Pressable>
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
                <Quick label={t("messaging:controls.rows.searchChat")} icon="⌕" disabled={!can("search")} onPress={() => executeAction({ label: t("messaging:controls.rows.searchChat"), icon: "⌕", action: "search-chat", disabled: !can("search"), disabledReason: t("messaging:controls.quickReasons.search") })} />
                <Quick label={t("messaging:controls.rows.sharedMedia")} icon="▧" disabled={!can("shared_media")} onPress={() => executeAction({ label: t("messaging:controls.rows.sharedMedia"), icon: "▧", action: "shared-media", disabled: !can("shared_media"), disabledReason: t("messaging:controls.quickReasons.sharedMedia") })} />
                <Quick label={assistantConversation ? t("messaging:controls.quick.identity") : t("messaging:controls.quick.members")} icon="♟" disabled={!can("members")} onPress={() => executeAction({ label: assistantConversation ? t("messaging:controls.quick.identity") : t("messaging:controls.quick.members"), icon: "♟", action: "members", disabled: !can("members"), disabledReason: t("messaging:controls.quickReasons.members") })} />
              </View>
              <View style={styles.actionGrid}>
                <Quick label={t("messaging:controls.quick.audioCall")} icon="☎" disabled={!can("voice_call") || !onStartCall} onPress={() => executeAction({ label: t("messaging:controls.quick.audioCall"), icon: "☎", action: "start-audio-call", disabled: !can("voice_call") || !onStartCall, disabledReason: t("messaging:controls.audioCallsDisabled") })} />
                <Quick label={t("messaging:controls.quick.videoCall")} icon="▣" disabled={!can("video_call") || !onStartCall} onPress={() => executeAction({ label: t("messaging:controls.quick.videoCall"), icon: "▣", action: "start-video-call", disabled: !can("video_call") || !onStartCall, disabledReason: t("messaging:controls.videoCallsDisabled") })} />
                <Quick label={stats.muted ? t("messaging:inbox.unmute") : t("messaging:inbox.mute")} icon="🔕" busy={savingKey === "mute"} disabled={!can("mute")} onPress={() => executeAction({ label: stats.muted ? t("messaging:controls.rows.unmuteConversation") : t("messaging:controls.rows.muteConversation"), icon: "🔕", action: "mute", disabled: !can("mute"), disabledReason: t("messaging:controls.quickReasons.mute") })} />
                <Quick label={stats.pinned ? t("messaging:controls.quick.unpin") : t("messaging:controls.quick.pin")} icon="📌" busy={savingKey === "pin"} disabled={!can("pin")} onPress={() => executeAction({ label: stats.pinned ? t("messaging:controls.rows.unpinConversation") : t("messaging:controls.rows.pinConversation"), icon: "📌", action: "pin", disabled: !can("pin"), disabledReason: t("messaging:controls.quickReasons.pin") })} />
                <Quick label={t("messaging:inbox.archive")} icon="▤" busy={savingKey === "archive"} disabled={!can("archive")} onPress={() => executeAction({ label: t("messaging:controls.rows.archiveConversation"), icon: "▤", action: "archive", dangerConfirm: t("messaging:controls.confirm.archiveInbox"), disabled: !can("archive"), disabledReason: t("messaging:controls.quickReasons.archive") })} />
              </View>
              <View style={styles.metrics}>
                <DashboardMetric label={t("messaging:controls.metrics.protection")} value={stats.security_label} />
                <DashboardMetric label={t("messaging:controls.metrics.members")} value={stats.members ? String(stats.members) : t("messaging:controls.unavailable")} />
                <DashboardMetric label={t("messaging:controls.metrics.mediaFiles")} value={String(stats.media_files)} />
                <DashboardMetric label={t("messaging:controls.metrics.knownStorage")} value={formatFileSize(stats.storage_used_bytes)} />
                <DashboardMetric label={t("messaging:controls.metrics.unread")} value={String(stats.unread)} />
                <DashboardMetric label={t("messaging:controls.metrics.connection")} value={stats.connection} />
              </View>
            </View>
            <View style={styles.searchWrap}><Text style={styles.searchIcon}>⌕</Text><TextInput accessibilityLabel={t("messaging:controls.a11ySearchSettings")} value={query} onChangeText={setQuery} placeholder={t("messaging:controls.searchSettingsPlaceholder")} placeholderTextColor={colors.muted} style={styles.search} /></View>
            {detail ? <DetailPanelView detail={detail} chatSearch={chatSearch} onChatSearchChange={setChatSearch} chatSearchLoading={chatSearchLoading} onRunSearch={runSearch} onClose={() => setDetail(null)} /> : null}
            {sections.length ? sections.map((section) => {
              const expanded = normalizedQuery ? true : open.includes(section.key);
              return <View key={section.key} style={[styles.section, section.key === "danger" && styles.dangerSection]}>
                <Pressable accessibilityRole="button" accessibilityState={{ expanded }} style={styles.sectionHeader} onPress={() => setOpen((current) => current.includes(section.key) ? current.filter((item) => item !== section.key) : [...current, section.key])}>
                  <View style={styles.sectionIcon}><Text style={styles.sectionIconText}>{section.icon}</Text></View><View style={styles.sectionCopy}><Text style={styles.sectionTitle}>{section.label}</Text><Text style={styles.sectionSubtitle}>{section.subtitle}</Text></View><Text style={styles.chevron}>{expanded ? "⌃" : "⌄"}</Text>
                </Pressable>
                {expanded ? section.rows.map((row) => <SettingRow key={`${section.key}-${row.label}`} row={row} settings={settings} saving={savingKey === settingKey(row.setting) || savingKey === row.action} onSaveSetting={saveSetting} onPress={() => executeAction(row)} />) : null}
              </View>;
            }) : <Text style={styles.empty}>{t("messaging:controls.noSettingsMatch", { query })}</Text>}
            {notice ? <Pressable accessibilityRole="button" accessibilityLabel={t("messaging:controls.a11yDismissNotice")} onPress={() => setNotice("")}><Text accessibilityLiveRegion="polite" style={styles.notice}>{notice}</Text></Pressable> : null}
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
  const unavailable = (reason: string): Pick<RowSpec, "disabled" | "disabledReason"> => ({ disabled: true, disabledReason: connected ? reason : translate("messaging:controls.offlineReason") });
  return {
    conversation: [
      action(translate("messaging:controls.rows.viewMembers"), "👥", "members", translate("messaging:controls.details.viewMembers", { count: stats.members, members: stats.members || translate("messaging:controls.membersUnknown") }), !can("members") ? unavailable(translate("messaging:controls.reasons.members")) : undefined),
      action(translate("messaging:controls.rows.sharedMedia"), "🖼", "shared-media", translate("messaging:controls.details.sharedMedia", { count: stats.media_files }), !can("shared_media") ? unavailable(translate("messaging:controls.reasons.sharedMedia")) : undefined),
      action(translate("messaging:controls.rows.pinnedMessages"), "📌", "pinned-messages", translate("messaging:controls.details.pinnedMessages")),
      action(translate("messaging:controls.rows.searchChat"), "⌕", "search-chat", translate("messaging:controls.details.searchChat"), !can("search") ? unavailable(translate("messaging:controls.reasons.search")) : undefined),
      action(translate("messaging:controls.rows.messageStats"), "▥", "message-stats", translate("messaging:controls.details.messageStats", { count: stats.messages })),
      action(translate("messaging:controls.rows.mediaStorage"), "◉", "storage", translate("messaging:controls.details.mediaStorage", { size: formatFileSize(stats.storage_used_bytes) })),
      action(translate("messaging:controls.rows.exportChat"), "⇧", "export-chat", translate("messaging:controls.details.exportChat"), !can("export_chat") ? unavailable(translate("messaging:controls.reasons.export")) : undefined, translate("messaging:controls.confirm.exportChat"))
    ],
    notifications: [
      setting(translate("messaging:controls.rows.muteConversation"), "🔕", "notifications", "mute_choice", "select", translate("messaging:controls.details.muteChoice"), OPTIONS.mute_choice),
      setting(translate("messaging:controls.rows.notificationSound"), "♪", "notifications", "sound", "select", translate("messaging:controls.details.sound"), OPTIONS.sound),
      setting(translate("messaging:controls.rows.lockScreen"), "▣", "notifications", "lock_screen", "toggle", translate("messaging:controls.details.lockScreen")),
      setting(translate("messaging:controls.rows.messagePreview"), "◉", "notifications", "message_preview", "toggle", translate("messaging:controls.details.messagePreviewNotifications")),
      setting(translate("messaging:controls.rows.mentionNotifications"), "@", "notifications", "mentions", "toggle", translate("messaging:controls.details.mentions")),
      setting(translate("messaging:controls.rows.reactionNotifications"), "✦", "notifications", "reactions", "toggle", translate("messaging:controls.details.reactions")),
      setting(translate("messaging:controls.rows.typingNotifications"), "…", "notifications", "typing", "toggle", translate("messaging:controls.details.typingNotifications")),
      setting(translate("messaging:controls.rows.readReceiptNotifications"), "✓", "notifications", "read_receipts", "toggle", translate("messaging:controls.details.readReceiptNotifications"))
    ],
    appearance: [
      setting(translate("messaging:controls.rows.theme"), "◌", "appearance", "theme", "select", translate("messaging:controls.details.theme"), OPTIONS.theme),
      setting(translate("messaging:controls.rows.wallpaper"), "▧", "appearance", "wallpaper", "select", translate("messaging:controls.details.wallpaper"), OPTIONS.wallpaper),
      setting(translate("messaging:controls.rows.bubbleColor"), "●", "appearance", "bubble_color", "select", translate("messaging:controls.details.bubbleColor"), OPTIONS.bubble_color),
      setting(translate("messaging:controls.rows.fontSize"), "Aa", "appearance", "font_size", "select", translate("messaging:controls.details.fontSize"), OPTIONS.font_size),
      setting(translate("messaging:controls.rows.chatDensity"), "↕", "appearance", "density", "select", translate("messaging:controls.details.density"), OPTIONS.density),
      setting(translate("messaging:controls.rows.animationLevel"), "✺", "appearance", "animation_level", "select", translate("messaging:controls.details.animationLevel"), OPTIONS.animation_level),
      setting(translate("messaging:controls.rows.reduceParticles"), "·", "appearance", "reduce_particles", "toggle", translate("messaging:controls.details.reduceParticles")),
      setting(translate("messaging:controls.rows.highContrast"), "◐", "appearance", "high_contrast", "toggle", translate("messaging:controls.details.highContrastAppearance"))
    ],
    privacy: [
      setting(translate("messaging:controls.rows.readReceipts"), "✓✓", "privacy", "read_receipts", "toggle", translate("messaging:controls.details.readReceipts")),
      setting(translate("messaging:controls.rows.typingIndicator"), "…", "privacy", "typing_indicator", "toggle", translate("messaging:controls.details.typingIndicator")),
      setting(translate("messaging:controls.rows.onlineStatus"), "●", "privacy", "online_status", "toggle", translate("messaging:controls.details.onlineStatus")),
      setting(translate("messaging:controls.rows.lastSeen"), "◷", "privacy", "last_seen", "toggle", translate("messaging:controls.details.lastSeen")),
      setting(translate("messaging:controls.rows.messagePreview"), "◉", "privacy", "message_preview", "toggle", translate("messaging:controls.details.messagePreviewPrivacy")),
      setting(translate("messaging:controls.rows.disappearingMessages"), "⌛", "privacy", "disappearing_messages", "select", translate("messaging:controls.details.disappearingMessages"), OPTIONS.disappearing_messages),
      setting(translate("messaging:controls.rows.privacyLock"), "▣", "privacy", "privacy_lock", "toggle", translate("messaging:controls.details.serverOptional")),
      setting(translate("messaging:controls.rows.hiddenConversation"), "◌", "privacy", "hidden_conversation", "toggle", translate("messaging:controls.details.serverOptional"))
    ],
    media: [
      setting(translate("messaging:controls.rows.autoDownloadPhotos"), "▧", "media", "auto_download_photos", "toggle", translate("messaging:controls.details.autoDownloadPhotos")),
      setting(translate("messaging:controls.rows.autoDownloadVideos"), "▶", "media", "auto_download_videos", "toggle", translate("messaging:controls.details.autoDownloadVideos")),
      setting(translate("messaging:controls.rows.autoDownloadVoice"), "🎙", "media", "auto_download_voice", "toggle", translate("messaging:controls.details.autoDownloadVoice")),
      setting(translate("messaging:controls.rows.uploadQuality"), "HD", "media", "upload_quality", "select", translate("messaging:controls.details.uploadQuality"), OPTIONS.upload_quality),
      setting(translate("messaging:controls.rows.autoSaveCamera"), "◎", "media", "auto_save_camera", "toggle", translate("messaging:controls.details.autoSaveCamera")),
      action(translate("messaging:controls.rows.clearMediaCache"), "⌫", "clear-cache", translate("messaging:controls.details.clearMediaCache"), undefined, translate("messaging:controls.confirm.clearCache")),
      action(translate("messaging:controls.rows.sharedLinks"), "↗", "shared-links", translate("messaging:controls.details.detectedLinks", { count: stats.links })),
      action(translate("messaging:controls.rows.sharedFiles"), "▤", "shared-files", translate("messaging:controls.details.fileCount", { count: stats.files }))
    ],
    security: [
      action(translate("messaging:controls.rows.encryptionStatus"), "🛡", "security-status", stats.security_label),
      action(translate("messaging:controls.rows.verifyContact"), "◇", "verify-contact", isGroup ? translate("messaging:controls.details.verifyContactGroup") : translate("messaging:controls.details.verifyContactDirect"), isGroup ? unavailable(translate("messaging:controls.reasons.verifyContact")) : undefined),
      action(translate("messaging:controls.rows.trustedDevices"), "▣", "unavailable", translate("messaging:controls.details.accountSecurity"), unavailable(translate("messaging:controls.reasons.trustedDevices"))),
      action(translate("messaging:controls.rows.activeSessions"), "◉", "unavailable", translate("messaging:controls.details.accountSecurity"), unavailable(translate("messaging:controls.reasons.activeSessions"))),
      action(translate("messaging:controls.rows.securityLog"), "▤", "unavailable", translate("messaging:controls.details.accountSecurity"), unavailable(translate("messaging:controls.reasons.securityLog"))),
      action(translate("messaging:controls.rows.reportConversation"), "!", "report-conversation", translate("messaging:controls.details.reportConversation"), !can("report") ? unavailable(translate("messaging:controls.reasons.report")) : undefined, translate("messaging:controls.confirm.report")),
      action(translate("messaging:controls.rows.blockUser"), "⊘", "block-user", translate("messaging:controls.details.blockUser"), (!can("block") || isGroup) ? unavailable(translate("messaging:controls.reasons.block")) : undefined, translate("messaging:controls.confirm.block"))
    ],
    productivity: [
      action(stats.pinned ? translate("messaging:controls.rows.unpinConversation") : translate("messaging:controls.rows.pinConversation"), "📌", "pin", translate("messaging:controls.details.pin"), !can("pin") ? unavailable(translate("messaging:controls.reasons.pin")) : undefined),
      action(translate("messaging:controls.rows.archiveConversation"), "▤", "archive", translate("messaging:controls.details.archive"), !can("archive") ? unavailable(translate("messaging:controls.reasons.archive")) : undefined, translate("messaging:controls.confirm.archive")),
      action(translate("messaging:controls.rows.markUnread"), "◌", "mark-unread", translate("messaging:controls.details.markUnread"), !can("mark_unread") ? unavailable(translate("messaging:controls.reasons.markUnread")) : undefined),
      setting(translate("messaging:controls.rows.favoriteConversation"), "★", "productivity", "favorite", "toggle", translate("messaging:controls.details.favorite")),
      setting(translate("messaging:controls.rows.reminder"), "⏱", "productivity", "reminder", "select", translate("messaging:controls.details.reminder"), OPTIONS.reminder),
      action(translate("messaging:controls.rows.createNote"), "✎", "create-note", translate("messaging:controls.details.controlEndpoint")),
      action(translate("messaging:controls.rows.createTask"), "☑", "create-task", translate("messaging:controls.details.controlEndpoint"))
    ],
    storage: [
      valueRow(translate("messaging:controls.rows.conversationSize"), "◉", formatFileSize(stats.storage_used_bytes), translate("messaging:controls.details.conversationSize")),
      valueRow(translate("messaging:controls.rows.photos"), "▧", String(stats.photos), translate("messaging:controls.details.photos")),
      valueRow(translate("messaging:controls.rows.videos"), "▶", String(stats.videos), translate("messaging:controls.details.videos")),
      valueRow(translate("messaging:controls.rows.voiceMessages"), "🎙", String(stats.voice), translate("messaging:controls.details.voiceMessages")),
      valueRow(translate("messaging:controls.rows.files"), "▤", String(stats.files), translate("messaging:controls.details.files")),
      action(translate("messaging:controls.rows.links"), "↗", "shared-links", translate("messaging:controls.details.detectedLinks", { count: stats.links })),
      action(translate("messaging:controls.rows.clearCache"), "⌫", "clear-cache", translate("messaging:controls.details.clearCache"), undefined, translate("messaging:controls.confirm.clearCache"))
    ],
    accessibility: [
      setting(translate("messaging:controls.rows.largeText"), "Aa", "accessibility", "large_text", "toggle", translate("messaging:controls.details.accessibility")),
      setting(translate("messaging:controls.rows.reduceMotion"), "↘", "accessibility", "reduce_motion", "toggle", translate("messaging:controls.details.accessibility")),
      setting(translate("messaging:controls.rows.highContrast"), "◐", "accessibility", "high_contrast", "toggle", translate("messaging:controls.details.accessibility")),
      setting(translate("messaging:controls.rows.voiceReader"), "🔊", "accessibility", "voice_reader", "toggle", translate("messaging:controls.details.accessibility")),
      setting(translate("messaging:controls.rows.speechToText"), "🎙", "accessibility", "speech_to_text", "toggle", translate("messaging:controls.details.accessibility")),
      setting(translate("messaging:controls.rows.textToSpeech"), "Aa", "accessibility", "text_to_speech", "toggle", translate("messaging:controls.details.accessibility")),
      setting(translate("messaging:controls.rows.hapticFeedback"), "✦", "accessibility", "haptic_feedback", "toggle", translate("messaging:controls.details.haptic"))
    ],
    danger: [
      danger(translate("messaging:controls.rows.clearConversation"), "⌫", "clear-conversation", translate("messaging:controls.confirm.clearConversation")),
      danger(translate("messaging:controls.rows.deleteConversation"), "×", "delete-conversation", translate("messaging:controls.confirm.deleteConversation")),
      danger(translate("messaging:controls.rows.leaveGroup"), "⇠", "leave-group", translate("messaging:controls.confirm.leaveGroup"), isGroup ? undefined : unavailable(translate("messaging:controls.reasons.leaveGroup"))),
      danger(translate("messaging:controls.rows.blockUser"), "⊘", "block-user", translate("messaging:controls.confirm.block"), (!can("block") || isGroup) ? unavailable(translate("messaging:controls.reasons.block")) : undefined),
      danger(translate("messaging:controls.rows.reportSpam"), "!", "report-conversation", translate("messaging:controls.confirm.report"), !can("report") ? unavailable(translate("messaging:controls.reasons.report")) : undefined),
      danger(translate("messaging:controls.rows.deleteMedia"), "⌧", "delete-media", translate("messaging:controls.confirm.deleteMedia")),
      danger(translate("messaging:controls.rows.resetSettings"), "↺", "reset-settings", translate("messaging:controls.confirm.resetSettings"))
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
    connection: translate("messaging:controls.unavailable"),
    security_label: translate("messaging:controls.protectedChannel"),
    activity_status: translate("messaging:controls.unavailable"),
    muted: false,
    pinned: false
  };
}

function action(label: string, icon: string, controlAction: ControlAction, detail: string, availability?: Pick<RowSpec, "disabled" | "disabledReason">, dangerConfirm?: string): RowSpec {
  return { label, icon, action: controlAction, detail, dangerConfirm, ...availability };
}

function danger(label: string, icon: string, controlAction: ControlAction, confirm: string, availability?: Pick<RowSpec, "disabled" | "disabledReason">): RowSpec {
  return { ...action(label, icon, controlAction, translate("messaging:controls.details.destructive"), availability, confirm), danger: true, destructive: true };
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
  const optionKey = setting.options?.find(([item]) => item === value)?.[1];
  return optionKey ? translate(optionKey) : humanize(value) || translate("messaging:controls.unsetOption");
}

function memberLine(member: ConversationControlMember) {
  const role = member.role ? ` · ${humanize(member.role)}` : "";
  const presence = member.presence ? ` · ${humanize(member.presence)}` : "";
  return `${member.display_name || translate("messaging:controls.pulseMember")}${role}${presence}`;
}

function mediaLine(item: ConversationControlMediaItem) {
  const size = formatFileSize(Number(item.file_size_bytes || 0));
  const type = humanize(item.media_type || item.mime_type || "file");
  const sender = item.sender_display_name || translate("messaging:controls.pulseMember");
  return `${type} · ${size} · ${sender}${item.body_preview ? ` · ${item.body_preview}` : ""}`;
}

function statsLines(stats: ReturnType<typeof statsFallback>) {
  return [
    translate("messaging:controls.stats.messages", { value: stats.messages }),
    translate("messaging:controls.stats.mediaFiles", { value: stats.media_files }),
    translate("messaging:controls.stats.photos", { value: stats.photos }),
    translate("messaging:controls.stats.videos", { value: stats.videos }),
    translate("messaging:controls.stats.voiceMessages", { value: stats.voice }),
    translate("messaging:controls.stats.files", { value: stats.files }),
    translate("messaging:controls.stats.links", { value: stats.links }),
    translate("messaging:controls.stats.storage", { value: formatFileSize(stats.storage_used_bytes) }),
    translate("messaging:controls.stats.unread", { value: stats.unread }),
    translate("messaging:controls.stats.connection", { value: stats.connection })
  ];
}

// The storage detail panel previously re-filtered `statsLines` with an English
// regex, which stopped matching as soon as the copy was translated. The subset
// is now produced directly so it stays correct in every locale.
function storageStatsLines(stats: ReturnType<typeof statsFallback>) {
  return [
    translate("messaging:controls.stats.mediaFiles", { value: stats.media_files }),
    translate("messaging:controls.stats.photos", { value: stats.photos }),
    translate("messaging:controls.stats.videos", { value: stats.videos }),
    translate("messaging:controls.stats.voiceMessages", { value: stats.voice }),
    translate("messaging:controls.stats.files", { value: stats.files }),
    translate("messaging:controls.stats.storage", { value: formatFileSize(stats.storage_used_bytes) })
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
  const { t } = useTranslation();
  return (
    <View style={styles.detailPanel}>
      <View style={styles.detailHeader}><View><Text style={styles.detailTitle}>{detail.title}</Text>{detail.subtitle ? <Text style={styles.detailSubtitle}>{detail.subtitle}</Text> : null}</View><Pressable accessibilityRole="button" accessibilityLabel={t("messaging:controls.a11yClosePanel", { title: detail.title })} onPress={onClose}><Text style={styles.detailClose}>×</Text></Pressable></View>
      {detail.kind === "search" ? <View style={styles.chatSearchRow}><TextInput accessibilityLabel={t("messaging:controls.a11ySearchConversation")} placeholder={t("messaging:controls.searchChatPlaceholder")} placeholderTextColor={colors.muted} value={chatSearch} onChangeText={onChatSearchChange} style={styles.chatSearchInput} onSubmitEditing={onRunSearch} /><Pressable accessibilityRole="button" accessibilityLabel={t("messaging:controls.a11yRunSearch")} style={styles.chatSearchButton} onPress={onRunSearch} disabled={chatSearchLoading}>{chatSearchLoading ? <ActivityIndicator color="#001118" /> : <Text style={styles.chatSearchButtonText}>{t("common:actions.search")}</Text>}</Pressable></View> : null}
      {detail.lines?.length ? detail.lines.slice(0, 80).map((line, index) => <Text key={`${detail.title}-${index}`} style={styles.detailLine}>{line}</Text>) : <Text style={styles.detailEmpty}>{t("messaging:controls.detail.noItems")}</Text>}
    </View>
  );
}

function SettingRow({ row, settings, saving, onSaveSetting, onPress }: { row: RowSpec; settings: ConversationControlSettings; saving: boolean; onSaveSetting: (row: RowSpec, value: boolean | string) => void; onPress: () => void }) {
  const { t } = useTranslation();
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
        <Text style={styles.rowDetail} numberOfLines={2}>{row.disabled ? row.disabledReason || t("messaging:controls.rowUnavailableFallback") : row.detail || t("messaging:controls.rowDetailFallback")}</Text>
      </View>
      {saving ? <ActivityIndicator color={colors.accent} /> : isToggle ? <Switch value={Boolean(current)} onValueChange={(value) => onSaveSetting(row, value)} disabled={row.disabled} trackColor={{ false: "#183044", true: "#118e79" }} thumbColor={current ? colors.accent : "#cfe0f5"} /> : rightValue ? <Text style={styles.rowValue}>{rightValue}</Text> : <Text style={styles.chevron}>{row.disabled ? t("messaging:controls.unavailable") : "›"}</Text>}
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
