/**
 * A PulseSoc post, rendered inside a message as the object it is.
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

export function PulsePostLinkCard({
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
        ? t("messaging:postCard.forbidden")
        : state.reason === "missing"
          ? t("messaging:postCard.missing")
          : t("messaging:postCard.unavailable")
      : "";

  return (
    <Pressable
      accessibilityRole="link"
      accessibilityLabel={
        preview
          ? t("messaging:postCard.a11yOpen", { author: authorLine || t("common:identity.member") })
          : t("messaging:postCard.a11yOpenGeneric")
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
          {preview.video ? (
            <View style={styles.videoBadge}>
              <Text style={styles.videoBadgeText}>{t("messaging:postCard.video")}</Text>
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
                {t("messaging:postCard.eyebrow")}
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
            {t("messaging:postCard.loading")}
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
          <Text style={styles.brand}>{t("messaging:postCard.eyebrow")}</Text>
          {state.status === "unavailable" ? null : <Text style={styles.cta}>{t("messaging:postCard.cta")}</Text>}
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
