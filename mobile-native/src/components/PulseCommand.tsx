import { ReactNode, useEffect, useRef } from "react";
import { AccessibilityInfo, Animated, Easing, Image, Platform, Pressable, ScrollView, StyleProp, StyleSheet, Text, TextInput, View, ViewStyle, useWindowDimensions } from "react-native";
import { colors } from "../theme/colors";
import { logiNexus, LogiNexusTone, toneColor } from "../theme/logiNexus";
import { createThemedStyles } from "../theme/themedStyles";

export function PulseCommandPanel({ children, style, tone = "default" }: { children: ReactNode; style?: StyleProp<ViewStyle>; tone?: LogiNexusTone }) {
  const color = toneColor(tone);
  return (
    <View style={[styles.panel, { borderColor: `${color}66` }, style]}>
      <View style={[styles.panelGlow, { backgroundColor: color }]} />
      {children}
    </View>
  );
}

export function PulseCommandHeader({
  title,
  subtitle,
  status = "Connected",
  actions,
  tone = "default"
}: {
  title: string;
  subtitle?: string;
  status?: string;
  actions?: ReactNode;
  tone?: LogiNexusTone;
}) {
  const color = toneColor(tone);
  const { width } = useWindowDimensions();
  const compact = Platform.OS !== "web" || width < 560;
  return (
    <PulseCommandPanel tone={tone} style={[styles.header, compact && styles.headerCompact]}>
      <View style={styles.headerCopy}>
        <Text style={styles.eyebrow}>PULSE COMMAND</Text>
        <Text style={styles.headerTitle} numberOfLines={compact ? 2 : 1}>{title}</Text>
        {subtitle ? <Text style={styles.headerSubtitle} numberOfLines={2}>{subtitle}</Text> : null}
      </View>
      <View style={[styles.headerSide, compact && styles.headerSideCompact]}>
        <View style={[styles.statusPill, { borderColor: `${color}70`, backgroundColor: `${color}18` }]}>
          <View style={[styles.statusDot, { backgroundColor: color }]} />
          <Text style={[styles.statusText, { color }]} numberOfLines={1}>{status}</Text>
        </View>
        {actions}
      </View>
    </PulseCommandPanel>
  );
}

export function PulseCommandSearch({
  value,
  onChangeText,
  placeholder = "Search Pulse Command"
}: {
  value: string;
  onChangeText: (value: string) => void;
  placeholder?: string;
}) {
  return (
    <View style={styles.searchWrap}>
      <Text style={styles.searchGlyph}>⌕</Text>
      <TextInput
        accessibilityLabel={placeholder}
        autoCapitalize="none"
        placeholder={placeholder}
        placeholderTextColor={colors.muted}
        returnKeyType="search"
        style={styles.search}
        value={value}
        onChangeText={onChangeText}
      />
    </View>
  );
}

export function PulseCommandSegmentRail({
  items,
  selected,
  onSelect
}: {
  items: Array<{ key: string; label: string; count?: number }>;
  selected: string;
  onSelect: (key: string) => void;
}) {
  const rail = useRef<ScrollView>(null);
  const selectedIndex = Math.max(0, items.findIndex((item) => item.key === selected));
  useEffect(() => {
    rail.current?.scrollTo({ x: Math.max(0, selectedIndex * 88 - 24), animated: true });
  }, [selectedIndex]);
  return (
    <ScrollView ref={rail} horizontal style={styles.segmentRail} contentContainerStyle={styles.segmentRailContent} showsHorizontalScrollIndicator={false} accessibilityRole="tablist" testID="pulse-command-filter-rail">
      {items.map((item) => {
        const active = item.key === selected;
        return (
          <Pressable
            key={item.key}
            accessibilityRole="tab"
            accessibilityState={{ selected: active }}
            testID={`pulse-command-filter-${item.key}`}
            style={[styles.segment, active && styles.segmentActive]}
            onPress={() => onSelect(item.key)}
          >
            <Text style={[styles.segmentText, active && styles.segmentTextActive]}>{item.label}</Text>
            {typeof item.count === "number" && item.count > 0 ? <Text style={styles.segmentCount}>{item.count}</Text> : null}
          </Pressable>
        );
      })}
    </ScrollView>
  );
}

export function PulseCommandAvatar({ label, imageUrl, active, tone = "default", size = 48 }: { label?: string; imageUrl?: string; active?: boolean; tone?: LogiNexusTone; size?: number }) {
  const color = toneColor(tone);
  return (
    <View style={[styles.avatar, { borderColor: active ? color : colors.border, borderRadius: size / 2, height: size, width: size }]}>
      {imageUrl ? <Image accessibilityIgnoresInvertColors source={{ uri: imageUrl }} style={[styles.avatarImage, { borderRadius: size / 2 }]} resizeMode="cover" /> : <Text style={[styles.avatarText, { color }]}>{initials(label)}</Text>}
      {active ? <View style={[styles.avatarSignal, { backgroundColor: color }]} /> : null}
    </View>
  );
}

export function PulseCommandOrb({ size = 58, warning = false }: { size?: number; warning?: boolean }) {
  const pulse = useRef(new Animated.Value(0)).current;
  useEffect(() => {
    let animation: Animated.CompositeAnimation | null = null;
    AccessibilityInfo.isReduceMotionEnabled().then((reduced) => {
      if (reduced) return;
      animation = Animated.loop(Animated.sequence([
        Animated.timing(pulse, { toValue: 1, duration: 1500, easing: Easing.inOut(Easing.sin), useNativeDriver: true }),
        Animated.timing(pulse, { toValue: 0, duration: 1500, easing: Easing.inOut(Easing.sin), useNativeDriver: true })
      ]));
      animation.start();
    }).catch(() => undefined);
    return () => animation?.stop();
  }, [pulse]);
  const tone = warning ? colors.warning : colors.accent;
  return (
    <View accessibilityLabel={warning ? "Pulse Command reconnecting" : "Pulse Command connected"} style={[styles.orb, { height: size, width: size, borderRadius: size / 2 }]}>
      <Animated.View style={[styles.orbHalo, { borderColor: tone, borderRadius: size / 2, transform: [{ scale: pulse.interpolate({ inputRange: [0, 1], outputRange: [0.86, 1.1] }) }], opacity: pulse.interpolate({ inputRange: [0, 1], outputRange: [0.38, 0.08] }) }]} />
      <View style={[styles.orbCore, { backgroundColor: tone, shadowColor: tone }]} />
    </View>
  );
}

function initials(label?: string) {
  const parts = String(label || "P").trim().split(/\s+/).filter(Boolean);
  return (parts.length > 1 ? `${parts[0][0]}${parts[parts.length - 1][0]}` : parts[0]?.slice(0, 2) || "P").toUpperCase();
}

export function PulseCommandAction({
  label,
  onPress,
  tone = "default",
  disabled,
  compact
}: {
  label: string;
  onPress: () => void;
  tone?: LogiNexusTone;
  disabled?: boolean;
  compact?: boolean;
}) {
  const color = toneColor(tone);
  return (
    <Pressable
      accessibilityRole="button"
      accessibilityLabel={label}
      disabled={disabled}
      style={({ pressed }) => [
        styles.action,
        compact && styles.actionCompact,
        { borderColor: `${color}80`, backgroundColor: `${color}16`, opacity: disabled ? 0.5 : pressed ? 0.78 : 1 }
      ]}
      onPress={onPress}
    >
      <Text style={[styles.actionText, { color }]} numberOfLines={1}>{label}</Text>
    </Pressable>
  );
}

export function PulseCommandMetric({ value, label, tone = "default" }: { value: string | number; label: string; tone?: LogiNexusTone }) {
  const color = toneColor(tone);
  return (
    <View style={styles.metric}>
      <Text style={[styles.metricValue, { color }]}>{value}</Text>
      <Text style={styles.metricLabel}>{label}</Text>
    </View>
  );
}

const styles = createThemedStyles(() => ({
  action: {
    alignItems: "center",
    borderRadius: logiNexus.radius.medium,
    borderWidth: 1,
    justifyContent: "center",
    minHeight: 44,
    paddingHorizontal: logiNexus.spacing.lg
  },
  actionCompact: {
    minHeight: 36,
    paddingHorizontal: logiNexus.spacing.md
  },
  actionText: {
    ...logiNexus.typography.button
  },
  avatar: {
    alignItems: "center",
    backgroundColor: "rgba(7, 16, 29, 0.86)",
    borderRadius: logiNexus.radius.circular,
    borderWidth: 1,
    height: 48,
    justifyContent: "center",
    width: 48
  },
  avatarSignal: {
    borderColor: colors.background,
    borderRadius: 7,
    borderWidth: 2,
    bottom: 0,
    height: 12,
    position: "absolute",
    right: 0,
    width: 12
  },
  avatarImage: {
    borderRadius: 999,
    height: "100%",
    width: "100%"
  },
  avatarText: {
    fontSize: 14,
    fontWeight: "900"
  },
  orb: {
    alignItems: "center",
    backgroundColor: "rgba(8,20,33,0.96)",
    borderColor: "rgba(97,233,246,0.2)",
    borderWidth: 1,
    justifyContent: "center"
  },
  orbHalo: {
    borderWidth: 1,
    height: "86%",
    position: "absolute",
    width: "86%"
  },
  orbCore: {
    borderRadius: 999,
    height: "44%",
    shadowOpacity: 0.9,
    shadowRadius: 12,
    width: "44%"
  },
  eyebrow: {
    ...logiNexus.typography.label,
    color: colors.accent,
    letterSpacing: 1.4
  },
  header: {
    alignItems: "center",
    flexDirection: "row",
    gap: logiNexus.spacing.lg,
    justifyContent: "space-between"
  },
  headerCompact: {
    alignItems: "stretch",
    flexDirection: "column"
  },
  headerCopy: {
    flex: 1,
    gap: 3,
    minWidth: 0
  },
  headerSide: {
    alignItems: "flex-end",
    gap: logiNexus.spacing.sm
  },
  headerSideCompact: {
    alignItems: "center",
    flexDirection: "row",
    flexWrap: "wrap",
    justifyContent: "space-between"
  },
  headerSubtitle: {
    ...logiNexus.typography.body,
    color: colors.muted
  },
  headerTitle: {
    ...logiNexus.typography.title,
    color: colors.text
  },
  metric: {
    backgroundColor: "rgba(255,255,255,0.035)",
    borderColor: colors.border,
    borderRadius: logiNexus.radius.medium,
    borderWidth: StyleSheet.hairlineWidth,
    flex: 1,
    minHeight: 54,
    padding: logiNexus.spacing.sm
  },
  metricLabel: {
    ...logiNexus.typography.metadata,
    color: colors.muted
  },
  metricValue: {
    ...logiNexus.typography.metric
  },
  panel: {
    backgroundColor: colors.glassStrong,
    borderRadius: logiNexus.radius.panel,
    borderWidth: 1,
    overflow: "hidden",
    padding: logiNexus.spacing.md
  },
  panelGlow: {
    height: 2,
    left: 0,
    opacity: 0.88,
    position: "absolute",
    right: 0,
    top: 0
  },
  search: {
    ...logiNexus.typography.body,
    color: colors.text,
    flex: 1,
    minHeight: 44,
    paddingRight: logiNexus.spacing.md
  },
  searchGlyph: {
    color: colors.accentStrong,
    fontSize: 20,
    fontWeight: "900",
    width: 28
  },
  searchWrap: {
    alignItems: "center",
    backgroundColor: colors.glass,
    borderColor: colors.border,
    borderRadius: logiNexus.radius.large,
    borderWidth: 1,
    flexDirection: "row",
    minHeight: 46,
    paddingLeft: logiNexus.spacing.md
  },
  segment: {
    alignItems: "center",
    borderColor: "transparent",
    borderRadius: logiNexus.radius.capsule,
    borderWidth: 1,
    flexDirection: "row",
    gap: 6,
    minHeight: 36,
    paddingHorizontal: logiNexus.spacing.md
  },
  segmentActive: {
    backgroundColor: colors.signalDim,
    borderColor: colors.accent
  },
  segmentCount: {
    color: colors.accent,
    fontSize: 11,
    fontWeight: "900"
  },
  segmentRail: {
    backgroundColor: colors.glass,
    borderColor: colors.border,
    borderRadius: logiNexus.radius.large,
    borderWidth: 1,
    flexGrow: 0
  },
  segmentRailContent: {
    flexDirection: "row",
    gap: logiNexus.spacing.sm,
    padding: 5
  },
  segmentText: {
    ...logiNexus.typography.button,
    color: colors.muted
  },
  segmentTextActive: {
    color: colors.text
  },
  statusDot: {
    borderRadius: 5,
    height: 10,
    width: 10
  },
  statusPill: {
    alignItems: "center",
    borderRadius: logiNexus.radius.capsule,
    borderWidth: 1,
    flexDirection: "row",
    gap: 6,
    minHeight: 30,
    paddingHorizontal: logiNexus.spacing.md
  },
  statusText: {
    ...logiNexus.typography.metadata
  }
}));
