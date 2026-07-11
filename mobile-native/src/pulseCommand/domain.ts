import { MessengerConversation, MessengerMessage, MessengerPresence } from "../api/messenger";
import { compactPreview, formatShortTime } from "../utils/format";

export type PulseCommandActionKey =
  | "reply"
  | "react"
  | "retry"
  | "report"
  | "safety"
  | "deleteSelf"
  | "deleteEveryone";

export type PulseCommandActionRule = {
  key: PulseCommandActionKey;
  label: string;
  tone?: "default" | "warning" | "danger" | "safety";
  available: boolean;
  destructive?: boolean;
  confirmationRequired?: boolean;
  accessibilityLabel: string;
};

export function isActivePresence(value?: string) {
  return ["online", "active", "live"].includes(String(value || "").toLowerCase());
}

export function conversationDisplayTitle(item: Pick<MessengerConversation, "id" | "title" | "name">) {
  return item.title || item.name || `Conversation ${item.id}`;
}

export function conversationPreview(item: MessengerConversation) {
  if (item.typing) return "Typing...";
  if (item.failed || item.delivery_status === "failed") return "Delivery failed. Open to retry.";
  return compactPreview(item.latest_message || item.last_message_preview, "Open chat");
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
  if (message.body) return message.body;
  const type = String(message.message_type || message.type || "").toLowerCase();
  if (type === "image" || type === "gif") return "Image attachment";
  if (type === "video") return "Video attachment";
  if (type === "voice" || type === "audio") return "Voice message";
  if (type === "document" || type === "file") return "File attachment";
  return "Attachment";
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
  return `${sender}: ${messagePreview(message)}, ${delivery}`;
}

export function typingSummary(presence?: MessengerPresence) {
  const names = presence?.typing?.filter((item) => item.is_typing !== false).map((item) => item.display_name || "Someone") || [];
  if (!names.length) return "";
  if (names.length === 1) return `${names[0]} is typing`;
  return `${names.slice(0, 2).join(", ")} are typing`;
}

export function optimisticReaction(previous: Record<string, number>, reactionType: string) {
  return {
    ...previous,
    [reactionType]: Number(previous?.[reactionType] || 0) + 1
  };
}

export function reactionIcon(reaction: string) {
  const key = reaction.toLowerCase();
  if (key.includes("spark")) return "✦";
  if (key.includes("thank")) return "✓";
  if (key.includes("seen")) return "◌";
  if (key.includes("fire")) return "🔥";
  return "◈";
}

export function messageActionRules(message: MessengerMessage): PulseCommandActionRule[] {
  const status = String(message.local_status || message.delivery_status || message.status || "").toLowerCase();
  const serverAccepted = Number(message.id || 0) > 0;
  const failed = status === "failed";
  const deleted = Boolean(message.deleted_at || status === "deleted");
  return [
    {
      key: "reply",
      label: "Reply",
      available: !deleted,
      accessibilityLabel: "Reply to message"
    },
    {
      key: "react",
      label: "React",
      available: serverAccepted && !deleted,
      accessibilityLabel: "React to message"
    },
    {
      key: "retry",
      label: "Retry",
      tone: "warning",
      available: failed,
      accessibilityLabel: "Retry failed message"
    },
    {
      key: "report",
      label: "Report",
      tone: "warning",
      available: serverAccepted && !message.is_mine,
      confirmationRequired: true,
      accessibilityLabel: "Report message to Trust and Safety"
    },
    {
      key: "safety",
      label: "Mute / Block",
      tone: "safety",
      available: !message.is_mine,
      accessibilityLabel: "Open safety controls"
    },
    {
      key: "deleteSelf",
      label: "Delete for me",
      tone: "danger",
      available: true,
      destructive: true,
      confirmationRequired: serverAccepted,
      accessibilityLabel: "Delete message for me"
    },
    {
      key: "deleteEveryone",
      label: "Delete for everyone",
      tone: "danger",
      available: Boolean(message.is_mine),
      destructive: true,
      confirmationRequired: serverAccepted,
      accessibilityLabel: "Delete message for everyone"
    }
  ];
}
