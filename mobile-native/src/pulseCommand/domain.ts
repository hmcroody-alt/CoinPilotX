import { MessengerConversation, MessengerMessage, MessengerPresence } from "../api/messenger";
import { PulseGroup, PulseGroupAsset, PulseGroupInvitation, PulseGroupMember, PulseRoom, PulseRoomParticipant } from "../api/groups";
import { compactPreview, formatShortTime } from "../utils/format";
import { isSingleEmoji } from "../emoji/grapheme";

export type PulseCommandActionKey =
  | "reply"
  | "react"
  | "retry"
  | "copy"
  | "forward"
  | "edit"
  | "save"
  | "share"
  | "translate"
  | "info"
  | "openLink"
  | "copyLink"
  | "shareLink"
  | "viewMedia"
  | "saveMedia"
  | "report"
  | "safety"
  | "deleteSelf"
  | "deleteEveryone";

export type PulseCommandCommunityActionKey =
  | "join"
  | "leave"
  | "openChat"
  | "reportGroup"
  | "invite"
  | "share"
  | "mute"
  | "settings"
  | "viewProfile"
  | "messageMember"
  | "promote"
  | "demote"
  | "removeMember"
  | "acceptInvite"
  | "declineInvite"
  | "openRoom"
  | "leaveRoom"
  | "reportRoom"
  | "providerBoundary";

export type PulseCommandActionRule = {
  key: PulseCommandActionKey;
  label: string;
  tone?: "default" | "warning" | "danger" | "safety";
  available: boolean;
  destructive?: boolean;
  confirmationRequired?: boolean;
  accessibilityLabel: string;
  /**
   * Ionicons glyph name, carried by the rule rather than looked up by the
   * renderer. A lookup table beside the menu is one more place that has to
   * learn about a new action, and the failure when it does not is a blank
   * square rather than an error.
   *
   * Optional because the older rule sets here (rooms, members, providers)
   * render as plain labels and have no icons to give.
   */
  icon?: string;
  /**
   * Catalog key for `label`, carried alongside it for the same reason as
   * `icon`: so a new action arrives with its translation already attached,
   * rather than needing a second edit in a table the renderer owns.
   *
   * `label` stays the English source of truth and is passed as the
   * `defaultValue`, so a missing catalog entry degrades to readable English
   * instead of rendering a raw key at someone.
   */
  i18nKey?: string;
};

/**
 * What a long press is holding, beyond the message row itself.
 *
 * The message alone cannot answer "is there a link in this", because the
 * answer has to come from the *same* parser that makes links tappable and
 * builds post cards. Asking a second parser here would let the menu offer
 * "Open Link" for something the tap handler refuses to open, which is the
 * exact drift this context exists to prevent. So links arrive already
 * extracted, by `detectLinks`, from the caller that already needed them.
 */
export type MessageActionContext = {
  /** Links found in the body by `links/messageLinks.detectLinks`. */
  links?: readonly string[];
  /** More than two participants, so "who has read this" is a real question. */
  group?: boolean;
  /**
   * Deliberately absent: a `viewerModerates` flag.
   *
   * It existed here briefly and widened Delete-for-everyone, on the assumption
   * that a conversation owner could unsend anyone. The server does not agree --
   * `comm_v2.delete_message` refuses any non-sender. A context field that only
   * ever produces a 403 is worse than no field, because it reads like the power
   * is wired.
   */
  /** Viewer's locale differs from the message's, so translating says something. */
  translatable?: boolean;
};

export type MessageActionKind = "text" | "media" | "voice" | "unavailable";

const VOICE_TYPES = ["voice", "audio", "voice_message", "audio_message"];
const MEDIA_TYPES = ["image", "gif", "video", "document", "file", "media", "attachment"];

/**
 * What kind of thing the long press is on.
 *
 * Deliberately widened beyond `message_type`: a row can carry attachments with
 * a type of `"text"` (that is how media sent with a caption arrives), and a
 * menu that offered "Copy" and no "Save to Photos" for a photo would be wrong
 * in the most visible possible way.
 */
export function messageActionKind(message: MessengerMessage): MessageActionKind {
  if (message.deleted_at || message.moderated_at || message.moderation_state) return "unavailable";
  const type = String(message.message_type || message.type || "").toLowerCase();
  if (VOICE_TYPES.includes(type)) return "voice";
  if (MEDIA_TYPES.includes(type)) return "media";
  const attachments = (message as { attachments?: unknown[]; media?: unknown[] });
  if ((attachments.attachments?.length || 0) > 0 || (attachments.media?.length || 0) > 0) return "media";
  return "text";
}

export type PulseCommandCommunityActionRule = {
  key: PulseCommandCommunityActionKey;
  label: string;
  available: boolean;
  tone?: "default" | "warning" | "danger" | "safety";
  destructive?: boolean;
  confirmationRequired?: boolean;
  providerBoundary?: boolean;
  accessibilityLabel: string;
};

/**
 * True only when the server has affirmatively reported a human as online.
 *
 * The accepted set is exactly the one token the unified presence service emits
 * for a live user. "active", "live" and "available" were previously accepted
 * too, which meant any loosely-worded field anywhere in the payload could light
 * up a green dot. "assistant" is excluded on purpose: bots are not people, and
 * they render their own always-available label instead of a presence dot.
 */
export function isActivePresence(value?: string) {
  return String(value || "").toLowerCase() === "online";
}

export function isAssistantPresence(value?: string) {
  return String(value || "").toLowerCase() === "assistant";
}

/** Human-readable presence for a conversation row or header. */
export function presenceLabel(value?: string) {
  const token = String(value || "").toLowerCase();
  if (token === "assistant") return "Always available";
  if (token === "online") return "Online";
  if (token === "away") return "Away";
  if (token === "offline") return "Offline";
  return "";
}

export function conversationDisplayTitle(item: Pick<MessengerConversation, "id" | "title" | "name">) {
  return item.title || item.name || `Conversation ${item.id}`;
}

export function conversationPreview(item: MessengerConversation) {
  if (item.typing) return "Typing...";
  if (item.failed || item.delivery_status === "failed") return "Delivery failed. Open to retry.";
  const preview = item.latest_message || item.last_message_preview;
  return compactPreview(isGeneratedVoiceFilename(preview) ? "Voice message" : preview, "Open chat");
}

export function conversationTime(item: MessengerConversation) {
  return formatShortTime(item.last_activity_at || item.updated_at);
}

export function conversationSignalBadges(item: MessengerConversation) {
  const badges = [];
  if (item.pinned) badges.push("pinned");
  if (item.muted) badges.push("muted");
  if (item.verified) badges.push("verified");
  badges.push(item.conversation_type || "direct");
  if (item.presence) badges.push(item.presence);
  return badges;
}

export function conversationAccessibilityLabel(item: MessengerConversation) {
  const title = conversationDisplayTitle(item);
  const parts = [`Open ${title}`];
  if (Number(item.unread_count || 0) > 0) parts.push(`${item.unread_count} unread`);
  if (item.muted) parts.push("muted");
  if (item.pinned) parts.push("pinned");
  if (item.presence) parts.push(`${item.presence} presence`);
  return parts.join(", ");
}

export function messagePreview(message: MessengerMessage) {
  if (message.deleted_at) return "Deleted message";
  if (message.moderated_at || message.moderation_state) return "Unavailable after safety review";
  const type = String(message.message_type || message.type || "").toLowerCase();
  if (type === "voice" || type === "audio" || type === "voice_message" || type === "audio_message") return "Voice message";
  if (message.body) return message.body;
  if (type === "image" || type === "gif") return "Image attachment";
  if (type === "video") return "Video attachment";
  if (type === "document" || type === "file") return "File attachment";
  return "Attachment";
}

function isGeneratedVoiceFilename(value?: string) {
  return /^(?:pulsesoc[-_ ]voice[-_]\d+|[^\s/\\]+)\.(m4a|mp4|aac|mp3|wav|webm|ogg)$/i.test(String(value || "").trim());
}

export function messageDeliveryLabel(status?: string, seenAt?: string) {
  const normalized = String(status || "").toLowerCase();
  if (seenAt || normalized === "seen" || normalized === "read") return "Read";
  if (normalized === "failed") return "Failed";
  if (normalized === "sending") return "Sending";
  if (normalized === "delivered") return "Delivered";
  if (normalized === "deleted") return "Deleted";
  return "Sent";
}

export function messageAccessibilityLabel(message: MessengerMessage) {
  const sender = message.is_mine ? "You" : message.sender_display_name || (message.sender_trust_state === "intelligence" ? "UNDX" : "Sender");
  const delivery = messageDeliveryLabel(message.local_status || message.delivery_status || message.status, message.seen_at);
  const type = String(message.message_type || message.type || "").toLowerCase();
  if (["voice", "audio", "voice_message", "audio_message"].includes(type)) {
    const duration = Math.max(0, Math.round(Number(message.duration_seconds || message.duration || 0)));
    const durationLabel = duration >= 60 ? `${Math.floor(duration / 60)} minutes ${duration % 60} seconds` : `${duration} seconds`;
    return `${sender}: Voice message, ${durationLabel}, ${formatShortTime(message.created_at)}, ${delivery}`;
  }
  return `${sender}: ${messagePreview(message)}, ${delivery}`;
}

export function typingSummary(presence?: MessengerPresence) {
  const names = presence?.typing?.filter((item) => item.is_typing !== false).map((item) => item.display_name || "Someone") || [];
  if (!names.length) return "";
  if (names.length === 1) return `${names[0]} is typing`;
  return `${names.slice(0, 2).join(", ")} are typing`;
}

/**
 * Optimistic reaction update. When the viewer switches their reaction, the
 * previously selected one is decremented so counts stay accurate before the
 * server reconciles (emoji foundation Stage 8).
 */
export function optimisticReaction(previous: Record<string, number>, reactionType: string, previousViewerReaction?: string) {
  const next: Record<string, number> = {
    ...previous,
    [reactionType]: Number(previous?.[reactionType] || 0) + 1
  };
  if (previousViewerReaction && previousViewerReaction !== reactionType) {
    const remaining = Number(previous?.[previousViewerReaction] || 0) - 1;
    if (remaining > 0) next[previousViewerReaction] = remaining;
    else delete next[previousViewerReaction];
  }
  return next;
}

/**
 * Display glyph for a stored reaction value. New reactions are native Unicode
 * emoji and render as themselves; legacy named reactions map to canonical
 * Unicode equivalents (display-only — stored values are never rewritten).
 */
export function reactionIcon(reaction: string) {
  if (isSingleEmoji(reaction)) return reaction;
  const key = reaction.toLowerCase();
  if (key.includes("pulse")) return "❤️";
  if (key.includes("spark")) return "✨";
  if (key.includes("thank")) return "🙏";
  if (key.includes("seen")) return "👀";
  if (key.includes("fire")) return "🔥";
  return "❤️";
}

/**
 * Server-enforced windows, mirrored so the menu does not offer a button that
 * is already guaranteed to fail. Both numbers live in
 * `pulse_communications_v2/service.py` (`edit_message`, `delete_message`); if
 * they move there, they move here.
 */
const EDIT_WINDOW_MINUTES = 15;
const DELETE_FOR_EVERYONE_WINDOW_MINUTES = 30;

/**
 * Whether a message is still young enough for a time-limited action.
 *
 * Fails toward *offering* the action: a missing or unparseable `created_at`
 * returns true. The server is the authority either way, so the two mistakes
 * are not symmetric -- wrongly offering Edit ends in a clear server message
 * the user can read, while wrongly hiding it leaves someone staring at a
 * menu that has silently dropped an action they are entitled to, with no
 * explanation and nothing to tap. Prefer the recoverable failure.
 */
function withinWindow(createdAt: unknown, minutes: number): boolean {
  const raw = String(createdAt || "").trim();
  if (!raw) return true;
  // Naive timestamps are read as UTC, which is what the server writes.
  const normalized = /(?:Z|[+-]\d{2}:?\d{2})$/.test(raw) ? raw : `${raw}Z`;
  const sentAt = Date.parse(normalized);
  if (!Number.isFinite(sentAt)) return true;
  return Date.now() - sentAt <= minutes * 60_000;
}

/**
 * Which actions a long press offers, and in what order.
 *
 * ## The point is what is NOT here
 *
 * The menu is the feature. A press on someone else's photo and a press on your
 * own voice note are different situations, and a single list that showed every
 * action with most of them greyed out would be a worse answer than the old
 * seven-item sheet: it would be longer, it would bury Reply under things that
 * cannot happen, and it would teach nobody what applies to what.
 *
 * So availability is computed per action from the message and its context, and
 * the renderer draws only `available` rules in the order returned here. Adding
 * an action means adding one rule, not another branch in JSX -- which is what
 * keeps the next action from being dropped in at the end of the list because
 * that was the easy place to put it.
 *
 * ## Order is a claim about frequency, not about importance
 *
 * Reply is first everywhere because it is what most long presses are for.
 * Link actions sit directly under Reply when there is a link, because a press
 * on a message that is mostly a URL is usually about the URL. Destructive
 * actions are last, always, and never adjacent to Reply.
 *
 * ## Voice is deliberately thin
 *
 * No Copy (there is no text), no Translate (same), no View. Nothing here
 * touches playback, the waveform, or the audio session -- the menu reads
 * `message_type` and stops. A voice action that paused playback to show a menu
 * would be a change to audio behaviour arriving through a menu, which is
 * exactly how the session gets stolen.
 */

/**
 * Whether the reaction strip is worth drawing above this message.
 *
 * Reacting is deliberately **not** one of the rules below, and this function
 * exists because pretending otherwise failed silently. The rules describe rows
 * in a list; the strip is a different control in a different band. Asking
 * `rules.some((rule) => rule.key === "react")` of a list that has never
 * contained such a rule is a question whose answer is always `false` -- which
 * is to say the strip never appeared, and nothing anywhere said so.
 *
 * The condition mirrors what reacting actually requires rather than restating
 * the menu's: a server id to attach the reaction to, and a message still there
 * to attach it to. A message still in flight has no id and the screen's own
 * handler refuses it, so offering the strip would be six buttons that answer
 * "not yet".
 */
export function canReactToMessage(message: MessengerMessage) {
  const status = String(message.local_status || message.delivery_status || message.status || "").toLowerCase();
  const deleted = Boolean(message.deleted_at || status === "deleted");
  if (deleted || messageActionKind(message) === "unavailable") return false;
  return Number(message.id || 0) > 0;
}

export function messageActionRules(
  message: MessengerMessage,
  context: MessageActionContext = {}
): PulseCommandActionRule[] {
  const status = String(message.local_status || message.delivery_status || message.status || "").toLowerCase();
  const serverAccepted = Number(message.id || 0) > 0;
  const failed = status === "failed";
  const deleted = Boolean(message.deleted_at || status === "deleted");
  const kind = messageActionKind(message);
  const gone = kind === "unavailable" || deleted;
  const mine = Boolean(message.is_mine);
  const links = context.links || [];
  const hasLink = links.length > 0 && !gone;
  const hasText = Boolean(String(message.body || "").trim()) && !gone;
  /**
   * A message still on its way has no server identity, so anything that names
   * it to the server -- forwarding it, reporting it, asking who read it --
   * has nothing to name. Those wait; Copy and Delete-for-me do not, because
   * both are answerable entirely on this device.
   */
  const addressable = serverAccepted && !gone;

  return [
    {
      key: "reply",
      i18nKey: "common:actions.reply",
      icon: "arrow-undo-outline",
      label: "Reply",
      available: !gone,
      accessibilityLabel: "Reply to message"
    },
    {
      key: "openLink",
      i18nKey: "messaging:messageActions.openLink",
      icon: "open-outline",
      label: "Open Link",
      available: hasLink,
      accessibilityLabel: links.length > 1 ? "Choose a link to open" : "Open the link in this message"
    },
    {
      key: "copyLink",
      i18nKey: "messaging:messageActions.copyLink",
      icon: "link-outline",
      label: "Copy Link",
      available: hasLink,
      accessibilityLabel: links.length > 1 ? "Choose a link to copy" : "Copy the link in this message"
    },
    {
      key: "shareLink",
      i18nKey: "messaging:messageActions.shareLink",
      icon: "share-social-outline",
      label: "Share Link",
      available: hasLink,
      accessibilityLabel: links.length > 1 ? "Choose a link to share" : "Share the link in this message"
    },
    {
      key: "viewMedia",
      i18nKey: "common:actions.view",
      icon: "expand-outline",
      label: "View",
      available: kind === "media" && !gone,
      accessibilityLabel: "Open this attachment full screen"
    },
    {
      key: "saveMedia",
      i18nKey: "messaging:messageActions.saveToPhotos",
      icon: "download-outline",
      label: "Save to Photos",
      available: kind === "media" && !gone,
      accessibilityLabel: "Save this attachment to your device"
    },
    {
      key: "copy",
      i18nKey: "common:actions.copy",
      icon: "copy-outline",
      label: "Copy",
      // Copy is about text. A voice note has none, and a photo's caption is
      // covered by this same flag when it has one.
      available: hasText,
      accessibilityLabel: "Copy message text"
    },
    {
      key: "translate",
      i18nKey: "messaging:messageActions.translate",
      icon: "language-outline",
      label: "Translate",
      // Never offered on your own message: you wrote it.
      available: hasText && !mine && context.translatable !== false,
      accessibilityLabel: "Translate this message"
    },
    {
      key: "forward",
      i18nKey: "messaging:messageActions.forward",
      icon: "arrow-redo-outline",
      label: "Forward",
      available: addressable,
      accessibilityLabel: "Forward this message to another conversation"
    },
    {
      key: "share",
      i18nKey: "common:actions.share",
      icon: "share-outline",
      label: "Share",
      available: !gone && (hasText || kind === "media" || kind === "voice"),
      accessibilityLabel: "Share this message outside PulseSoc"
    },
    {
      key: "save",
      i18nKey: "common:actions.save",
      icon: "bookmark-outline",
      label: "Save",
      available: addressable,
      accessibilityLabel: "Save this message to your saved items"
    },
    {
      key: "edit",
      i18nKey: "common:actions.edit",
      icon: "create-outline",
      label: "Edit",
      // Own text only, and only once the server has a copy to amend. Editing
      // a message that has not landed would race the send.
      available: mine && kind === "text" && hasText && addressable && withinWindow(message.created_at, EDIT_WINDOW_MINUTES),
      accessibilityLabel: "Edit your message"
    },
    {
      key: "info",
      i18nKey: "messaging:messageActions.info",
      icon: "information-circle-outline",
      label: "Message Info",
      available: addressable,
      accessibilityLabel: context.group ? "See who has read this message" : "See delivery details for this message"
    },
    {
      key: "retry",
      i18nKey: "messaging:chat.retry",
      icon: "refresh-outline",
      label: "Retry",
      tone: "warning",
      available: failed,
      accessibilityLabel: "Retry failed message"
    },
    {
      key: "report",
      i18nKey: "common:actions.report",
      icon: "flag-outline",
      label: "Report",
      tone: "warning",
      available: addressable && !mine,
      confirmationRequired: true,
      accessibilityLabel: "Report message to Trust and Safety"
    },
    {
      key: "safety",
      i18nKey: "messaging:chat.muteBlock",
      icon: "shield-half-outline",
      label: "Mute / Block",
      tone: "safety",
      available: !mine,
      accessibilityLabel: "Open safety controls"
    },
    {
      key: "deleteSelf",
      i18nKey: "messaging:chat.deleteForMe",
      icon: "trash-outline",
      label: "Delete for me",
      tone: "danger",
      available: true,
      destructive: true,
      confirmationRequired: serverAccepted,
      accessibilityLabel: "Delete message for me"
    },
    {
      key: "deleteEveryone",
      i18nKey: "messaging:chat.deleteForEveryone",
      icon: "trash-bin-outline",
      label: "Delete for everyone",
      tone: "danger",
      /**
       * Author only, inside the window -- because that is exactly what
       * `comm_v2.delete_message` grants. It refuses a non-sender outright
       * ("You can only delete your own message for everyone") and refuses
       * anyone past 30 minutes, moderator or not.
       *
       * An earlier draft of this rule offered it to conversation moderators
       * too. That was a guess about the server dressed up as a mirror of it,
       * and it would have put a button in a moderator's menu that returns 403
       * every time. `viewerModerates` therefore does NOT widen this; if the
       * server ever grants moderators the power, this is the line to change,
       * and the test named for it is the one that should fail first.
       */
      available: mine && !gone && withinWindow(message.created_at, DELETE_FOR_EVERYONE_WINDOW_MINUTES),
      destructive: true,
      confirmationRequired: serverAccepted,
      accessibilityLabel: "Delete message for everyone"
    }
  ];
}

export function groupDisplayTitle(group: Pick<PulseGroup, "name" | "id">) {
  return group.name || `PulseSoc Group ${group.id}`;
}

export function groupTypeLabel(group: Pick<PulseGroup, "category" | "group_type">) {
  return `${group.category || "Community"} · ${group.group_type || "public"}`;
}

export function groupRoleLabel(group: Pick<PulseGroup, "viewer_role" | "joined" | "can_manage">) {
  if (group.can_manage) return "manager";
  if (group.viewer_role) return group.viewer_role;
  return group.joined ? "member" : "not joined";
}

export function groupMemberRoleLabel(role?: string) {
  const normalized = String(role || "member").toLowerCase();
  if (normalized === "owner") return "Owner";
  if (normalized === "admin") return "Admin";
  if (normalized === "moderator" || normalized === "mod") return "Moderator";
  if (normalized === "pending") return "Pending";
  if (normalized === "invited") return "Invited";
  return "Member";
}

export function groupRolePriority(role?: string) {
  const normalized = String(role || "").toLowerCase();
  if (normalized === "owner") return 5;
  if (normalized === "admin") return 4;
  if (normalized === "moderator" || normalized === "mod") return 3;
  if (normalized === "member") return 2;
  if (normalized === "pending" || normalized === "invited") return 1;
  return 0;
}

export function groupSummary(group: PulseGroup) {
  return group.description || "PulseSoc community";
}

export function groupSignalBadges(group: PulseGroup) {
  const badges = [
    `${Number(group.member_count || 0)} members`,
    `${Number(group.post_count || 0)} posts`,
    group.trust_level || "standard",
    groupRoleLabel(group)
  ];
  if (group.featured) badges.push("featured");
  if (group.status && group.status !== "active") badges.push(group.status);
  return badges.filter(Boolean);
}

export function groupNotificationLabel(group: Pick<PulseGroup, "notification_state" | "joined">) {
  if (!group.joined) return "not subscribed";
  const state = String(group.notification_state || "").toLowerCase();
  if (state === "muted") return "muted";
  if (state === "mentions") return "mentions only";
  if (state === "all") return "all updates";
  return "standard notifications";
}

export function groupAccessibilityLabel(group: PulseGroup) {
  return [
    `Open group ${groupDisplayTitle(group)}`,
    groupTypeLabel(group),
    groupRoleLabel(group),
    `${Number(group.member_count || 0)} members`
  ].join(", ");
}

export function groupActionRules(group: PulseGroup): PulseCommandCommunityActionRule[] {
  const joined = Boolean(group.joined || group.viewer_role);
  const canManage = Boolean(group.can_manage || groupRolePriority(group.viewer_role) >= 3);
  return [
    {
      key: joined ? "leave" : "join",
      label: joined ? "Leave" : "Join",
      available: true,
      tone: joined ? "warning" : "default",
      destructive: joined,
      accessibilityLabel: joined ? `Leave ${groupDisplayTitle(group)}` : `Join ${groupDisplayTitle(group)}`
    },
    {
      key: "openChat",
      label: "Chat",
      available: joined || group.group_type === "public",
      accessibilityLabel: `Open chat for ${groupDisplayTitle(group)}`
    },
    {
      key: "invite",
      label: "Invite",
      available: joined || canManage,
      accessibilityLabel: `Invite people to ${groupDisplayTitle(group)}`
    },
    {
      key: "share",
      label: "Share",
      available: true,
      accessibilityLabel: `Share ${groupDisplayTitle(group)}`
    },
    {
      key: "mute",
      label: group.notification_state === "muted" ? "Unmute" : "Mute",
      available: joined,
      tone: "safety",
      accessibilityLabel: `${group.notification_state === "muted" ? "Unmute" : "Mute"} ${groupDisplayTitle(group)}`
    },
    {
      key: "settings",
      label: "Settings",
      available: canManage,
      accessibilityLabel: `Open settings for ${groupDisplayTitle(group)}`
    },
    {
      key: "reportGroup",
      label: "Report",
      available: true,
      tone: "warning",
      accessibilityLabel: `Report ${groupDisplayTitle(group)}`
    }
  ];
}

export function groupMemberAccessibilityLabel(member: PulseGroupMember) {
  return [
    `Open member ${member.display_name}`,
    groupMemberRoleLabel(member.role),
    member.presence ? `${member.presence} presence` : "",
    member.verified ? "verified" : ""
  ].filter(Boolean).join(", ");
}

export function groupMemberActionRules(group: PulseGroup, member: PulseGroupMember): PulseCommandCommunityActionRule[] {
  const viewerPriority = group.can_manage ? 4 : groupRolePriority(group.viewer_role);
  const memberPriority = groupRolePriority(member.role);
  const canModerateMember = viewerPriority >= 3 && viewerPriority > memberPriority;
  return [
    {
      key: "viewProfile",
      label: "Profile",
      available: true,
      accessibilityLabel: `Open profile for ${member.display_name}`
    },
    {
      key: "messageMember",
      label: "Message",
      available: Boolean(member.user_id),
      accessibilityLabel: `Message ${member.display_name}`
    },
    {
      key: "promote",
      label: "Promote",
      available: viewerPriority >= 4 && memberPriority > 0 && memberPriority < 3,
      accessibilityLabel: `Promote ${member.display_name}`
    },
    {
      key: "demote",
      label: "Demote",
      available: viewerPriority >= 4 && memberPriority >= 3 && memberPriority < viewerPriority,
      tone: "warning",
      accessibilityLabel: `Demote ${member.display_name}`
    },
    {
      key: "removeMember",
      label: "Remove",
      available: canModerateMember,
      destructive: true,
      confirmationRequired: true,
      tone: "danger",
      accessibilityLabel: `Remove ${member.display_name} from ${groupDisplayTitle(group)}`
    },
    {
      key: "reportGroup",
      label: "Report",
      available: true,
      tone: "warning",
      accessibilityLabel: `Report ${member.display_name}`
    }
  ];
}

export function groupInvitationStateLabel(invitation: PulseGroupInvitation) {
  const state = String(invitation.status || "pending").toLowerCase();
  if (state === "accepted") return "Accepted";
  if (state === "declined" || state === "rejected") return "Declined";
  if (state === "cancelled" || state === "canceled") return "Cancelled";
  if (state === "expired") return "Expired";
  return "Pending";
}

export function groupInvitationAccessibilityLabel(invitation: PulseGroupInvitation) {
  return `${invitation.display_name}, ${groupInvitationStateLabel(invitation)}, ${groupMemberRoleLabel(invitation.role)}`;
}

export function groupAssetCategoryLabel(asset: PulseGroupAsset) {
  if (asset.kind === "photo") return "Photo";
  if (asset.kind === "video") return "Video";
  if (asset.kind === "audio") return "Audio";
  if (asset.kind === "file") return "File";
  if (asset.kind === "link") return "Link";
  return "Asset";
}

export function roomDisplayTitle(room: Pick<PulseRoom, "title" | "name" | "id">) {
  return room.title || room.name || `PulseSoc Room ${room.id}`;
}

export function roomSummary(room: PulseRoom) {
  return room.last_message || room.description || room.pinned_notice || "Persistent PulseSoc room";
}

export function roomSignalBadges(room: PulseRoom) {
  const badges = [
    `${Number(room.online_count || 0)} active`,
    `${Number(room.unread_count || 0)} unread`
  ];
  if (room.partial) badges.push("provider boundary");
  if (room.energy) badges.push(`energy ${room.energy}`);
  return badges;
}

export function roomProviderStateLabel(room: Pick<PulseRoom, "partial" | "provider" | "provider_state">) {
  const state = String(room.provider_state || "").toLowerCase();
  if (room.partial || state === "provider_boundary") return "provider boundary";
  if (state === "inactive" || state === "closed") return "inactive";
  if (state === "scheduled") return "scheduled";
  if (state === "live" || state === "connected") return "live";
  if (state === "permission_required") return "permission required";
  return room.provider ? `${room.provider} ready` : "native room";
}

export function roomAccessibilityLabel(room: PulseRoom) {
  return [
    `Open room ${roomDisplayTitle(room)}`,
    `${Number(room.online_count || 0)} active`,
    `${Number(room.unread_count || 0)} unread`,
    room.partial ? "provider boundary" : "native room"
  ].join(", ");
}

export function roomActionRules(room: PulseRoom): PulseCommandCommunityActionRule[] {
  const providerBoundary = Boolean(room.partial || room.provider_state === "provider_boundary");
  return [
    {
      key: "openRoom",
      label: Number(room.conversation_id || 0) ? "Open Room" : "Join Room",
      available: !providerBoundary,
      providerBoundary,
      accessibilityLabel: `${Number(room.conversation_id || 0) ? "Open" : "Join"} ${roomDisplayTitle(room)}`
    },
    {
      key: "share",
      label: "Share",
      available: true,
      accessibilityLabel: `Share ${roomDisplayTitle(room)}`
    },
    {
      key: "reportRoom",
      label: "Report",
      available: true,
      tone: "warning",
      accessibilityLabel: `Report ${roomDisplayTitle(room)}`
    },
    {
      key: "providerBoundary",
      label: roomProviderStateLabel(room),
      available: providerBoundary,
      providerBoundary: true,
      accessibilityLabel: `${roomDisplayTitle(room)} is not available in the app yet`
    }
  ];
}

export function roomParticipantRoleLabel(participant: Pick<PulseRoomParticipant, "role" | "provider_state">) {
  const role = String(participant.role || "participant").toLowerCase();
  if (role === "host") return "Host";
  if (role === "speaker") return "Speaker";
  if (role === "moderator" || role === "mod") return "Moderator";
  if (role === "listener") return "Listener";
  if (role === "invited") return "Invited";
  if (String(participant.provider_state || "").toLowerCase() === "disconnected") return "Disconnected";
  return "Participant";
}

export function roomParticipantAccessibilityLabel(participant: PulseRoomParticipant) {
  return [
    participant.display_name,
    roomParticipantRoleLabel(participant),
    participant.presence ? `${participant.presence} presence` : "",
    participant.provider_state ? `provider ${participant.provider_state}` : ""
  ].filter(Boolean).join(", ");
}
