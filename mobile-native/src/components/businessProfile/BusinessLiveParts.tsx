/**
 * Presentational parts for the "live" Business Profile screen.
 *
 * Everything here is deliberately dumb: it takes values and renders them. All
 * data loading, save state, and navigation live in the screen, which keeps this
 * file reviewable as pure UI and keeps the animations testable without a
 * network.
 *
 * Colour comes exclusively from `logiNexus.colors.businessLive`. Nothing in
 * this file inlines a hex value, which is what makes a future light theme a
 * single swap at the palette rather than an audit of every StyleSheet.
 */

import { Ionicons } from "@expo/vector-icons";
import { LinearGradient } from "expo-linear-gradient";
import { ReactNode, useState } from "react";
import { AccessibilityRole, Animated, Image, Pressable, StyleSheet, Text, TextInput, View, ViewStyle } from "react-native";
import Svg, { Circle, Defs, Line, LinearGradient as SvgLinearGradient, Stop } from "react-native-svg";
import { logiNexus } from "../../theme/logiNexus";
import { useBusinessLiveAmbient, useBusinessLiveMarquee, useBusinessLiveRing } from "../../theme/businessLiveMotion";

const palette = logiNexus.colors.businessLive;
const AnimatedCircle = Animated.createAnimatedComponent(Circle);

/* ------------------------------------------------------------------ header */

export function LiveSyncBadge({ reducedMotion, label }: { reducedMotion: boolean; label: string }) {
  // Rests fully lit under reduce-motion (`resetTo: 1`) rather than dark, so the
  // badge still reads as "on" when its ping is suppressed.
  const ping = useBusinessLiveAmbient(logiNexus.motion.ambient, reducedMotion, { resetTo: 1, pingPong: true });
  return (
    <View style={styles.liveBadge} accessible accessibilityRole="text" accessibilityLabel={label}>
      <View style={styles.liveDotWrap}>
        <Animated.View
          style={[
            styles.liveDotHalo,
            {
              opacity: ping.interpolate({ inputRange: [0, 1], outputRange: [0.55, 0] }),
              transform: [{ scale: ping.interpolate({ inputRange: [0, 1], outputRange: [1, 2.6] }) }]
            }
          ]}
        />
        <View style={styles.liveDot} />
      </View>
      <Text style={styles.liveBadgeText}>{label}</Text>
    </View>
  );
}

export function GhostPill({
  label,
  icon,
  onPress,
  accessibilityLabel,
  accessibilityHint
}: {
  label: string;
  icon?: keyof typeof Ionicons.glyphMap;
  onPress: () => void;
  accessibilityLabel?: string;
  accessibilityHint?: string;
}) {
  return (
    <Pressable
      onPress={onPress}
      accessibilityRole="button"
      accessibilityLabel={accessibilityLabel || label}
      accessibilityHint={accessibilityHint}
      style={({ pressed }) => [styles.ghostPill, pressed && styles.pressed]}
    >
      {icon ? <Ionicons name={icon} size={14} color={palette.secondary} /> : null}
      <Text style={styles.ghostPillText}>{label}</Text>
    </Pressable>
  );
}

/* ------------------------------------------------------------------ ticker */

export type LiveTickerStat = {
  key: string;
  label: string;
  value: string;
  /**
   * Marks a stat with no backing API yet. Rendered dim and without a trend
   * colour so a placeholder can never be mistaken for a measurement.
   */
  placeholder?: boolean;
  trend?: "up" | "down" | "flat";
};

/**
 * Auto-scrolling stat ticker.
 *
 * The row is rendered twice and translated by exactly one copy's width, so the
 * second copy lands where the first began and the loop has no seam.
 *
 * Under reduce-motion it becomes a static wrapping row — the same content,
 * fully readable, nothing moving. The container also carries a spoken summary
 * of every stat, so assistive tech never has to chase a moving target.
 */
export function LiveDataTicker({ stats, reducedMotion }: { stats: LiveTickerStat[]; reducedMotion: boolean }) {
  const [contentWidth, setContentWidth] = useState(0);
  const translate = useBusinessLiveMarquee(contentWidth, reducedMotion);
  if (!stats.length) return null;

  const spoken = stats.map((stat) => `${stat.label}: ${stat.value}`).join(". ");

  if (reducedMotion) {
    return (
      <View style={[styles.tickerShell, styles.tickerStatic]} accessible accessibilityLabel={spoken}>
        {stats.map((stat) => (
          <TickerCell key={stat.key} stat={stat} />
        ))}
      </View>
    );
  }

  return (
    <View style={styles.tickerShell} accessible accessibilityLabel={spoken}>
      <Animated.View
        // The visual copies are hidden from assistive tech: the container above
        // already speaks the full list once, and exposing the duplicated row
        // would read every stat twice.
        importantForAccessibility="no-hide-descendants"
        accessibilityElementsHidden
        style={[styles.tickerTrack, translate ? { transform: [{ translateX: translate }] } : null]}
      >
        <View style={styles.tickerCopy} onLayout={(event) => setContentWidth(event.nativeEvent.layout.width)}>
          {stats.map((stat) => (
            <TickerCell key={stat.key} stat={stat} />
          ))}
        </View>
        <View style={styles.tickerCopy}>
          {stats.map((stat) => (
            <TickerCell key={`echo-${stat.key}`} stat={stat} />
          ))}
        </View>
      </Animated.View>
      <LinearGradient
        pointerEvents="none"
        colors={[palette.background, "transparent"]}
        start={{ x: 0, y: 0.5 }}
        end={{ x: 1, y: 0.5 }}
        style={[styles.tickerFade, styles.tickerFadeLeft]}
      />
      <LinearGradient
        pointerEvents="none"
        colors={["transparent", palette.background]}
        start={{ x: 0, y: 0.5 }}
        end={{ x: 1, y: 0.5 }}
        style={[styles.tickerFade, styles.tickerFadeRight]}
      />
    </View>
  );
}

function TickerCell({ stat }: { stat: LiveTickerStat }) {
  const valueColor = stat.placeholder
    ? palette.textDim
    : stat.trend === "up"
      ? palette.accent
      : stat.trend === "down"
        ? palette.warning
        : palette.textPrimary;
  return (
    <View style={styles.tickerCell}>
      <Text style={styles.tickerLabel}>{stat.label}</Text>
      <Text style={[styles.tickerValue, { color: valueColor }]}>{stat.value}</Text>
      <View style={styles.tickerDivider} />
    </View>
  );
}

/* ------------------------------------------------------- completeness ring */

const RING_SIZE = 96;
const RING_STROKE = 8;
const RING_RADIUS = (RING_SIZE - RING_STROKE) / 2;
const RING_CIRCUMFERENCE = 2 * Math.PI * RING_RADIUS;

export function CompletenessMeter({
  percent,
  headline,
  suggestion,
  reducedMotion,
  ringLabel
}: {
  percent: number;
  headline: string;
  suggestion: string;
  reducedMotion: boolean;
  ringLabel: string;
}) {
  const progress = useBusinessLiveRing(percent, reducedMotion);
  const glow = useBusinessLiveAmbient(logiNexus.motion.ambient * 1.6, reducedMotion, { resetTo: 0, pingPong: true });
  const rounded = Math.round(Number.isFinite(percent) ? percent : 0);

  return (
    <View style={styles.meterRow}>
      <View
        style={styles.ringWrap}
        accessible
        accessibilityRole={"progressbar" as AccessibilityRole}
        accessibilityLabel={ringLabel}
        // The percentage is exposed as a value, not baked into the label, so
        // assistive tech announces the change when the real number arrives.
        accessibilityValue={{ min: 0, max: 100, now: rounded, text: `${rounded}%` }}
      >
        <Animated.View
          pointerEvents="none"
          style={[styles.ringGlow, { opacity: glow.interpolate({ inputRange: [0, 1], outputRange: [0.18, 0.45] }) }]}
        />
        <Svg width={RING_SIZE} height={RING_SIZE}>
          <Defs>
            <SvgLinearGradient id="businessLiveRing" x1="0" y1="0" x2="1" y2="1">
              <Stop offset="0" stopColor={palette.accent} />
              <Stop offset="1" stopColor={palette.secondary} />
            </SvgLinearGradient>
          </Defs>
          <Circle
            cx={RING_SIZE / 2}
            cy={RING_SIZE / 2}
            r={RING_RADIUS}
            stroke={palette.hairlineStrong}
            strokeWidth={RING_STROKE}
            fill="none"
          />
          <AnimatedCircle
            cx={RING_SIZE / 2}
            cy={RING_SIZE / 2}
            r={RING_RADIUS}
            stroke="url(#businessLiveRing)"
            strokeWidth={RING_STROKE}
            strokeLinecap="round"
            fill="none"
            strokeDasharray={`${RING_CIRCUMFERENCE} ${RING_CIRCUMFERENCE}`}
            // Offset runs full circumference (empty) down to zero (full), and
            // the -90deg rotation below starts the arc at twelve o'clock.
            strokeDashoffset={progress.interpolate({
              inputRange: [0, 100],
              outputRange: [RING_CIRCUMFERENCE, 0]
            })}
            transform={`rotate(-90 ${RING_SIZE / 2} ${RING_SIZE / 2})`}
          />
        </Svg>
        <View style={styles.ringCenter} pointerEvents="none">
          <Text style={styles.ringValue}>{rounded}</Text>
          <Text style={styles.ringUnit}>%</Text>
        </View>
      </View>
      <View style={styles.meterCopy}>
        <Text style={styles.meterHeadline}>{headline}</Text>
        <Text style={styles.meterSuggestion}>{suggestion}</Text>
      </View>
    </View>
  );
}

/* ------------------------------------------------------ shimmering surface */

/**
 * Card shell with a slow rotating border sheen.
 *
 * A single oversized gradient rotates behind the card; an inset opaque layer
 * covers everything but a hairline at the edge, so what the eye sees is light
 * travelling around the border. One transform drives it, so it stays on the
 * native driver.
 */
export function ShimmerBorderCard({
  children,
  reducedMotion,
  style
}: {
  children: ReactNode;
  reducedMotion: boolean;
  style?: ViewStyle;
}) {
  const spin = useBusinessLiveAmbient(logiNexus.motion.borderShimmer, reducedMotion, { resetTo: 0 });
  return (
    <View style={[styles.shimmerShell, style]}>
      <View style={styles.shimmerClip} pointerEvents="none">
        <Animated.View
          style={[
            styles.shimmerSpinner,
            { transform: [{ rotate: spin.interpolate({ inputRange: [0, 1], outputRange: ["0deg", "360deg"] }) }] }
          ]}
        >
          <LinearGradient
            colors={["transparent", palette.accentGlow, palette.secondary, "transparent"]}
            start={{ x: 0, y: 0 }}
            end={{ x: 1, y: 1 }}
            style={StyleSheet.absoluteFill}
          />
        </Animated.View>
      </View>
      <View style={styles.shimmerInner}>{children}</View>
    </View>
  );
}

/* -------------------------------------------------------- buyer preview */

export type BuyerPreviewChip = { key: string; icon: keyof typeof Ionicons.glyphMap; label: string; placeholder?: boolean };
export type BuyerPreviewStat = { key: string; value: string; label: string; placeholder?: boolean };

export function BuyerPreviewCard({
  coverUrl,
  avatarUrl,
  businessName,
  verified,
  handle,
  category,
  bio,
  chips,
  stats,
  reducedMotion
}: {
  coverUrl?: string;
  avatarUrl?: string;
  businessName: string;
  verified: boolean;
  handle: string;
  category: string;
  bio: string;
  chips: BuyerPreviewChip[];
  stats: BuyerPreviewStat[];
  reducedMotion: boolean;
}) {
  const sheen = useBusinessLiveAmbient(logiNexus.motion.borderShimmer * 0.8, reducedMotion, { resetTo: 0 });
  return (
    <ShimmerBorderCard reducedMotion={reducedMotion}>
      <View style={styles.cover}>
        {coverUrl ? <Image source={{ uri: coverUrl }} style={StyleSheet.absoluteFill} resizeMode="cover" /> : null}
        <PerspectiveGrid />
        <Animated.View
          pointerEvents="none"
          style={[
            StyleSheet.absoluteFill,
            {
              // Travels a little past both edges so the sheen enters and exits
              // rather than appearing and vanishing mid-card.
              transform: [{ translateX: sheen.interpolate({ inputRange: [0, 1], outputRange: [-260, 260] }) }]
            }
          ]}
        >
          <LinearGradient
            colors={["transparent", palette.sheen, "transparent"]}
            start={{ x: 0, y: 0 }}
            end={{ x: 1, y: 0 }}
            style={styles.coverSheen}
          />
        </Animated.View>
        <LinearGradient
          pointerEvents="none"
          colors={["transparent", palette.overlayScrim]}
          style={StyleSheet.absoluteFill}
        />
      </View>

      <View style={styles.previewBody}>
        <View style={styles.avatarWrap}>
          <View style={styles.avatarGlow} pointerEvents="none" />
          {avatarUrl ? (
            <Image source={{ uri: avatarUrl }} style={styles.avatar} />
          ) : (
            <View style={[styles.avatar, styles.avatarFallback]}>
              <Ionicons name="storefront-outline" size={26} color={palette.accent} />
            </View>
          )}
        </View>

        <View style={styles.nameRow}>
          <Text style={styles.businessName} numberOfLines={1}>
            {businessName}
          </Text>
          {verified ? (
            <Ionicons name="checkmark-circle" size={18} color={palette.secondary} accessibilityLabel="Verified" />
          ) : null}
        </View>
        <Text style={styles.handleLine} numberOfLines={1}>
          {handle}
          {category ? ` · ${category}` : ""}
        </Text>
        {bio ? <Text style={styles.bio}>{bio}</Text> : null}

        <View style={styles.chipRow}>
          {chips.map((chip) => (
            <View key={chip.key} style={styles.chip}>
              <Ionicons name={chip.icon} size={12} color={chip.placeholder ? palette.textDim : palette.accent} />
              <Text style={[styles.chipText, chip.placeholder && styles.chipTextPlaceholder]}>{chip.label}</Text>
            </View>
          ))}
        </View>

        <View style={styles.statFooter}>
          {stats.map((stat) => (
            <View key={stat.key} style={styles.statCell} accessible accessibilityLabel={`${stat.label}: ${stat.value}`}>
              <Text style={[styles.statValue, stat.placeholder && styles.statValuePlaceholder]}>{stat.value}</Text>
              <Text style={styles.statLabel}>{stat.label}</Text>
            </View>
          ))}
        </View>
      </View>
    </ShimmerBorderCard>
  );
}

/**
 * Static perspective grid behind the cover. Horizontal lines are spaced on a
 * squared curve so they bunch toward the horizon, and the verticals converge on
 * a centre vanishing point — the depth cue is geometry, not animation, so it
 * costs nothing per frame and stays put under reduce-motion.
 */
function PerspectiveGrid() {
  const width = 360;
  const height = 132;
  const horizon = height * 0.28;
  const rows = [0.18, 0.34, 0.52, 0.72, 1].map((t) => horizon + (height - horizon) * t * t);
  const columns = [-2, -1, 0, 1, 2];
  return (
    <Svg width="100%" height="100%" viewBox={`0 0 ${width} ${height}`} preserveAspectRatio="none" pointerEvents="none">
      {rows.map((y) => (
        <Line key={`h-${y}`} x1={0} y1={y} x2={width} y2={y} stroke={palette.gridLine} strokeWidth={0.8} />
      ))}
      {columns.map((c) => (
        <Line
          key={`v-${c}`}
          x1={width / 2 + c * (width / 4)}
          y1={height}
          x2={width / 2 + c * 14}
          y2={horizon}
          stroke={palette.gridLine}
          strokeWidth={0.8}
        />
      ))}
    </Svg>
  );
}

/* --------------------------------------------------------------- list rows */

export function DetailRow({
  icon,
  label,
  value,
  emptyConsequence,
  onPress
}: {
  icon: keyof typeof Ionicons.glyphMap;
  label: string;
  /** Empty string means "not set" — the row then explains what that costs. */
  value: string;
  emptyConsequence: string;
  onPress: () => void;
}) {
  const isEmpty = !value.trim();
  return (
    <Pressable
      onPress={onPress}
      accessibilityRole="button"
      accessibilityLabel={isEmpty ? `${label}. Not set. ${emptyConsequence}` : `${label}. ${value}`}
      accessibilityHint={`Edit ${label.toLowerCase()}`}
      style={({ pressed }) => [styles.row, pressed && styles.pressed]}
    >
      <View style={[styles.iconTile, isEmpty && styles.iconTileEmpty]}>
        <Ionicons name={icon} size={16} color={isEmpty ? palette.warning : palette.accent} />
      </View>
      <View style={styles.rowCopy}>
        <Text style={styles.rowLabel}>{label}</Text>
        {isEmpty ? (
          <Text style={styles.rowEmpty}>Not set — {emptyConsequence}</Text>
        ) : (
          <Text style={styles.rowValue} numberOfLines={2}>
            {value}
          </Text>
        )}
      </View>
      <Ionicons name="chevron-forward" size={16} color={palette.textDim} />
    </Pressable>
  );
}

/**
 * A detail row that edits its value in place.
 *
 * Used for the fields the seller-application draft endpoint owns outright. The
 * alternative — bouncing to the full application form to change one line — is
 * what makes the Save footer meaningless, because nothing on this screen would
 * ever be dirty. Fields that belong to a different API (the handle) or are a
 * multi-step choice (category) still navigate; see `DetailRow`.
 */
export function EditableDetailRow({
  icon,
  label,
  value,
  placeholder,
  emptyConsequence,
  error,
  onChangeValue,
  keyboardType
}: {
  icon: keyof typeof Ionicons.glyphMap;
  label: string;
  value: string;
  placeholder: string;
  emptyConsequence: string;
  error?: string;
  onChangeValue: (next: string) => void;
  keyboardType?: "default" | "email-address" | "url";
}) {
  const [editing, setEditing] = useState(false);
  const isEmpty = !value.trim();

  if (editing) {
    return (
      <View style={styles.row}>
        <View style={[styles.iconTile, isEmpty && styles.iconTileEmpty]}>
          <Ionicons name={icon} size={16} color={isEmpty ? palette.warning : palette.accent} />
        </View>
        <View style={styles.rowCopy}>
          <Text style={styles.rowLabel}>{label}</Text>
          <TextInput
            value={value}
            onChangeText={onChangeValue}
            onBlur={() => setEditing(false)}
            onSubmitEditing={() => setEditing(false)}
            placeholder={placeholder}
            placeholderTextColor={palette.textDim}
            accessibilityLabel={label}
            autoFocus
            autoCapitalize={keyboardType === "email-address" || keyboardType === "url" ? "none" : "sentences"}
            keyboardType={keyboardType === "url" ? "default" : keyboardType}
            style={styles.rowInput}
          />
          {error ? <Text style={styles.rowError}>{error}</Text> : null}
        </View>
        <Pressable
          onPress={() => setEditing(false)}
          accessibilityRole="button"
          accessibilityLabel={`Done editing ${label.toLowerCase()}`}
          hitSlop={8}
        >
          <Ionicons name="checkmark" size={18} color={palette.accent} />
        </Pressable>
      </View>
    );
  }

  return (
    <Pressable
      onPress={() => setEditing(true)}
      accessibilityRole="button"
      accessibilityLabel={isEmpty ? `${label}. Not set. ${emptyConsequence}` : `${label}. ${value}`}
      accessibilityHint={`Edit ${label.toLowerCase()}`}
      style={({ pressed }) => [styles.row, pressed && styles.pressed]}
    >
      <View style={[styles.iconTile, isEmpty && styles.iconTileEmpty]}>
        <Ionicons name={icon} size={16} color={isEmpty ? palette.warning : palette.accent} />
      </View>
      <View style={styles.rowCopy}>
        <Text style={styles.rowLabel}>{label}</Text>
        {isEmpty ? (
          <Text style={styles.rowEmpty}>Not set — {emptyConsequence}</Text>
        ) : (
          <Text style={styles.rowValue} numberOfLines={2}>
            {value}
          </Text>
        )}
        {error ? <Text style={styles.rowError}>{error}</Text> : null}
      </View>
      <Ionicons name="create-outline" size={16} color={palette.textDim} />
    </Pressable>
  );
}

/**
 * A row that is part of the design but has no field behind it yet. Rendered
 * present and explained, never tappable — a chevron that leads nowhere is worse
 * than an honest blank.
 */
export function UnbackedDetailRow({
  icon,
  label,
  emptyConsequence,
  note
}: {
  icon: keyof typeof Ionicons.glyphMap;
  label: string;
  emptyConsequence: string;
  note: string;
}) {
  return (
    <View
      style={styles.row}
      accessible
      accessibilityRole="text"
      accessibilityState={{ disabled: true }}
      accessibilityLabel={`${label}. Not set. ${emptyConsequence}. ${note}`}
    >
      <View style={[styles.iconTile, styles.iconTileEmpty]}>
        <Ionicons name={icon} size={16} color={palette.textDim} />
      </View>
      <View style={styles.rowCopy}>
        <Text style={styles.rowLabel}>{label}</Text>
        <Text style={styles.rowEmpty}>Not set — {emptyConsequence}</Text>
        <Text style={styles.rowNote}>{note}</Text>
      </View>
    </View>
  );
}

export function ConnectedRow({
  icon,
  label,
  detail,
  empty,
  onPress
}: {
  icon: keyof typeof Ionicons.glyphMap;
  label: string;
  detail: string;
  empty?: boolean;
  onPress: () => void;
}) {
  return (
    <Pressable
      onPress={onPress}
      accessibilityRole="button"
      accessibilityLabel={`${label}. ${detail}`}
      style={({ pressed }) => [styles.row, pressed && styles.pressed]}
    >
      <View style={styles.iconTile}>
        <Ionicons name={icon} size={16} color={palette.secondary} />
      </View>
      <View style={styles.rowCopy}>
        <Text style={styles.rowLabel}>{label}</Text>
        <Text style={[styles.rowValue, empty && styles.rowValueDim]}>{detail}</Text>
      </View>
      <Ionicons name="chevron-forward" size={16} color={palette.textDim} />
    </Pressable>
  );
}

/* ------------------------------------------------------------ trust callout */

export function TrustCallout({
  title,
  body,
  actionLabel,
  onPress,
  reducedMotion
}: {
  title: string;
  body: string;
  actionLabel: string;
  onPress: () => void;
  reducedMotion: boolean;
}) {
  const scan = useBusinessLiveAmbient(logiNexus.motion.scanSweep, reducedMotion, { resetTo: 0 });
  return (
    <View style={styles.trustCard}>
      <Animated.View
        pointerEvents="none"
        style={[
          styles.scanStripe,
          { transform: [{ translateY: scan.interpolate({ inputRange: [0, 1], outputRange: [-70, 190] }) }] }
        ]}
      >
        <LinearGradient
          colors={["transparent", palette.warningGlow, "transparent"]}
          style={StyleSheet.absoluteFill}
        />
      </Animated.View>
      <View style={styles.trustHeader}>
        <View style={styles.trustIcon}>
          <Ionicons name="shield-checkmark-outline" size={16} color={palette.warning} />
        </View>
        <Text style={styles.trustTitle}>{title}</Text>
      </View>
      <Text style={styles.trustBody}>{body}</Text>
      <Pressable
        onPress={onPress}
        accessibilityRole="button"
        accessibilityLabel={actionLabel}
        style={({ pressed }) => [styles.trustButton, pressed && styles.pressed]}
      >
        <Text style={styles.trustButtonText}>{actionLabel}</Text>
        <Ionicons name="arrow-forward" size={14} color={palette.warning} />
      </Pressable>
    </View>
  );
}

/* ---------------------------------------------------------- footer actions */

export function FooterActions({
  dirty,
  saving,
  discardLabel,
  saveLabel,
  onDiscard,
  onSave,
  reducedMotion
}: {
  dirty: boolean;
  saving: boolean;
  discardLabel: string;
  saveLabel: string;
  onDiscard: () => void;
  onSave: () => void;
  reducedMotion: boolean;
}) {
  // The save glow only breathes while there is something to save, so the motion
  // is a signal rather than decoration.
  const glow = useBusinessLiveAmbient(logiNexus.motion.ambient, dirty ? reducedMotion : true, {
    resetTo: 0,
    pingPong: true
  });
  return (
    <View style={styles.footer}>
      <Pressable
        onPress={onDiscard}
        disabled={!dirty || saving}
        accessibilityRole="button"
        accessibilityLabel={discardLabel}
        accessibilityState={{ disabled: !dirty || saving }}
        style={({ pressed }) => [styles.discardButton, (!dirty || saving) && styles.disabled, pressed && styles.pressed]}
      >
        <Text style={styles.discardText}>{discardLabel}</Text>
      </Pressable>
      <View style={styles.saveWrap}>
        <Animated.View
          pointerEvents="none"
          style={[
            styles.saveGlow,
            { opacity: dirty ? glow.interpolate({ inputRange: [0, 1], outputRange: [0.25, 0.6] }) : 0 }
          ]}
        />
        <Pressable
          onPress={onSave}
          disabled={!dirty || saving}
          accessibilityRole="button"
          accessibilityLabel={saveLabel}
          accessibilityState={{ disabled: !dirty || saving, busy: saving }}
          style={({ pressed }) => [styles.saveButton, pressed && styles.pressed]}
        >
          <LinearGradient
            colors={dirty ? [palette.accent, palette.secondary] : [palette.panelRaised, palette.panelRaised]}
            start={{ x: 0, y: 0 }}
            end={{ x: 1, y: 1 }}
            style={StyleSheet.absoluteFill}
          />
          <Text style={[styles.saveText, !dirty && styles.saveTextDisabled]}>{saveLabel}</Text>
        </Pressable>
      </View>
    </View>
  );
}

/* ------------------------------------------------------------------ shared */

export function SectionHeading({ title, caption }: { title: string; caption?: string }) {
  return (
    <View style={styles.sectionHeading}>
      <Text style={styles.sectionTitle}>{title}</Text>
      {caption ? <Text style={styles.sectionCaption}>{caption}</Text> : null}
    </View>
  );
}

export function LivePanel({ children, style }: { children: ReactNode; style?: ViewStyle }) {
  return <View style={[styles.panel, style]}>{children}</View>;
}

const styles = StyleSheet.create({
  pressed: { opacity: 0.72 },
  disabled: { opacity: 0.4 },

  liveBadge: {
    flexDirection: "row",
    alignItems: "center",
    gap: logiNexus.spacing.xs + 2,
    paddingHorizontal: logiNexus.spacing.md,
    paddingVertical: 5,
    borderRadius: logiNexus.radius.capsule,
    borderWidth: StyleSheet.hairlineWidth,
    borderColor: palette.hairlineStrong,
    backgroundColor: palette.accentSoft
  },
  liveDotWrap: { width: 8, height: 8, alignItems: "center", justifyContent: "center" },
  liveDot: { width: 6, height: 6, borderRadius: 3, backgroundColor: palette.accent },
  liveDotHalo: { position: "absolute", width: 8, height: 8, borderRadius: 4, backgroundColor: palette.accent },
  liveBadgeText: { ...logiNexus.typography.label, color: palette.accent, letterSpacing: 0.6 },

  ghostPill: {
    flexDirection: "row",
    alignItems: "center",
    gap: logiNexus.spacing.xs + 2,
    paddingHorizontal: logiNexus.spacing.lg,
    paddingVertical: 8,
    borderRadius: logiNexus.radius.capsule,
    borderWidth: StyleSheet.hairlineWidth,
    borderColor: palette.hairline,
    backgroundColor: palette.panel
  },
  ghostPillText: { ...logiNexus.typography.button, color: palette.textPrimary },

  tickerShell: {
    height: 54,
    justifyContent: "center",
    overflow: "hidden",
    borderTopWidth: StyleSheet.hairlineWidth,
    borderBottomWidth: StyleSheet.hairlineWidth,
    borderColor: palette.hairline
  },
  tickerStatic: { height: undefined, flexDirection: "row", flexWrap: "wrap", paddingVertical: logiNexus.spacing.md },
  tickerTrack: { flexDirection: "row" },
  tickerCopy: { flexDirection: "row" },
  tickerCell: { flexDirection: "row", alignItems: "center", gap: logiNexus.spacing.sm, paddingRight: logiNexus.spacing.lg },
  tickerLabel: { ...logiNexus.typography.metadata, color: palette.textMuted },
  tickerValue: { ...logiNexus.typography.metadata, color: palette.textPrimary },
  tickerDivider: { width: 3, height: 3, borderRadius: 2, marginLeft: logiNexus.spacing.md, backgroundColor: palette.textDim },
  tickerFade: { position: "absolute", top: 0, bottom: 0, width: 36 },
  tickerFadeLeft: { left: 0 },
  tickerFadeRight: { right: 0 },

  meterRow: { flexDirection: "row", alignItems: "center", gap: logiNexus.spacing.lg },
  ringWrap: { width: RING_SIZE, height: RING_SIZE, alignItems: "center", justifyContent: "center" },
  ringGlow: {
    position: "absolute",
    width: RING_SIZE + 18,
    height: RING_SIZE + 18,
    borderRadius: (RING_SIZE + 18) / 2,
    backgroundColor: palette.accentGlow
  },
  ringCenter: { position: "absolute", flexDirection: "row", alignItems: "flex-start" },
  ringValue: { ...logiNexus.typography.metric, color: palette.textPrimary },
  ringUnit: { ...logiNexus.typography.metadata, color: palette.textMuted, marginTop: 4 },
  meterCopy: { flex: 1, gap: logiNexus.spacing.xs },
  meterHeadline: { ...logiNexus.typography.sectionTitle, color: palette.textPrimary },
  meterSuggestion: { ...logiNexus.typography.body, color: palette.textMuted },

  shimmerShell: {
    borderRadius: logiNexus.radius.card,
    overflow: "hidden",
    backgroundColor: palette.hairlineStrong,
    padding: 1
  },
  shimmerClip: { ...StyleSheet.absoluteFillObject, overflow: "hidden" },
  // Square and oversized so a rotation never exposes a corner of the card.
  shimmerSpinner: { position: "absolute", top: "-75%", left: "-75%", width: "250%", height: "250%" },
  shimmerInner: {
    borderRadius: logiNexus.radius.card - 1,
    overflow: "hidden",
    backgroundColor: palette.panelStrong
  },

  cover: { height: 132, backgroundColor: palette.panelRaised, overflow: "hidden" },
  coverSheen: { width: 120, height: "100%" },
  previewBody: { padding: logiNexus.spacing.lg, paddingTop: 0, gap: logiNexus.spacing.sm },
  avatarWrap: { marginTop: -30, width: 64, height: 64, alignItems: "center", justifyContent: "center" },
  avatarGlow: {
    position: "absolute",
    width: 78,
    height: 78,
    borderRadius: 39,
    backgroundColor: palette.accentGlow,
    opacity: 0.35
  },
  avatar: { width: 64, height: 64, borderRadius: 32, borderWidth: 2, borderColor: palette.panelStrong },
  avatarFallback: { alignItems: "center", justifyContent: "center", backgroundColor: palette.panelRaised },
  nameRow: { flexDirection: "row", alignItems: "center", gap: logiNexus.spacing.sm },
  businessName: { ...logiNexus.typography.title, color: palette.textPrimary, flexShrink: 1 },
  handleLine: { ...logiNexus.typography.metadata, color: palette.textMuted },
  bio: { ...logiNexus.typography.body, color: palette.textMuted },
  chipRow: { flexDirection: "row", flexWrap: "wrap", gap: logiNexus.spacing.sm, marginTop: logiNexus.spacing.xs },
  chip: {
    flexDirection: "row",
    alignItems: "center",
    gap: 5,
    paddingHorizontal: logiNexus.spacing.md,
    paddingVertical: 5,
    borderRadius: logiNexus.radius.capsule,
    borderWidth: StyleSheet.hairlineWidth,
    borderColor: palette.hairline,
    backgroundColor: palette.panel
  },
  chipText: { ...logiNexus.typography.metadata, color: palette.textPrimary },
  chipTextPlaceholder: { color: palette.textDim, fontStyle: "italic" },
  statFooter: {
    flexDirection: "row",
    marginTop: logiNexus.spacing.md,
    paddingTop: logiNexus.spacing.md,
    borderTopWidth: StyleSheet.hairlineWidth,
    borderColor: palette.hairline
  },
  statCell: { flex: 1, alignItems: "center", gap: 2 },
  statValue: { ...logiNexus.typography.sectionTitle, color: palette.textPrimary },
  statValuePlaceholder: { color: palette.textDim, fontStyle: "italic" },
  statLabel: { ...logiNexus.typography.label, color: palette.textMuted },

  row: {
    flexDirection: "row",
    alignItems: "center",
    gap: logiNexus.spacing.md,
    paddingVertical: logiNexus.spacing.md,
    paddingHorizontal: logiNexus.spacing.lg
  },
  iconTile: {
    width: 34,
    height: 34,
    borderRadius: logiNexus.radius.medium,
    alignItems: "center",
    justifyContent: "center",
    backgroundColor: palette.accentSoft,
    borderWidth: StyleSheet.hairlineWidth,
    borderColor: palette.hairline
  },
  iconTileEmpty: { backgroundColor: palette.warningSoft },
  rowCopy: { flex: 1, gap: 2 },
  rowLabel: { ...logiNexus.typography.label, color: palette.textMuted },
  rowValue: { ...logiNexus.typography.body, color: palette.textPrimary },
  rowValueDim: { color: palette.textDim },
  rowEmpty: { ...logiNexus.typography.body, color: palette.warning, fontStyle: "italic" },
  rowNote: { ...logiNexus.typography.metadata, color: palette.textDim },
  rowError: { ...logiNexus.typography.metadata, color: palette.danger },
  rowInput: {
    ...logiNexus.typography.body,
    color: palette.textPrimary,
    paddingVertical: 4,
    paddingHorizontal: 0,
    borderBottomWidth: StyleSheet.hairlineWidth,
    borderColor: palette.hairlineStrong
  },

  trustCard: {
    borderRadius: logiNexus.radius.card,
    borderWidth: StyleSheet.hairlineWidth,
    borderColor: palette.warningGlow,
    backgroundColor: palette.warningSoft,
    padding: logiNexus.spacing.lg,
    gap: logiNexus.spacing.sm,
    overflow: "hidden"
  },
  scanStripe: { position: "absolute", left: 0, right: 0, height: 70 },
  trustHeader: { flexDirection: "row", alignItems: "center", gap: logiNexus.spacing.sm },
  trustIcon: {
    width: 30,
    height: 30,
    borderRadius: logiNexus.radius.medium,
    alignItems: "center",
    justifyContent: "center",
    backgroundColor: palette.panel
  },
  trustTitle: { ...logiNexus.typography.sectionTitle, color: palette.textPrimary, flex: 1 },
  trustBody: { ...logiNexus.typography.body, color: palette.textMuted },
  trustButton: {
    alignSelf: "flex-start",
    flexDirection: "row",
    alignItems: "center",
    gap: logiNexus.spacing.sm,
    marginTop: logiNexus.spacing.xs,
    paddingHorizontal: logiNexus.spacing.lg,
    paddingVertical: 8,
    borderRadius: logiNexus.radius.capsule,
    borderWidth: StyleSheet.hairlineWidth,
    borderColor: palette.warningGlow,
    backgroundColor: palette.panel
  },
  trustButtonText: { ...logiNexus.typography.button, color: palette.warning },

  footer: { flexDirection: "row", gap: logiNexus.spacing.md, alignItems: "center" },
  discardButton: {
    flex: 1,
    alignItems: "center",
    paddingVertical: logiNexus.spacing.md,
    borderRadius: logiNexus.radius.capsule,
    borderWidth: StyleSheet.hairlineWidth,
    borderColor: palette.hairline,
    backgroundColor: "transparent"
  },
  discardText: { ...logiNexus.typography.button, color: palette.textMuted },
  saveWrap: { flex: 1.4, alignItems: "center", justifyContent: "center" },
  saveGlow: {
    position: "absolute",
    left: 10,
    right: 10,
    top: 6,
    bottom: -4,
    borderRadius: logiNexus.radius.capsule,
    backgroundColor: palette.accentGlow
  },
  saveButton: {
    width: "100%",
    alignItems: "center",
    justifyContent: "center",
    paddingVertical: logiNexus.spacing.md,
    borderRadius: logiNexus.radius.capsule,
    overflow: "hidden"
  },
  saveText: { ...logiNexus.typography.button, color: palette.background },
  saveTextDisabled: { color: palette.textDim },

  sectionHeading: { gap: 2, marginBottom: logiNexus.spacing.md },
  sectionTitle: { ...logiNexus.typography.sectionTitle, color: palette.textPrimary },
  sectionCaption: { ...logiNexus.typography.metadata, color: palette.textMuted },

  panel: {
    borderRadius: logiNexus.radius.card,
    borderWidth: StyleSheet.hairlineWidth,
    borderColor: palette.hairline,
    backgroundColor: palette.panel,
    overflow: "hidden"
  }
});
