/**
 * The pause / resume control on a campaign or promotion.
 *
 * It is a real switch — `accessibilityRole="switch"` with a `checked` state —
 * not a coloured button, so a screen reader announces "Delivering, switch, on"
 * and toggling it says "off". The thumb travels on toggle for a physical feel,
 * but nothing rides on the motion: the state is in the role, the label, and the
 * text beside it. Under reduce-motion the thumb jumps.
 *
 * `on` means "delivering". Turning it off pauses; turning it on resumes. The
 * parent owns the backend call and passes `busy` while it is in flight, during
 * which the switch is disabled so it cannot be double-toggled.
 */

import { Animated, Pressable, StyleSheet, Text, View } from "react-native";
import { adsLight } from "../../theme/adsLight";
import { useAdsSwitchThumb } from "../../theme/adsMotion";

export type PauseSwitchProps = {
  /** True = delivering (switch on). False = paused (switch off). */
  on: boolean;
  onToggle: (next: boolean) => void;
  reducedMotion: boolean;
  busy?: boolean;
  disabled?: boolean;
  /** Word shown beside the switch, e.g. "Delivering" / "Paused". */
  label: string;
};

const TRACK_WIDTH = 46;
const THUMB = 22;
const TRAVEL = TRACK_WIDTH - THUMB - 4;

export function PauseSwitch({
  on,
  onToggle,
  reducedMotion,
  busy = false,
  disabled = false,
  label
}: PauseSwitchProps) {
  const thumb = useAdsSwitchThumb(reducedMotion, on);
  const isDisabled = disabled || busy;

  return (
    <Pressable
      style={styles.row}
      onPress={() => !isDisabled && onToggle(!on)}
      disabled={isDisabled}
      accessibilityRole="switch"
      accessibilityState={{ checked: on, disabled: isDisabled, busy }}
      accessibilityLabel={label}
      hitSlop={8}
    >
      <View
        style={[
          styles.track,
          { backgroundColor: on ? adsLight.status.success : adsLight.border.secondaryButton },
          isDisabled ? styles.trackDisabled : null
        ]}
      >
        <Animated.View
          style={[
            styles.thumb,
            {
              transform: [
                {
                  translateX: thumb.interpolate({
                    inputRange: [0, 1],
                    outputRange: [0, TRAVEL]
                  })
                }
              ]
            }
          ]}
        />
      </View>
      <Text style={[styles.label, { color: on ? adsLight.status.success : adsLight.text.muted }]}>
        {busy ? "Working…" : label}
      </Text>
    </Pressable>
  );
}

const styles = StyleSheet.create({
  row: {
    flexDirection: "row",
    alignItems: "center",
    gap: 8,
    minHeight: adsLight.size.tapTarget
  },
  track: {
    width: TRACK_WIDTH,
    height: THUMB + 4,
    borderRadius: adsLight.radius.pill,
    padding: 2,
    justifyContent: "center"
  },
  trackDisabled: { opacity: 0.5 },
  thumb: {
    width: THUMB,
    height: THUMB,
    borderRadius: THUMB / 2,
    backgroundColor: "#FFFFFF"
  },
  label: { fontSize: 13, fontWeight: "700" }
});
