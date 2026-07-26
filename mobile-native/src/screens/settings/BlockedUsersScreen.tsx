import { useMemo } from "react";
import { fetchBlockedUsers, setBlocked, type RelationshipUser } from "../../settings/api";
import { RelationshipListScreen, type RelationshipListConfig } from "./RelationshipListScreen";

/**
 * Blocked accounts.
 *
 * All list behaviour (loading, search, optimistic removal, rollback) lives in
 * `RelationshipListScreen`; this file is only the block-specific vocabulary and
 * endpoints. The subtitle exists because block and mute are the single most
 * confused pair of controls in the app — users block when they meant to mute,
 * then report the "bug" that the person can no longer message them.
 */
export function BlockedUsersScreen() {
  const config = useMemo<RelationshipListConfig>(
    () => ({
      title: "Blocked accounts",
      subtitle:
        "Blocking cuts contact both ways: they can't see your profile, posts, or stories, can't message or call you, and you won't see them either. They're never told they were blocked. If you only want their posts out of your feed, mute them instead.",
      fetch: fetchBlockedUsers,
      mutate: (userId: number, active: boolean) => setBlocked(userId, active),
      actionLabel: "Unblock",
      sinceVerb: "Blocked",
      sectionTitle: "Blocked accounts",
      sectionFootnote:
        "Unblocking lets this account find, follow, and message you again. It does not automatically restore a follow in either direction.",
      emptyIcon: "shield-checkmark-outline",
      emptyTitle: "You haven't blocked anyone",
      emptyBody:
        "Accounts you block appear here. You can block someone from their profile or from any post, comment, or message they sent.",
      searchPlaceholder: "Search blocked accounts",
      confirmTitle: (user: RelationshipUser) => `Unblock ${user.displayName}?`,
      confirmMessage: (user: RelationshipUser) =>
        `${user.username ? `@${user.username}` : "This account"} will be able to see your profile, follow you, and send you messages again.`,
      testIDPrefix: "blocked-users"
    }),
    []
  );

  return <RelationshipListScreen {...config} />;
}
