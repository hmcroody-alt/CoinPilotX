/**
 * One Marketplace recommendation on a Reel, as small as it can usefully be.
 *
 * ## Where this renders, and why that is the whole safety argument
 *
 * Not as an overlay. This component is mounted as the **first child of the
 * Reel's caption column** — the bottom-anchored `styles.caption` block in
 * `ReelPlayerCard`. That single fact discharges most of the brief's hard rules
 * without any arithmetic:
 *
 *   * **It cannot cover the caption.** It is a sibling in the same flex column,
 *     laid out above the title. The column is anchored by its *bottom* edge, so
 *     adding a row to the top grows it upward — the caption text, the music
 *     chip and the mute button do not move by a pixel.
 *   * **It cannot cover the action rail.** The column is `right: 76` and the
 *     rail is `right: 12` at `width: 60`, so the rail ends at 72 and the column
 *     starts at 76. The clearance is the existing layout's, not a number this
 *     file chose.
 *   * **It cannot cover the creator header.** The header is pinned to the top
 *     of the frame; this grows from the bottom, one compact row high.
 *   * **It cannot cover the progress bar,** which sits below the column's bottom
 *     anchor, on the safe-area line.
 *
 * The alternative — an absolutely positioned chip at some computed `bottom` —
 * was rejected for exactly the reason it looks easier: the caption's height is
 * content-dependent, so any constant is a guess that a two-line caption in one
 * locale turns into an overlap. There is no constant here to get wrong.
 *
 * ## It cannot pause the Reel
 *
 * Structurally, not by convention: this component receives no playback props,
 * no player ref and no media callbacks. There is nothing in scope to pause,
 * mute, or seek.
 *
 * ## Why it disappears on its own
 *
 * The brief asks for a chip that "collapses elegantly if ignored". A product
 * suggestion that sits on top of someone's video for the entire watch is an
 * advert with no close button, whatever it is labelled. So it folds itself away
 * after `IGNORED_MS` of being visible and un-interacted-with, and it does not
 * come back for that reel. Auto-collapse is **not** reported as feedback: the
 * user did not tell us anything, and writing a `see_fewer` because they kept
 * watching their video would poison the model with an opinion nobody expressed.
 */
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Animated, Image, Pressable, StyleSheet, Text, View } from "react-native";
import {
  CommerceFeedbackAction,
  CommercePlacement,
  recordCommerceEngagement
} from "../api/commerceDiscovery";
import { useTranslation } from "../i18n/I18nContext";
import { colors } from "../theme/colors";
import { logiNexus } from "../theme/logiNexus";
import { useLogiNexusReducedMotion } from "../theme/logiNexusMotion";
import { useCommerceImpression } from "./useCommerceImpression";

type Navigation = { navigate: (...args: any[]) => void };

export type ReelsCommerceChipProps = {
  placement: CommercePlacement;
  /** True only for the reel the user is actually watching. */
  isActive: boolean;
  /** Server-owned dwell in ms, from the serve response. */
  visibleDwellMs: number;
  navigation: Navigation;
  onFeedback: (placement: CommercePlacement, action: CommerceFeedbackAction) => void;
};

/** How long the chip lingers, visible and ignored, before folding itself away. */
const IGNORED_MS = 8000;

/** The fold. Short — this is a dismissal, not a transition the user waits on. */
const COLLAPSE_MS = logiNexus.motion.quick;

export function ReelsCommerceChip({
  placement,
  isActive,
  visibleDwellMs,
  navigation,
  onFeedback
}: ReelsCommerceChipProps) {
  const { t } = useTranslation();
  const reducedMotion = useLogiNexusReducedMotion();

  const [gone, setGone] = useState(false);
  const fade = useRef(new Animated.Value(1)).current;
  const clickingRef = useRef(false);

  // The chip is on screen exactly when its reel is the active one, so the
  // list's per-reel viewability *is* this unit's viewability.
  useCommerceImpression({
    placement,
    isViewable: isActive && !gone,
    visibleDwellMs,
    active: isActive
  });

  /**
   * Fold, then (optionally) report.
   *
   * `action` is null for the ignore timer: the fold is identical, but nothing is
   * written, because "kept watching the video" is not a preference.
   */
  const retire = useCallback(
    (action: CommerceFeedbackAction | null) => {
      const finish = () => {
        setGone(true);
        if (action) onFeedback(placement, action);
      };
      if (reducedMotion) {
        finish();
        return;
      }
      Animated.timing(fade, {
        toValue: 0,
        duration: COLLAPSE_MS,
        useNativeDriver: true
      }).start(finish);
    },
    [fade, onFeedback, placement, reducedMotion]
  );

  // The ignore timer runs only while the reel is actually being watched, so a
  // chip on a reel the user scrolled past mid-countdown still gets its full
  // moment when they scroll back.
  useEffect(() => {
    if (!isActive || gone) return undefined;
    const timer = setTimeout(() => retire(null), IGNORED_MS);
    return () => clearTimeout(timer);
  }, [isActive, gone, retire]);

  const handleOpen = useCallback(async () => {
    if (clickingRef.current) return;
    clickingRef.current = true;
    // Before navigating: the transition unmounts this chip, and a beacon
    // started on the way out races its own component's teardown.
    await recordCommerceEngagement(placement, "click").catch(() => undefined);
    clickingRef.current = false;
    const listingId = placement.product.listingId;
    if (!listingId) return;
    navigation.navigate("MarketplaceProduct", {
      listingId,
      title: placement.product.title || undefined
    });
  }, [navigation, placement]);

  const product = placement.product;

  const priceText = useMemo(() => product.priceLabel || "", [product.priceLabel]);

  const label = t(placement.labelKey || "commerce:discovery.label.recommended");

  if (gone || !product.title) return null;

  return (
    <Animated.View style={[styles.wrap, { opacity: fade }]} testID={`reels-commerce-${placement.placementId}`}>
      <Pressable
        accessibilityRole="button"
        accessibilityLabel={[label, product.title, product.sellerStoreName, priceText]
          .filter(Boolean)
          .join(". ")}
        testID="reels-commerce-body"
        onPress={handleOpen}
        style={styles.chip}
      >
        {product.coverImageUrl ? (
          <Image source={{ uri: product.coverImageUrl }} style={styles.cover} resizeMode="cover" />
        ) : (
          <View style={[styles.cover, styles.coverEmpty]} />
        )}
        <View style={styles.copy}>
          {/* The label is the disclosure. It is never "Sponsored" here — the
              reels surface only ever serves organic and house placements, and
              the server picks the key from the promotion class, not the score. */}
          <Text style={styles.label} numberOfLines={1}>
            {label}
          </Text>
          <Text style={styles.title} numberOfLines={1}>
            {product.title}
          </Text>
        </View>
        {priceText ? (
          <Text style={styles.price} numberOfLines={1}>
            {priceText}
          </Text>
        ) : null}
      </Pressable>
      <Pressable
        accessibilityRole="button"
        accessibilityLabel={t("commerce:discovery.menu.hide")}
        testID="reels-commerce-dismiss"
        // Generous touch target on a small glyph, expanded outward so the box
        // does not grow and start crowding the caption underneath it.
        hitSlop={{ top: 10, bottom: 10, left: 10, right: 10 }}
        onPress={() => retire("hide")}
        style={styles.dismiss}
      >
        <Text style={styles.dismissGlyph}>×</Text>
      </Pressable>
    </Animated.View>
  );
}

const styles = StyleSheet.create({
  chip: {
    alignItems: "center",
    flex: 1,
    flexDirection: "row",
    gap: 8
  },
  copy: {
    flex: 1
  },
  cover: {
    backgroundColor: "rgba(255,255,255,0.10)",
    borderRadius: 8,
    height: 34,
    width: 34
  },
  coverEmpty: {
    borderColor: "rgba(255,255,255,0.16)",
    borderWidth: 1
  },
  dismiss: {
    alignItems: "center",
    height: 22,
    justifyContent: "center",
    width: 22
  },
  dismissGlyph: {
    color: "rgba(244,247,251,0.72)",
    fontSize: 15,
    fontWeight: "700"
  },
  label: {
    color: "rgba(244,247,251,0.70)",
    fontSize: 9,
    fontWeight: "800",
    letterSpacing: 0.4,
    textTransform: "uppercase"
  },
  price: {
    color: colors.accent,
    fontSize: 12,
    fontWeight: "900"
  },
  title: {
    color: colors.text,
    fontSize: 12,
    fontWeight: "800",
    marginTop: 1
  },
  wrap: {
    alignItems: "center",
    alignSelf: "stretch",
    backgroundColor: "rgba(2,9,18,0.72)",
    borderColor: "rgba(65,239,211,0.24)",
    borderRadius: 14,
    borderWidth: 1,
    flexDirection: "row",
    gap: 8,
    // Bottom margin only: the column is bottom-anchored, so this is the gap
    // between the chip and the caption title beneath it.
    marginBottom: 8,
    paddingHorizontal: 8,
    paddingVertical: 7
  }
});
