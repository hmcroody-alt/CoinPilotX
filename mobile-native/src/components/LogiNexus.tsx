import { ReactNode } from "react";
import { Pressable, StyleProp, StyleSheet, Text, View, ViewStyle } from "react-native";
import { colors } from "../theme/colors";
import { logiNexus, LogiNexusTone, toneColor } from "../theme/logiNexus";

type SurfaceProps = {
  children: ReactNode;
  style?: StyleProp<ViewStyle>;
  tone?: LogiNexusTone;
};

export function LogiNexusPanel({ children, style, tone = "default" }: SurfaceProps) {
  const color = toneColor(tone);
  return (
    <View style={[styles.panel, { borderColor: color }, style]}>
      <View pointerEvents="none" style={[styles.panelSignal, { backgroundColor: color }]} />
      {children}
    </View>
  );
}

export function LogiNexusCard({ children, style, tone = "default" }: SurfaceProps) {
  const color = toneColor(tone);
  return (
    <View style={[styles.card, { borderColor: `${color}66` }, style]}>
      {children}
    </View>
  );
}

export function LogiNexusBadge({ label, tone = "default" }: { label: string; tone?: LogiNexusTone }) {
  const color = toneColor(tone);
  return (
    <View style={[styles.badge, { borderColor: color, backgroundColor: `${color}1f` }]}>
      <Text style={[styles.badgeText, { color }]} numberOfLines={1}>
        {label}
      </Text>
    </View>
  );
}

export function LogiNexusMetric({ value, label, tone = "default" }: { value: string | number; label: string; tone?: LogiNexusTone }) {
  const color = toneColor(tone);
  return (
    <View style={styles.metric}>
      <Text style={[styles.metricValue, { color }]}>{value}</Text>
      <Text style={styles.metricLabel}>{label}</Text>
    </View>
  );
}

export function LogiNexusButton({
  label,
  onPress,
  tone = "default",
  disabled,
  testID,
  accessibilityLabel,
  variant = "solid"
}: {
  label: string;
  onPress: () => void;
  tone?: LogiNexusTone;
  disabled?: boolean;
  testID?: string;
  accessibilityLabel?: string;
  variant?: "solid" | "outline";
}) {
  const color = toneColor(tone);
  const solid = variant === "solid";
  return (
    <Pressable
      accessibilityRole="button"
      accessibilityLabel={accessibilityLabel || label}
      disabled={disabled}
      testID={testID}
      style={({ pressed }) => [
        styles.button,
        {
          backgroundColor: solid ? color : "transparent",
          borderColor: color,
          opacity: disabled ? 0.56 : pressed ? 0.76 : 1
        }
      ]}
      onPress={onPress}
    >
      <Text style={[styles.buttonText, { color: solid ? colors.background : color }]}>{label}</Text>
    </Pressable>
  );
}

export function LogiNexusEmptyState({ title, body, tone = "default" }: { title: string; body: string; tone?: LogiNexusTone }) {
  return (
    <LogiNexusCard tone={tone} style={styles.empty}>
      <LogiNexusBadge label="quiet sector" tone={tone} />
      <Text style={styles.emptyTitle}>{title}</Text>
      <Text style={styles.emptyBody}>{body}</Text>
    </LogiNexusCard>
  );
}

export function LogiNexusSignalIndicator({ active = true, tone = "default" }: { active?: boolean; tone?: LogiNexusTone }) {
  const color = active ? toneColor(tone) : colors.disabled;
  return (
    <View style={[styles.signalOuter, { borderColor: color }]}>
      <View style={[styles.signalInner, { backgroundColor: color }]} />
    </View>
  );
}

const styles = StyleSheet.create({
  badge: {
    alignSelf: "flex-start",
    borderRadius: logiNexus.radius.capsule,
    borderWidth: 1,
    maxWidth: "100%",
    paddingHorizontal: logiNexus.spacing.md,
    paddingVertical: 6
  },
  badgeText: {
    ...logiNexus.typography.label,
    letterSpacing: 1,
    textTransform: "uppercase"
  },
  button: {
    alignItems: "center",
    borderRadius: logiNexus.radius.medium,
    borderWidth: 1,
    justifyContent: "center",
    minHeight: 44,
    paddingHorizontal: logiNexus.spacing.lg
  },
  buttonText: {
    ...logiNexus.typography.button
  },
  card: {
    backgroundColor: colors.glass,
    borderRadius: logiNexus.radius.large,
    borderWidth: StyleSheet.hairlineWidth,
    overflow: "hidden",
    padding: logiNexus.spacing.lg
  },
  empty: {
    gap: logiNexus.spacing.sm
  },
  emptyBody: {
    ...logiNexus.typography.body,
    color: colors.muted
  },
  emptyTitle: {
    ...logiNexus.typography.sectionTitle,
    color: colors.text
  },
  metric: {
    alignItems: "center",
    backgroundColor: "rgba(255,255,255,0.035)",
    borderColor: colors.border,
    borderRadius: logiNexus.radius.medium,
    borderWidth: 1,
    flex: 1,
    justifyContent: "center",
    minHeight: 66,
    paddingHorizontal: logiNexus.spacing.sm
  },
  metricLabel: {
    ...logiNexus.typography.metadata,
    color: colors.muted,
    marginTop: 2,
    textAlign: "center"
  },
  metricValue: {
    ...logiNexus.typography.metric
  },
  panel: {
    backgroundColor: colors.glassStrong,
    borderRadius: logiNexus.radius.panel,
    borderWidth: 1,
    overflow: "hidden",
    padding: logiNexus.spacing.lg
  },
  panelSignal: {
    height: 2,
    left: 0,
    opacity: 0.9,
    position: "absolute",
    right: 0,
    top: 0
  },
  signalInner: {
    borderRadius: 5,
    height: 10,
    width: 10
  },
  signalOuter: {
    alignItems: "center",
    borderRadius: 11,
    borderWidth: 1,
    height: 22,
    justifyContent: "center",
    width: 22
  }
});
