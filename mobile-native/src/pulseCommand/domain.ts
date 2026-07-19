import { MessengerConversation, MessengerMessage, MessengerPresence } from "../api/messenger";
import { PulseGroup, PulseGroupAsset, PulseGroupInvitation, PulseGroupMember, PulseRoom, PulseRoomParticipant } from "../api/groups";
import { compactPreview, formatShortTime } from "../utils/format";

export type PulseCommandActionKey =
  | "reply"
  | "react"
  | "retry"
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
};

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

export function isActivePresence(value?: string) {
  return ["online", "active", "live"].includes(String(value || "").toLowerCase());
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
  return /^pulsesoc[-_ ]voice[-_]\d+\.(m4a|mp4|aac|mp3|wav|webm|ogg)$/i.test(String(value || "").trim());
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
      accessibilityLabel: `${roomDisplayTitle(room)} has a provider boundary`
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
