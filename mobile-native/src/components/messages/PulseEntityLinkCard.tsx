/**
 * A PulseSoc object, rendered inside a message as the thing it is.
 *
 * ## One card, several kinds
 *
 * Posts and profiles share this component rather than getting one each. They
 * are the same card — a picture, a name, a line, a way in — and the parts that
 * are easy to get wrong are the parts they share: the shell that must not
 * reflow when it resolves, the unavailable state that must stop being
 * tappable, the accessibility label, the video badge that may only be drawn
 * over a thumbnail that actually exists. Two components would mean fixing each
 * of those twice, and the second fix is the one that gets forgotten.
 *
 * Only the *words* differ by kind, and they are chosen in `cardCopy` below,
 * where every translation key is a literal. A composed key such as
 * `messaging:${kind}Card.cta` would read more cleverly and would be invisible
 * to the i18n extractor, so it would ship English to eleven locales.
 *
 * ## It is derived, never stored
 *
 * The card is computed from the message body at render time. Nothing about it
 * is written into the message: no new `message_type`, no preview columns, no
 * second send path. That is not a shortcut — it is the requirement. Every post
 * link already sitting in every conversation becomes a card the moment this
 * ships, without anyone resending anything and without a migration that would
 * have to guess at bodies it had never parsed. A message is text; a card is a
 * reading of that text; and a reading can be improved later, where a stored
 * snapshot would be frozen at the version that wrote it.
 *
 * It also means the card cannot be forged into saying something the link does
 * not. `resolvePulseEntity` takes the URL out of the body and the preview comes
 * back from the server for that id, so the author name on the card is the
 * author of the post the tap opens. There is no sender-supplied field anywhere
 * in the card, which is what makes "the card is the destination" true rather
 * than merely intended.
 *
 * ## The shell is the same size as the card
 *
 * Preview resolution is a network round trip and chat must not wait for it. The
 * loading state therefore occupies the *finished* layout rather than collapsing:
 * a card that grows when it resolves shoves the conversation under the reader's
 * thumb mid-scroll, which is worse than a moment of grey.
 *
 * ## Unavailable is a state, not an absence
 *
 * A post the viewer cannot see still renders a card — a quiet one that says so.
 * Dropping back to a raw URL would be worse in both directions: the sender's
 * message would look broken, and the URL itself would be the one thing still
 * on screen, which is the part that carries no information the viewer can use.
 */

import React from "react";
import { Image, Pressable, StyleSheet, Text, View } from "react-native";
import { useTranslation } from "../../i18n";
import { colors } from "../../theme/colors";
import { chatGraphite } from "../../theme/chatGraphite";
import { PulseEntityRef } from "../../links/pulseEntity";
import { useEntityPreview } from "../../links/entityPreview";

type CardCopy = {
  eyebrow: string;
  cta: string;
  loading: string;
  forbidden: string;
  missing: string;
  unavailable: string;
  /**
   * The word over the corner of the thumbnail. A post says "Video" because the
   * badge is *news* — most posts are not video, so the badge is what tells you
   * this one is. A reel says "Play" because every reel is video and repeating
   * that over a card whose eyebrow already reads REEL would say nothing; what
   * the reader does not yet know is that the still is a clip they can start.
   */
  mediaBadge: string;
  a11yGeneric: string;
  a11yFor: (author: string) => string;
};

/**
 * The words for one kind. Every key spelled out, for the extractor's sake.
 *
 * The card's kind comes from the *resolved* entity, not from the preview, so
 * the loading and unavailable states are already kind-specific: a profile link
 * that is still resolving says "Loading profile…", and one that has been
 * deleted says so about a profile. Falling back to the post wording while the
 * kind was unknown would have been a small lie told at the most visible moment.
 */
function cardCopy(kind: PulseEntityRef["kind"], t: (key: string, vars?: Record<string, unknown>) => string): CardCopy {
  if (kind === "profile") {
    return {
      eyebrow: t("messaging:profileCard.eyebrow"),
      cta: t("messaging:profileCard.cta"),
      loading: t("messaging:profileCard.loading"),
      forbidden: t("messaging:profileCard.forbidden"),
      missing: t("messaging:profileCard.missing"),
      unavailable: t("messaging:profileCard.unavailable"),
      // A profile has no clip to start, and `previewFromProfile` sets
      // `video: false`, so this is never read. It is spelled anyway rather
      // than left to a `?? ""`: the next kind added should have to answer the
      // question, not inherit a blank.
      mediaBadge: t("messaging:postCard.video"),
      a11yGeneric: t("messaging:profileCard.a11yOpenGeneric"),
      a11yFor: (author: string) => t("messaging:profileCard.a11yOpen", { author })
    };
  }
  if (kind === "reel") {
    return {
      eyebrow: t("messaging:reelCard.eyebrow"),
      cta: t("messaging:reelCard.cta"),
      loading: t("messaging:reelCard.loading"),
      forbidden: t("messaging:reelCard.forbidden"),
      missing: t("messaging:reelCard.missing"),
      unavailable: t("messaging:reelCard.unavailable"),
      mediaBadge: t("messaging:reelCard.play"),
      a11yGeneric: t("messaging:reelCard.a11yOpenGeneric"),
      a11yFor: (author: string) => t("messaging:reelCard.a11yOpen", { author })
    };
  }
  return {
    eyebrow: t("messaging:postCard.eyebrow"),
    cta: t("messaging:postCard.cta"),
    loading: t("messaging:postCard.loading"),
    forbidden: t("messaging:postCard.forbidden"),
    missing: t("messaging:postCard.missing"),
    unavailable: t("messaging:postCard.unavailable"),
    mediaBadge: t("messaging:postCard.video"),
    a11yGeneric: t("messaging:postCard.a11yOpenGeneric"),
    a11yFor: (author: string) => t("messaging:postCard.a11yOpen", { author })
  };
}

export function PulseEntityLinkCard({
  entity,
  onOpen,
  onLongPress
}: {
  entity: PulseEntityRef;
  onOpen: (url: string) => void;
  onLongPress?: () => void;
}) {
  const { t } = useTranslation();
  const state = useEntityPreview(entity);
  const preview = state.status === "ready" ? state.preview : null;
  const copy = cardCopy(entity.kind, t);
  /**
   * The handle is the identity; the display name is the courtesy. When the
   * server sends only one of them the card shows that one rather than an empty
   * row, and when it sends neither the author row is dropped entirely — an
   * "@" on its own would read as a rendering fault.
   */
  const handle = preview?.authorHandle ? `@${preview.authorHandle}` : "";
  const authorLine = preview ? preview.authorName || handle : "";
  const unavailableLine =
    state.status === "unavailable"
      ? state.reason === "forbidden"
        ? copy.forbidden
        : state.reason === "missing"
          ? copy.missing
          : copy.unavailable
      : "";

  return (
    <Pressable
      accessibilityRole="link"
      accessibilityLabel={
        preview ? copy.a11yFor(authorLine || t("common:identity.member")) : copy.a11yGeneric
      }
      // A card for a post that cannot be loaded is not tappable. Sending someone
      // to a screen that will show them the same refusal, one navigation later,
      // is a worse answer than the card already gave them.
      disabled={state.status === "unavailable"}
      onPress={() => onOpen(entity.url)}
      onLongPress={onLongPress}
      style={({ pressed }) => [styles.card, pressed && styles.pressed, state.status === "unavailable" && styles.cardQuiet]}
    >
      {preview?.thumbnailUrl ? (
        <View style={styles.mediaFrame}>
          <Image source={{ uri: preview.thumbnailUrl }} style={styles.media} resizeMode="cover" />
          {/* Only ever over a picture that exists. A badge floating on the
              empty frame would be the black-rectangle bug again, wearing a
              label that says the rectangle is fine. */}
          {preview.video ? (
            <View style={styles.videoBadge}>
              <Text style={styles.videoBadgeText}>{copy.mediaBadge}</Text>
            </View>
          ) : null}
        </View>
      ) : state.status === "loading" ? (
        <View style={[styles.mediaFrame, styles.skeleton]} />
      ) : null}

      <View style={styles.body}>
        <View style={styles.authorRow}>
          {preview?.authorAvatarUrl ? (
            <Image source={{ uri: preview.authorAvatarUrl }} style={styles.avatar} />
          ) : (
            <View style={[styles.avatar, styles.avatarFallback]} />
          )}
          <View style={styles.authorText}>
            {authorLine ? (
              <Text style={styles.authorName} numberOfLines={1}>
                {authorLine}
              </Text>
            ) : (
              <Text style={styles.eyebrow} numberOfLines={1}>
                {copy.eyebrow}
              </Text>
            )}
            {/* The handle repeats under the name only when both exist, so a post
                whose author has no display name does not show the handle twice. */}
            {authorLine && handle && authorLine !== handle ? (
              <Text style={styles.handle} numberOfLines={1}>
                {handle}
              </Text>
            ) : null}
          </View>
        </View>

        {state.status === "loading" ? (
          <Text style={styles.caption} numberOfLines={2}>
            {copy.loading}
          </Text>
        ) : unavailableLine ? (
          <Text style={styles.unavailable} numberOfLines={2}>
            {unavailableLine}
          </Text>
        ) : preview?.caption ? (
          <Text style={styles.caption} numberOfLines={3}>
            {preview.caption}
          </Text>
        ) : null}

        <View style={styles.footerRow}>
          <Text style={styles.brand}>{copy.eyebrow}</Text>
          {state.status === "unavailable" ? null : <Text style={styles.cta}>{copy.cta}</Text>}
        </View>
      </View>
    </Pressable>
  );
}

const styles = StyleSheet.create({
  card: {
    backgroundColor: chatGraphite.insetSurface,
    borderColor: "rgba(123, 223, 255, 0.28)",
    borderRadius: 14,
    borderWidth: StyleSheet.hairlineWidth,
    marginBottom: 6,
    maxWidth: 260,
    minWidth: 216,
    overflow: "hidden"
  },
  cardQuiet: { borderColor: chatGraphite.quietDivider },
  pressed: { opacity: 0.82 },
  mediaFrame: { aspectRatio: 1.6, backgroundColor: "rgba(0,0,0,0.28)", width: "100%" },
  media: { height: "100%", width: "100%" },
  skeleton: { backgroundColor: "rgba(255,255,255,0.05)" },
  videoBadge: {
    backgroundColor: "rgba(4,18,28,0.78)",
    borderRadius: 6,
    bottom: 8,
    paddingHorizontal: 7,
    paddingVertical: 3,
    position: "absolute",
    right: 8
  },
  videoBadgeText: { color: "#ffffff", fontSize: 11, fontWeight: "700" },
  body: { gap: 5, padding: 10 },
  authorRow: { alignItems: "center", flexDirection: "row", gap: 8 },
  avatar: { backgroundColor: "rgba(255,255,255,0.08)", borderRadius: 13, height: 26, width: 26 },
  avatarFallback: { borderColor: chatGraphite.quietDivider, borderWidth: StyleSheet.hairlineWidth },
  authorText: { flex: 1 },
  authorName: { color: chatGraphite.primaryText, fontSize: 13, fontWeight: "800" },
  handle: { color: chatGraphite.secondaryText, fontSize: 11, fontWeight: "600" },
  eyebrow: { color: chatGraphite.senderAccent, fontSize: 11, fontWeight: "800", letterSpacing: 0.4 },
  caption: { color: chatGraphite.primaryText, fontSize: 13, lineHeight: 18 },
  unavailable: { color: chatGraphite.secondaryText, fontSize: 12, fontWeight: "600", lineHeight: 17 },
  footerRow: { alignItems: "center", flexDirection: "row", justifyContent: "space-between", marginTop: 1 },
  brand: { color: chatGraphite.secondaryText, fontSize: 10, fontWeight: "800", letterSpacing: 0.5 },
  cta: { color: colors.accent, fontSize: 12, fontWeight: "800" }
});
