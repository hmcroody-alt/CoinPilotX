import { useMemo } from "react";
import { fetchMutedUsers, setMuted, type RelationshipUser } from "../../settings/api";
import { RelationshipListScreen, type RelationshipListConfig } from "./RelationshipListScreen";

/**
 * Muted accounts.
 *
 * Same list mechanics as Blocked (see `RelationshipListScreen`) — only the copy
 * and the endpoints differ. The subtitle draws the distinction from blocking
 * explicitly, because muting is silent and one-way and users regularly assume
 * it also stops messages.
 */
export function MutedUsersScreen() {
  const config = useMemo<RelationshipListConfig>(
    () => ({
      title: "Muted accounts",
      subtitle:
        "Muting is one-way and private: their posts, reels, and stories stop appearing in your feed, but you still follow each other, they can still message and call you, and they're never told. To cut off contact entirely, block them instead.",
      fetch: fetchMutedUsers,
      mutate: (userId: number, active: boolean) => setMuted(userId, active),
      actionLabel: "Unmute",
      sinceVerb: "Muted",
      sectionTitle: "Muted accounts",
      sectionFootnote:
        "Unmuting brings this account's posts, reels, and stories back into your feed. Nothing you missed while they were muted is re-delivered.",
      emptyIcon: "volume-mute-outline",
      emptyTitle: "You haven't muted anyone",
      emptyBody:
        "Accounts you mute appear here. Mute someone from the ••• menu on any of their posts to quieten them without unfollowing.",
      searchPlaceholder: "Search muted accounts",
      confirmTitle: (user: RelationshipUser) => `Unmute ${user.displayName}?`,
      confirmMessage: (user: RelationshipUser) =>
        `${user.username ? `@${user.username}` : "This account"} will start appearing in your feed and stories again.`,
      testIDPrefix: "muted-users"
    }),
    []
  );

  return <RelationshipListScreen {...config} />;
}
