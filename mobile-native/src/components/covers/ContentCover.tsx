/**
 * ContentCover — every grid tile gets a meaningful preview, never a black or
 * blank square.
 *
 * One component covers all content kinds: photos/videos/reels render their
 * thumbnail with the right overlays; audio gets a designed card (artwork or
 * gradient + waveform); text posts become typography quote cards; articles,
 * marketplace listings, memories, events and collections each get purposeful
 * fallbacks. When an image URL exists it is shown with a premium skeleton
 * underneath and a fade-in on load; when it is missing or fails to load, the
 * kind-specific designed card takes over — the failure path IS a design, not
 * an empty rectangle.
 *
 * Theme-aware via useTheme(): gradients, text and skeleton tones all follow
 * the active palette (Dark / Black / White / Light Futuristic), and every
 * animation collapses to a static state under Reduce Motion.
 */

import { LinearGradient } from "expo-linear-gradient";
import { memo, useEffect, useMemo, useRef, useState } from "react";
import { Animated, Image, StyleSheet, Text, View, ViewStyle } from "react-native";
import { Ionicons } from "@expo/vector-icons";
import { useTheme } from "../../theme/ThemeContext";

export type ContentCoverKind =
  | "photo"
  | "video"
  | "reel"
  | "audio"
  | "music"
  | "text"
  | "article"
  | "shared"
  | "listing"
  | "memory"
  | "event"
  | "collection"
  | "document";

export type ContentCoverProps = {
  kind: ContentCoverKind;
  /** Best available thumbnail/artwork URL; the designed card renders without it. */
  imageUrl?: string | null;
  /** Body text for text/shared posts; also the accessible description. */
  text?: string;
  /** Title line for audio/article/memory/event/collection cards. */
  title?: string;
  /** Secondary line — artist, author, seller, location. */
  subtitle?: string;
  durationSeconds?: number;
  /** Video has an attached soundtrack — shows the small music badge. */
  hasMusic?: boolean;
  viewCount?: number;
  muted?: boolean;
  /** Marketplace category slug/name; drives the placeholder icon. */
  category?: string;
  /** Memory/event date label, already formatted (e.g. "Aug 2024"). */
  dateLabel?: string;
  readingMinutes?: number;
  /** Normalized 0..1 amplitude samples; a stable pattern is derived when absent. */
  waveform?: number[];
  /** Up to four URLs for the collection collage. */
  collageUrls?: string[];
  borderRadius?: number;
  style?: ViewStyle;
  testID?: string;
};

/* ------------------------------------------------------------------ *
 * Deterministic visual identity per item
 * ------------------------------------------------------------------ */

function hashSeed(value: string): number {
  let hash = 5381;
  for (let index = 0; index < value.length; index += 1) {
    hash = ((hash << 5) + hash + value.charCodeAt(index)) >>> 0;
  }
  return hash;
}

const DARK_GRADIENTS: [string, string][] = [
  ["#0e2a3f", "#15436a"],
  ["#221645", "#3b2a72"],
  ["#0c332f", "#155a50"],
  ["#3a1d33", "#63305a"],
  ["#123246", "#1d5a6e"],
  ["#302038", "#4c3a68"]
];

const LIGHT_GRADIENTS: [string, string][] = [
  ["#dcebfa", "#c3dbf3"],
  ["#e6e0f8", "#d2c8f0"],
  ["#d8f1ea", "#bfe4d8"],
  ["#fae3ef", "#f0cade"],
  ["#dceef5", "#c5e0ec"],
  ["#ece4f2", "#d9cde6"]
];

function gradientFor(seedText: string, light: boolean): [string, string] {
  const sets = light ? LIGHT_GRADIENTS : DARK_GRADIENTS;
  return sets[hashSeed(seedText || "pulsesoc") % sets.length];
}

/* ------------------------------------------------------------------ *
 * Small shared pieces
 * ------------------------------------------------------------------ */

export function formatCoverDuration(seconds?: number): string {
  const total = Math.floor(Number(seconds) || 0);
  if (total <= 0) return "";
  const minutes = Math.floor(total / 60);
  const rest = String(total % 60).padStart(2, "0");
  return `${minutes}:${rest}`;
}

export function formatCoverCount(count?: number): string {
  const value = Number(count) || 0;
  if (value <= 0) return "";
  if (value >= 1_000_000) return `${(value / 1_000_000).toFixed(1).replace(/\.0$/, "")}M`;
  if (value >= 1_000) return `${(value / 1_000).toFixed(1).replace(/\.0$/, "")}K`;
  return String(value);
}

/** Themed pulse placeholder shown while a thumbnail loads. Never flashes. */
function CoverSkeleton() {
  const theme = useTheme();
  const pulse = useRef(new Animated.Value(0.6)).current;
  useEffect(() => {
    if (theme.reduceMotion) {
      pulse.setValue(0.75);
      return;
    }
    const loop = Animated.loop(
      Animated.sequence([
        Animated.timing(pulse, { toValue: 1, duration: 900, useNativeDriver: true }),
        Animated.timing(pulse, { toValue: 0.6, duration: 900, useNativeDriver: true })
      ])
    );
    loop.start();
    return () => loop.stop();
  }, [pulse, theme.reduceMotion]);
  return (
    <Animated.View
      style={[StyleSheet.absoluteFill, { backgroundColor: theme.colors.surfaceRaised, opacity: pulse }]}
      testID="cover-skeleton"
    />
  );
}

/**
 * Thumbnail with skeleton-under + fade-in. On load error it reports up so the
 * kind-specific designed card replaces it — the grid never shows a dead tile.
 */
function CoverImage({ uri, onFailed }: { uri: string; onFailed: () => void }) {
  const theme = useTheme();
  const [loaded, setLoaded] = useState(false);
  const fade = useRef(new Animated.Value(0)).current;
  useEffect(() => {
    if (!loaded) return;
    Animated.timing(fade, { toValue: 1, duration: theme.duration(220), useNativeDriver: true }).start();
  }, [fade, loaded, theme]);
  return (
    <View style={StyleSheet.absoluteFill}>
      {!loaded ? <CoverSkeleton /> : null}
      <Animated.View style={[StyleSheet.absoluteFill, { opacity: theme.reduceMotion ? 1 : fade }]}>
        <Image
          source={{ uri }}
          resizeMode="cover"
          style={StyleSheet.absoluteFill}
          onLoad={() => setLoaded(true)}
          onError={onFailed}
        />
      </Animated.View>
    </View>
  );
}

function GradientCard({ seed, children }: { seed: string; children?: React.ReactNode }) {
  const theme = useTheme();
  const light = theme.scheme === "light";
  const [from, to] = gradientFor(seed, light);
  return (
    <LinearGradient colors={[from, to]} start={{ x: 0, y: 0 }} end={{ x: 1, y: 1 }} style={styles.fillCenter}>
      {children}
    </LinearGradient>
  );
}

/* ------------------------------------------------------------------ *
 * Kind-specific designed cards (the "no image" and overlay layers)
 * ------------------------------------------------------------------ */

function useCardInk() {
  const theme = useTheme();
  const light = theme.scheme === "light";
  return {
    theme,
    light,
    ink: light ? "#17242f" : "#f2f7fb",
    inkSoft: light ? "rgba(23,36,47,0.66)" : "rgba(242,247,251,0.72)",
    chipBg: light ? "rgba(255,255,255,0.72)" : "rgba(0,0,0,0.42)"
  };
}

function quoteFontSize(text: string): number {
  const length = text.trim().length;
  if (length <= 30) return 21;
  if (length <= 80) return 17;
  if (length <= 160) return 14;
  return 12;
}

function TextQuoteCard({ text }: { text: string }) {
  const { ink, inkSoft } = useCardInk();
  const body = text.trim() || "PulseSoc";
  return (
    <GradientCard seed={body}>
      <View style={styles.quoteWrap}>
        <Text
          style={[styles.quoteText, { color: ink, fontSize: quoteFontSize(body) }]}
          numberOfLines={6}
          adjustsFontSizeToFit
          minimumFontScale={0.7}
        >
          {body}
        </Text>
        <Text style={[styles.brand, { color: inkSoft }]}>PulseSoc</Text>
      </View>
    </GradientCard>
  );
}

const BAR_COUNT = 22;

function waveformBars(seedText: string, samples?: number[]): number[] {
  if (samples && samples.length >= 4) {
    const bars: number[] = [];
    for (let index = 0; index < BAR_COUNT; index += 1) {
      const sample = samples[Math.floor((index / BAR_COUNT) * samples.length)] ?? 0.4;
      bars.push(Math.min(1, Math.max(0.16, Number(sample) || 0.4)));
    }
    return bars;
  }
  const seed = hashSeed(seedText || "audio");
  return Array.from({ length: BAR_COUNT }, (_unused, index) => {
    const wave = Math.sin((index + (seed % 17)) * 0.85) * 0.5 + 0.5;
    return 0.2 + wave * 0.75;
  });
}

function AudioCard({
  title,
  subtitle,
  durationSeconds,
  waveform
}: {
  title?: string;
  subtitle?: string;
  durationSeconds?: number;
  waveform?: number[];
}) {
  const { theme, ink, inkSoft } = useCardInk();
  const bars = useMemo(() => waveformBars(title || subtitle || "", waveform), [subtitle, title, waveform]);
  const breathe = useRef(new Animated.Value(0.7)).current;
  useEffect(() => {
    if (theme.reduceMotion) {
      breathe.setValue(0.9);
      return;
    }
    const loop = Animated.loop(
      Animated.sequence([
        Animated.timing(breathe, { toValue: 1, duration: 1400, useNativeDriver: true }),
        Animated.timing(breathe, { toValue: 0.7, duration: 1400, useNativeDriver: true })
      ])
    );
    loop.start();
    return () => loop.stop();
  }, [breathe, theme.reduceMotion]);
  const duration = formatCoverDuration(durationSeconds);
  return (
    <GradientCard seed={`${title || ""}${subtitle || ""}audio`}>
      <View style={styles.audioWrap}>
        <Ionicons name="musical-notes" size={22} color={ink} />
        <Animated.View style={[styles.waveRow, { opacity: breathe }]}>
          {bars.map((bar, index) => (
            <View
              key={index}
              style={[styles.waveBar, { backgroundColor: ink, height: 4 + bar * 26, opacity: 0.55 + bar * 0.45 }]}
            />
          ))}
        </Animated.View>
        {title ? (
          <Text style={[styles.cardTitle, { color: ink }]} numberOfLines={1}>
            {title}
          </Text>
        ) : null}
        {subtitle || duration ? (
          <Text style={[styles.cardMeta, { color: inkSoft }]} numberOfLines={1}>
            {[subtitle, duration].filter(Boolean).join(" · ")}
          </Text>
        ) : null}
      </View>
    </GradientCard>
  );
}

const CATEGORY_ICONS: [RegExp, keyof typeof Ionicons.glyphMap][] = [
  [/electron|phone|computer|tech|gadget/i, "hardware-chip-outline"],
  [/fashion|cloth|apparel|shoe|wear/i, "shirt-outline"],
  [/vehicle|car|auto|moto|bike/i, "car-outline"],
  [/furniture|home|garden|decor/i, "bed-outline"],
  [/service|repair|clean|labor/i, "construct-outline"],
  [/sport|fitness|outdoor/i, "basketball-outline"],
  [/book|media|music|game/i, "book-outline"],
  [/beauty|health/i, "sparkles-outline"],
  [/baby|kid|toy/i, "balloon-outline"],
  [/food|grocer/i, "restaurant-outline"],
  [/pet|animal/i, "paw-outline"]
];

function categoryIcon(category?: string): keyof typeof Ionicons.glyphMap {
  const value = String(category || "");
  for (const [pattern, icon] of CATEGORY_ICONS) {
    if (pattern.test(value)) return icon;
  }
  return "pricetag-outline";
}

function IconCard({
  icon,
  label,
  sublabel,
  seed
}: {
  icon: keyof typeof Ionicons.glyphMap;
  label?: string;
  sublabel?: string;
  seed: string;
}) {
  const { ink, inkSoft } = useCardInk();
  return (
    <GradientCard seed={seed}>
      <View style={styles.iconCardWrap}>
        <Ionicons name={icon} size={30} color={ink} />
        {label ? (
          <Text style={[styles.cardTitle, { color: ink }]} numberOfLines={2}>
            {label}
          </Text>
        ) : null}
        {sublabel ? (
          <Text style={[styles.cardMeta, { color: inkSoft }]} numberOfLines={1}>
            {sublabel}
          </Text>
        ) : null}
      </View>
    </GradientCard>
  );
}

function MemoryCard({ title, dateLabel }: { title?: string; dateLabel?: string }) {
  const { ink, inkSoft, chipBg } = useCardInk();
  return (
    <GradientCard seed={`${title || ""}${dateLabel || ""}memory`}>
      <View style={styles.iconCardWrap}>
        <View style={[styles.dateChip, { backgroundColor: chipBg }]}>
          <Ionicons name="calendar-outline" size={14} color={ink} />
          {dateLabel ? <Text style={[styles.dateChipText, { color: ink }]}>{dateLabel}</Text> : null}
        </View>
        {title ? (
          <Text style={[styles.cardTitle, { color: ink }]} numberOfLines={2}>
            {title}
          </Text>
        ) : (
          <Ionicons name="images-outline" size={26} color={inkSoft} />
        )}
      </View>
    </GradientCard>
  );
}

function CollageCard({ urls, title }: { urls: string[]; title?: string }) {
  const theme = useTheme();
  const cells = urls.filter(Boolean).slice(0, 4);
  if (cells.length === 0) {
    return <IconCard icon="albums-outline" label={title} seed={`${title || ""}collection`} />;
  }
  return (
    <View style={styles.collage}>
      {[0, 1, 2, 3].map((index) => (
        <View key={index} style={[styles.collageCell, { backgroundColor: theme.colors.surfaceRaised }]}>
          {cells[index] ? <Image source={{ uri: cells[index] }} resizeMode="cover" style={StyleSheet.absoluteFill} /> : null}
        </View>
      ))}
    </View>
  );
}

/* ------------------------------------------------------------------ *
 * Overlays for playable media
 * ------------------------------------------------------------------ */

function MediaOverlay({
  kind,
  durationSeconds,
  hasMusic,
  viewCount,
  muted
}: {
  kind: ContentCoverKind;
  durationSeconds?: number;
  hasMusic?: boolean;
  viewCount?: number;
  muted?: boolean;
}) {
  const playable = kind === "video" || kind === "reel";
  const duration = formatCoverDuration(durationSeconds);
  const views = formatCoverCount(viewCount);
  if (!playable && !duration && !views) return null;
  return (
    <View pointerEvents="none" style={StyleSheet.absoluteFill}>
      {playable ? (
        <View style={styles.playCenter}>
          <View style={styles.playCircle}>
            <Ionicons name="play" size={18} color="#ffffff" style={styles.playIcon} />
          </View>
        </View>
      ) : null}
      {hasMusic || muted ? (
        <View style={styles.topRightBadges}>
          {hasMusic ? <Ionicons name="musical-note" size={13} color="#ffffff" /> : null}
          {muted ? <Ionicons name="volume-mute" size={13} color="#ffffff" /> : null}
        </View>
      ) : null}
      {duration || views ? (
        <View style={styles.bottomRow}>
          {views ? (
            <View style={styles.pill}>
              <Ionicons name="eye-outline" size={11} color="#ffffff" />
              <Text style={styles.pillText}>{views}</Text>
            </View>
          ) : (
            <View />
          )}
          {duration ? (
            <View style={styles.pill}>
              <Text style={styles.pillText}>{duration}</Text>
            </View>
          ) : null}
        </View>
      ) : null}
    </View>
  );
}

/* ------------------------------------------------------------------ *
 * The cover itself
 * ------------------------------------------------------------------ */

function designedCard(props: ContentCoverProps) {
  const { kind, text, title, subtitle, durationSeconds, waveform, category, dateLabel, readingMinutes, collageUrls } = props;
  switch (kind) {
    case "audio":
    case "music":
      return <AudioCard title={title} subtitle={subtitle} durationSeconds={durationSeconds} waveform={waveform} />;
    case "text":
    case "shared":
      return <TextQuoteCard text={text || title || ""} />;
    case "article":
      return (
        <IconCard
          icon="newspaper-outline"
          label={title || text}
          sublabel={[category, readingMinutes ? `${readingMinutes} min` : ""].filter(Boolean).join(" · ")}
          seed={`${title || text || ""}article`}
        />
      );
    case "listing":
      return <IconCard icon={categoryIcon(category)} label={title} sublabel={category} seed={`${title || ""}${category || ""}`} />;
    case "memory":
      return <MemoryCard title={title} dateLabel={dateLabel} />;
    case "event":
      return <MemoryCard title={title} dateLabel={dateLabel} />;
    case "collection":
      return <CollageCard urls={collageUrls || []} title={title} />;
    case "document":
      return <IconCard icon="document-text-outline" label={title} seed={`${title || ""}document`} />;
    case "video":
    case "reel":
      return <IconCard icon="film-outline" label={title} seed={`${title || text || ""}video`} />;
    default:
      return <IconCard icon="image-outline" label={title} seed={`${title || text || ""}photo`} />;
  }
}

export const ContentCover = memo(function ContentCover(props: ContentCoverProps) {
  const { kind, imageUrl, collageUrls, borderRadius = 0, style, testID } = props;
  const [imageFailed, setImageFailed] = useState(false);
  useEffect(() => {
    setImageFailed(false);
  }, [imageUrl]);
  const isCollage = kind === "collection" && (collageUrls?.length || 0) > 0;
  const showImage = Boolean(imageUrl) && !imageFailed && !isCollage;
  return (
    <View style={[styles.root, { borderRadius }, style]} testID={testID || `content-cover-${kind}`}>
      {showImage ? <CoverImage uri={String(imageUrl)} onFailed={() => setImageFailed(true)} /> : designedCard(props)}
      <MediaOverlay
        kind={kind}
        durationSeconds={props.durationSeconds}
        hasMusic={props.hasMusic}
        viewCount={props.viewCount}
        muted={props.muted}
      />
    </View>
  );
});

const styles = StyleSheet.create({
  root: { flex: 1, overflow: "hidden" },
  fillCenter: { ...StyleSheet.absoluteFillObject, alignItems: "center", justifyContent: "center" },
  quoteWrap: { alignItems: "center", flex: 1, justifyContent: "center", padding: 12, width: "100%" },
  quoteText: { fontWeight: "700", letterSpacing: 0.2, lineHeight: undefined, textAlign: "center" },
  brand: { bottom: 6, fontSize: 9, fontWeight: "700", letterSpacing: 1.4, position: "absolute", textTransform: "uppercase" },
  audioWrap: { alignItems: "center", gap: 7, justifyContent: "center", padding: 10, width: "100%" },
  waveRow: { alignItems: "center", flexDirection: "row", gap: 2, height: 32, justifyContent: "center" },
  waveBar: { borderRadius: 2, width: 3 },
  cardTitle: { fontSize: 12, fontWeight: "700", maxWidth: "92%", textAlign: "center" },
  cardMeta: { fontSize: 10, fontWeight: "600", maxWidth: "92%", textAlign: "center" },
  iconCardWrap: { alignItems: "center", gap: 7, justifyContent: "center", padding: 10, width: "100%" },
  dateChip: { alignItems: "center", borderRadius: 999, flexDirection: "row", gap: 4, paddingHorizontal: 8, paddingVertical: 3 },
  dateChipText: { fontSize: 10, fontWeight: "700" },
  collage: { flex: 1, flexDirection: "row", flexWrap: "wrap" },
  collageCell: { borderColor: "rgba(0,0,0,0.15)", borderWidth: StyleSheet.hairlineWidth, height: "50%", overflow: "hidden", width: "50%" },
  playCenter: { ...StyleSheet.absoluteFillObject, alignItems: "center", justifyContent: "center" },
  playCircle: {
    alignItems: "center",
    backgroundColor: "rgba(0,0,0,0.45)",
    borderColor: "rgba(255,255,255,0.65)",
    borderRadius: 999,
    borderWidth: 1,
    height: 38,
    justifyContent: "center",
    width: 38
  },
  playIcon: { marginLeft: 2 },
  topRightBadges: { flexDirection: "row", gap: 5, position: "absolute", right: 6, top: 6 },
  bottomRow: {
    alignItems: "center",
    bottom: 5,
    flexDirection: "row",
    justifyContent: "space-between",
    left: 5,
    position: "absolute",
    right: 5
  },
  pill: {
    alignItems: "center",
    backgroundColor: "rgba(0,0,0,0.55)",
    borderRadius: 7,
    flexDirection: "row",
    gap: 3,
    paddingHorizontal: 5,
    paddingVertical: 2
  },
  pillText: { color: "#ffffff", fontSize: 10, fontWeight: "700" }
});
