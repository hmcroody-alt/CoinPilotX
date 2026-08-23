import { useCallback, useEffect, useRef, useState } from "react";
import { Alert, Image, Pressable, StyleSheet, Text, View } from "react-native";
import {
  isAdExpired,
  recordAdClick,
  recordAdEvent,
  recordAdImpression,
  recordAdViewability,
  SponsoredAd
} from "../api/ads";
import { openNativeRoute } from "../navigation/nativeRouteActions";
import { colors } from "../theme/colors";
import { logiNexus } from "../theme/logiNexus";
import { createThemedStyles } from "../theme/themedStyles";

const VIEWABILITY_THRESHOLD_MS = 1000;

type SponsoredAdCardProps = {
  ad: SponsoredAd;
  isViewable: boolean;
  edgeInset?: number;
  navigation: { navigate: (...args: any[]) => void };
  onHide: (ad: SponsoredAd) => void;
};

function openDestination(navigation: SponsoredAdCardProps["navigation"], url: string) {
  const target = String(url || "").trim();
  if (!target) return;
  if (target.startsWith("/")) {
    openNativeRoute(navigation, target);
    return;
  }
  if (/^https?:\/\//i.test(target)) {
    Alert.alert("Sponsor destination recorded", "PulseSoc recorded your tap. This ad points to a site outside PulseSoc, and the app does not open those yet.");
  }
}

export function SponsoredAdCard({ ad, isViewable, edgeInset = 12, navigation, onHide }: SponsoredAdCardProps) {
  const [menuOpen, setMenuOpen] = useState(false);
  const impressionIdRef = useRef(0);
  const visibleMsRef = useRef(0);
  const viewableSinceRef = useRef<number | null>(null);
  const viewabilityReportedRef = useRef(false);
  const clickingRef = useRef(false);

  // Record the impression once, when the card first mounts into the feed. The
  // backend dedupes on the delivery token, so a re-render cannot double count.
  useEffect(() => {
    if (isAdExpired(ad)) return;
    let mounted = true;
    recordAdImpression(ad).then((impressionId) => {
      if (mounted) impressionIdRef.current = impressionId;
    });
    return () => {
      mounted = false;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [ad.creativeId, ad.campaignId, ad.deliveryToken]);

  const flushViewability = useCallback(() => {
    if (viewableSinceRef.current != null) {
      visibleMsRef.current += Date.now() - viewableSinceRef.current;
      viewableSinceRef.current = null;
    }
    if (
      !viewabilityReportedRef.current &&
      impressionIdRef.current > 0 &&
      visibleMsRef.current >= VIEWABILITY_THRESHOLD_MS
    ) {
      viewabilityReportedRef.current = true;
      recordAdViewability(impressionIdRef.current, visibleMsRef.current);
      // Deliberately nothing else. This block used to also fire
      // `recordAdEvent(ad, "conversion")`, which meant every ad that stayed on
      // screen for one second wrote a row the reporting layer counts as a
      // conversion — the same fact the line above already records as
      // `impressions.viewable = 1`, filed under a word that means something
      // else entirely. It was the only producer of `conversion` events in the
      // product, on either client, so "conversions" on the advertiser portal
      // and on the internal command centre
      // (services/dashboard_ads_command_center.py:386) was a viewability count
      // wearing an attribution label. There is no order link, no value and no
      // post-tap instrumentation anywhere, so the honest count of conversions
      // here is zero, and a zero is what this now writes.
    }
  }, [ad]);

  // Accumulate on-screen dwell time from the parent list's viewability signal.
  useEffect(() => {
    if (isViewable) {
      if (viewableSinceRef.current == null) viewableSinceRef.current = Date.now();
      const timer = setTimeout(flushViewability, VIEWABILITY_THRESHOLD_MS);
      return () => clearTimeout(timer);
    }
    flushViewability();
    return undefined;
  }, [isViewable, flushViewability]);

  useEffect(() => () => flushViewability(), [flushViewability]);

  const handlePress = useCallback(async () => {
    if (clickingRef.current) return;
    clickingRef.current = true;
    const serverDestination = await recordAdClick(ad);
    clickingRef.current = false;
    openDestination(navigation, serverDestination || ad.destinationUrl);
  }, [ad, navigation]);

  const handleHide = useCallback(() => {
    setMenuOpen(false);
    recordAdEvent(ad, "hide").catch(() => undefined);
    onHide(ad);
  }, [ad, onHide]);

  const handleReport = useCallback(() => {
    setMenuOpen(false);
    Alert.alert("Report this ad?", "Tell us why you're reporting this sponsored post.", [
      { text: "Cancel", style: "cancel" },
      {
        text: "Not relevant",
        onPress: () => {
          recordAdEvent(ad, "report", "not_relevant").catch(() => undefined);
          onHide(ad);
        }
      },
      {
        text: "Inappropriate",
        style: "destructive",
        onPress: () => {
          recordAdEvent(ad, "report", "inappropriate").catch(() => undefined);
          onHide(ad);
        }
      }
    ]);
  }, [ad, onHide]);

  const posterUrl = ad.thumbnailUrl || ad.mediaUrl;
  const isVideo = ad.creativeType === "video" || ad.mediaType === "video";
  const showMedia = Boolean(posterUrl) && (ad.creativeType === "image" || isVideo || ad.creativeType === "hologram");

  return (
    <View style={[styles.card, { marginHorizontal: edgeInset }]} accessibilityRole="none">
      <View style={styles.header}>
        <View style={styles.disclosureRow}>
          <Text style={styles.disclosure} accessibilityLabel="Sponsored advertisement">
            {ad.label || "Sponsored"}
          </Text>
          <View style={styles.disclosureDot} />
          <Text style={styles.disclosureHint}>Ad</Text>
        </View>
        <Pressable
          hitSlop={10}
          accessibilityRole="button"
          accessibilityLabel="Ad options"
          onPress={() => setMenuOpen((open) => !open)}
          style={styles.menuButton}
        >
          <Text style={styles.menuGlyph}>⋯</Text>
        </Pressable>
      </View>

      {menuOpen ? (
        <View style={styles.menu}>
          <Pressable style={styles.menuItem} accessibilityRole="button" onPress={handleHide}>
            <Text style={styles.menuItemText}>Hide this ad</Text>
          </Pressable>
          {ad.reportable ? (
            <Pressable style={styles.menuItem} accessibilityRole="button" onPress={handleReport}>
              <Text style={styles.menuItemText}>Report ad</Text>
            </Pressable>
          ) : null}
        </View>
      ) : null}

      <Pressable
        accessibilityRole="button"
        accessibilityLabel={`${ad.title || ad.label}. ${ad.body}. ${ad.callToAction}`}
        onPress={handlePress}
      >
        {showMedia ? (
          <View style={styles.mediaWrap}>
            <Image source={{ uri: posterUrl }} style={styles.media} resizeMode="cover" />
            {isVideo ? (
              <View style={styles.playBadge} pointerEvents="none">
                <Text style={styles.playGlyph}>▶</Text>
              </View>
            ) : null}
          </View>
        ) : null}
        {ad.title ? <Text style={styles.title}>{ad.title}</Text> : null}
        {ad.body ? <Text style={styles.body}>{ad.body}</Text> : null}
        <View style={styles.ctaRow}>
          <Text style={styles.cta}>{ad.callToAction}</Text>
          <Text style={styles.ctaArrow}>→</Text>
        </View>
      </Pressable>
    </View>
  );
}

const styles = createThemedStyles(() => ({
  /**
   * A translucent panel rather than `colors.surface`, which is opaque.
   *
   * This card is only ever rendered as a row of the Home Feed, and the feed now
   * sits on the shared `PulseBackground` rather than on a flat fill of its own.
   * An opaque card there punches a rectangular hole in the ambient layer every
   * few posts — the one discontinuity a scrolling eye reliably catches.
   * `home.surfaceGlass` is the feed's existing panel token, so the disclosure
   * still reads as a distinct bordered surface against the organic posts around
   * it while the environment stays continuous behind it.
   */
  card: {
    backgroundColor: logiNexus.colors.home.surfaceGlass,
    borderRadius: logiNexus.radius.large,
    borderWidth: 1,
    borderColor: colors.border,
    paddingHorizontal: logiNexus.spacing.lg,
    paddingVertical: logiNexus.spacing.md,
    marginVertical: logiNexus.spacing.sm
  },
  header: {
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "space-between",
    marginBottom: logiNexus.spacing.sm
  },
  disclosureRow: {
    flexDirection: "row",
    alignItems: "center",
    gap: logiNexus.spacing.sm
  },
  disclosure: {
    ...logiNexus.typography.label,
    color: colors.accent,
    letterSpacing: 0.6,
    textTransform: "uppercase"
  },
  disclosureDot: {
    width: 3,
    height: 3,
    borderRadius: 2,
    backgroundColor: colors.muted
  },
  disclosureHint: {
    ...logiNexus.typography.metadata,
    color: colors.muted
  },
  menuButton: {
    paddingHorizontal: logiNexus.spacing.sm
  },
  menuGlyph: {
    color: colors.muted,
    fontSize: 20,
    fontWeight: "900"
  },
  menu: {
    backgroundColor: colors.surfaceRaised,
    borderRadius: logiNexus.radius.medium,
    borderWidth: 1,
    borderColor: colors.border,
    marginBottom: logiNexus.spacing.sm,
    overflow: "hidden"
  },
  menuItem: {
    paddingHorizontal: logiNexus.spacing.lg,
    paddingVertical: logiNexus.spacing.md
  },
  menuItemText: {
    ...logiNexus.typography.body,
    color: colors.text
  },
  mediaWrap: {
    borderRadius: logiNexus.radius.medium,
    overflow: "hidden",
    marginBottom: logiNexus.spacing.md,
    backgroundColor: colors.background,
    alignItems: "center",
    justifyContent: "center"
  },
  media: {
    width: "100%",
    aspectRatio: 16 / 9
  },
  playBadge: {
    position: "absolute",
    width: 54,
    height: 54,
    borderRadius: 27,
    backgroundColor: "rgba(5, 9, 16, 0.66)",
    alignItems: "center",
    justifyContent: "center"
  },
  playGlyph: {
    color: colors.text,
    fontSize: 22,
    marginLeft: 3
  },
  title: {
    ...logiNexus.typography.sectionTitle,
    color: colors.text,
    marginBottom: logiNexus.spacing.xs
  },
  body: {
    ...logiNexus.typography.body,
    color: colors.muted,
    marginBottom: logiNexus.spacing.md
  },
  ctaRow: {
    flexDirection: "row",
    alignItems: "center",
    alignSelf: "flex-start",
    gap: logiNexus.spacing.sm,
    backgroundColor: colors.signalDim,
    borderRadius: logiNexus.radius.capsule,
    paddingHorizontal: logiNexus.spacing.lg,
    paddingVertical: logiNexus.spacing.sm
  },
  cta: {
    ...logiNexus.typography.button,
    color: colors.accent
  },
  ctaArrow: {
    color: colors.accent,
    fontWeight: "900"
  }
}));
