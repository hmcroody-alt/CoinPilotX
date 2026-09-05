/**
 * A module row inside a Business OS section landing page — the second layer.
 *
 * WHY THIS IS NOT `LaunchTile`
 *
 * `LaunchTile` is the first layer: a section tile that stays at full brightness
 * when locked, on the deliberate grounds that a *section* is not disabled, it is
 * early. That reasoning does not carry down here. By the time a user is inside a
 * section they have already been told the section is early; what they need now is
 * to tell, at a glance, which of the rows in front of them they can actually use.
 * So this row does what the brief asks for a locked module specifically —
 * reduced brightness, a grey surface, a lock icon and a worded badge — and the
 * tile keeps its own behaviour unchanged.
 *
 * Readiness is never carried by colour alone: the badge spells the state out and
 * the accessibility label says "Coming soon" outright, so the row still reads
 * correctly in greyscale and under a screen reader.
 *
 * The row is NOT `accessibilityState.disabled`. A locked row is still a live
 * control — tapping it is how the user gets the explanation — and marking it
 * disabled would tell assistive tech there is nothing to press.
 */

import { Pressable, StyleSheet, Text, View } from "react-native";
import { Ionicons } from "@expo/vector-icons";
import { colors } from "../theme/colors";
import { presenceTheme } from "../theme/presenceTheme";
import { createThemedStyles } from "../theme/themedStyles";
import { capabilityCopyState, type CapabilityAvailability } from "./sectionCapabilities";
import type { LaunchModuleId } from "./readiness";
import { useLaunchCopy } from "./useLaunchGate";

export function LaunchModuleRow({
  id,
  label,
  blurb,
  icon,
  availability,
  onPress
}: {
  id: LaunchModuleId;
  label: string;
  blurb: string;
  icon: string;
  /**
   * The verdict, passed in rather than derived from `id`.
   *
   * The row used to call `readinessOf(id)` itself, which made it a second
   * opinion on a question its parent had already answered — and the two differ
   * exactly where it matters, for a capability that is READY with nowhere to go.
   * That row drew a chevron and no badge while its tap did nothing. See
   * `sectionCapabilities.ts`.
   */
  availability: CapabilityAvailability;
  /** Always the gate, never a direct navigate. See `useLaunchGate`. */
  onPress: () => void;
}) {
  const state = capabilityCopyState(availability);
  const locked = state !== "READY";
  const { badge, accessibility } = useLaunchCopy();
  const a11y = accessibility(state, label, blurb);

  return (
    <Pressable
      accessibilityRole="button"
      accessibilityLabel={a11y.accessibilityLabel}
      accessibilityHint={a11y.accessibilityHint}
      testID={`launch-module-${id}`}
      onPress={onPress}
      style={[styles.row, locked ? styles.rowLocked : null]}
    >
      <View style={[styles.iconWrap, locked ? styles.iconWrapLocked : null]}>
        <Ionicons
          name={icon as never}
          size={18}
          color={locked ? colors.muted : colors.accent}
        />
      </View>

      <View style={styles.body}>
        <View style={styles.titleLine}>
          <Text style={[styles.label, locked ? styles.labelLocked : null]} numberOfLines={1}>
            {label}
          </Text>
          {/*
            The lock glyph is the brief's "subtle lock icon". It sits with the
            title rather than replacing the module's own icon, so the row still
            says what the module IS as well as that it is shut.
          */}
          {locked ? <Ionicons name="lock-closed" size={12} color={colors.muted} /> : null}
        </View>
        <Text style={[styles.blurb, locked ? styles.blurbLocked : null]} numberOfLines={2}>
          {blurb}
        </Text>
        {locked ? (
          <View style={styles.badge}>
            <Text style={styles.badgeText}>{badge(state)}</Text>
          </View>
        ) : null}
      </View>

      {/*
        A chevron is a promise of a destination, so only a module that has one
        gets it. A locked row would otherwise look identical to a working one at
        the right-hand edge, which is exactly the "looks tappable, lands
        nowhere" failure this layer exists to remove.
      */}
      {locked ? null : <Ionicons name="chevron-forward" size={18} color={colors.muted} />}
    </Pressable>
  );
}

const styles = createThemedStyles(() => ({
  badge: {
    alignSelf: "flex-start",
    backgroundColor: presenceTheme.tealSoft,
    borderColor: presenceTheme.tealBorder,
    borderRadius: 999,
    borderWidth: StyleSheet.hairlineWidth,
    marginTop: 4,
    paddingHorizontal: 8,
    paddingVertical: 3
  },
  badgeText: {
    color: presenceTheme.teal,
    fontSize: 10,
    fontWeight: "900",
    letterSpacing: 0.6
  },
  blurb: {
    color: colors.muted,
    fontSize: 12,
    lineHeight: 17
  },
  /** The "reduced brightness" the brief asks for, applied to text as well. */
  blurbLocked: {
    opacity: 0.7
  },
  body: {
    flex: 1,
    gap: 2
  },
  iconWrap: {
    alignItems: "center",
    backgroundColor: colors.surface,
    borderRadius: 10,
    height: 36,
    justifyContent: "center",
    width: 36
  },
  iconWrapLocked: {
    opacity: 0.6
  },
  label: {
    color: colors.text,
    flexShrink: 1,
    fontSize: 15,
    fontWeight: "700"
  },
  labelLocked: {
    color: colors.muted
  },
  row: {
    alignItems: "center",
    backgroundColor: colors.surfaceRaised,
    borderColor: colors.border,
    borderRadius: 12,
    borderWidth: StyleSheet.hairlineWidth,
    flexDirection: "row",
    gap: 12,
    // 44pt is the smallest comfortable touch target; the row is taller than
    // that in practice but the floor matters at the smallest font scale.
    minHeight: 44,
    padding: 12
  },
  /**
   * The grey, dimmed surface. `opacity` on the row itself would fade the badge
   * and the lock glyph too — the two things that have to stay legible — so the
   * dimming is applied per element instead.
   */
  rowLocked: {
    backgroundColor: colors.surface,
    borderColor: colors.border
  },
  titleLine: {
    alignItems: "center",
    flexDirection: "row",
    gap: 6
  }
}));
