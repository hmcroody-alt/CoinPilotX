/**
 * "Discussed in N conversations" — the reverse of a Private Office link.
 *
 * One component rather than a block copied into each host screen. Every copy
 * would be another chance for one of them to render "not discussed anywhere"
 * over a failed read, and the whole point of this affordance is that the empty
 * state is trustworthy.
 *
 * Hosted today by documents (the per-document detail region), facts (the "why"
 * provenance sheet) and meetings (a per-row disclosure added for this). Each
 * host owns its own disclosure design; this component owns only the read and
 * the three states, which is why adding a host is a wiring change rather than
 * three more chances to get the empty state wrong. There is still no records
 * screen to host it. `linkType` is the full union rather than the three that
 * ship, because the server answers for every kind already — the gap is in the
 * hosts, not in this component or the route beneath it.
 *
 * Worth knowing before trusting a screenshot of this panel: nothing in the app
 * currently WRITES a link. `link()` in `services/private_office/conversations.py`
 * is the only writer of `private_office_conversation_links`, it is reachable
 * only via `POST /<ref>/links`, and `linkPrivateConversation` has no non-test
 * caller. So every host renders the "none" line today. That line is honest —
 * the server really does return zero rows — but it is not yet evidence that the
 * panel can render rows in production.
 *
 * It owns no data of its own: it asks `listConversationsForTarget`, which is
 * the server's intersection of the link rows with the member's visible threads,
 * and renders exactly what came back. It does not filter, sort or cache — a
 * second view of who-may-see-what is precisely the thing the Office surface is
 * built to avoid.
 *
 * Three renderable states, deliberately kept apart:
 *   in flight  — nothing at all, because a claim not yet made is not a claim
 *   refused    — a short, honest line; never an empty list
 *   READY      — the rows, or the "no linked conversations" line
 *
 * A locked office relocks the app-wide lock store rather than drawing its own
 * door: the host screen is already inside `PrivateOfficeLockGate`, and the gate
 * is the one thing allowed to decide what a locked office looks like.
 */
import React, { useCallback, useEffect, useState } from "react";
import { ActivityIndicator, Pressable, StyleSheet, Text, View } from "react-native";

import { useTranslation } from "../i18n";
import type { PrivateConversationLinkType } from "../api/privateConversations";
import {
  listConversationsForTarget,
  type PrivateConversationListResult,
  type PrivateConversationSummary
} from "../api/privateConversations";
import { lockOfficeLocally } from "./officeLock";
import { colors } from "../theme/colors";

export function LinkedConversations({
  linkType,
  targetId,
  onOpenConversation
}: {
  linkType: PrivateConversationLinkType;
  targetId: string | number;
  /** Opens the canonical thread. There is no Office-side thread screen. */
  onOpenConversation: (conversationId: number) => void;
}) {
  const { t } = useTranslation();
  const [result, setResult] = useState<PrivateConversationListResult | null>(null);

  const load = useCallback(async () => {
    // Clear first, so a target change or a retry cannot leave the previous
    // answer on screen while the new read is in flight.
    setResult(null);
    const next = await listConversationsForTarget(linkType, targetId);
    if (next.state === "LOCKED") lockOfficeLocally();
    return next;
  }, [linkType, targetId]);

  useEffect(() => {
    let alive = true;
    load().then((next) => {
      if (alive) setResult(next);
    });
    return () => {
      alive = false;
    };
  }, [load]);

  const retry = useCallback(() => {
    load().then(setResult);
  }, [load]);

  // Nothing is claimed while the answer is unknown.
  if (result === null) {
    return (
      <View style={styles.root}>
        <ActivityIndicator color={colors.accent} />
      </View>
    );
  }

  if (result.state !== "READY") {
    // Deliberately not the empty line. A refusal says the read failed; it says
    // nothing whatsoever about whether this object is discussed anywhere.
    return (
      <View style={styles.root}>
        <Text style={styles.heading}>
          {t("premium:privateOffice.conversations.linked.title")}
        </Text>
        <Text style={styles.note}>
          {t("premium:privateOffice.conversations.linked.unavailable")}
        </Text>
        {result.state === "UNAVAILABLE" || result.state === "ERROR" ? (
          <Pressable
            onPress={retry}
            accessibilityRole="button"
            accessibilityLabel={t("premium:privateOffice.retry")}
            style={styles.retry}
          >
            <Text style={styles.retryText}>{t("premium:privateOffice.retry")}</Text>
          </Pressable>
        ) : null}
      </View>
    );
  }

  const conversations: PrivateConversationSummary[] = result.conversations;

  return (
    <View style={styles.root}>
      <Text style={styles.heading}>
        {t("premium:privateOffice.conversations.linked.title")}
      </Text>
      {conversations.length === 0 ? (
        <Text style={styles.note}>
          {t("premium:privateOffice.conversations.linked.none")}
        </Text>
      ) : null}
      {conversations.map((summary) => {
        const conversationId =
          summary.conversation.conversation_id || summary.conversation.id;
        return (
          <Pressable
            key={String(conversationId)}
            style={styles.row}
            onPress={() => onOpenConversation(Number(conversationId))}
            accessibilityRole="button"
            accessibilityLabel={String(
              summary.conversation.title || summary.conversation.name || conversationId
            )}
          >
            <Text style={styles.rowTitle} numberOfLines={1}>
              {summary.conversation.title ||
                summary.conversation.name ||
                t("premium:privateOffice.conversations.untitled")}
            </Text>
            {summary.classification.officeScope ? (
              <Text style={styles.rowMeta}>
                {t(
                  `premium:privateOffice.conversations.scopes.${summary.classification.officeScope}`,
                  { defaultValue: summary.classification.officeScope }
                )}
              </Text>
            ) : null}
          </Pressable>
        );
      })}
    </View>
  );
}

const styles = StyleSheet.create({
  root: { gap: 8, paddingTop: 6 },
  heading: { color: colors.text, fontSize: 13, fontWeight: "700", letterSpacing: 1 },
  note: { color: colors.muted, fontSize: 12, lineHeight: 18 },
  row: {
    borderRadius: 12,
    borderWidth: StyleSheet.hairlineWidth,
    borderColor: colors.border,
    backgroundColor: colors.surfaceRaised,
    paddingHorizontal: 12,
    paddingVertical: 10,
    gap: 2
  },
  rowTitle: { color: colors.text, fontSize: 14, fontWeight: "600" },
  rowMeta: { color: colors.muted, fontSize: 11, letterSpacing: 1 },
  retry: { alignSelf: "flex-start", paddingVertical: 6 },
  retryText: { color: colors.accent, fontSize: 12, fontWeight: "700" }
});
