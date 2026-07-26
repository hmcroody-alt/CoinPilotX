import { useEffect, useRef, useState } from "react";
import { AccessibilityInfo, Animated, Pressable, StyleSheet, Text, View } from "react-native";
import { Ionicons } from "@expo/vector-icons";
import * as Haptics from "expo-haptics";
import { DEFAULT_STATUS_REACTION, STATUS_REACTIONS, StatusReactionType } from "../api/status";
import { colors } from "../theme/colors";
import { useLogiNexusReducedMotion } from "../theme/logiNexusMotion";

type Props = {
  reactionCount: number;
  selectedReaction?: string;
  reactionPending?: boolean;
  shareBusy?: boolean;
  saved?: boolean;
  savePending?: boolean;
  onReact: (reactionType: StatusReactionType) => void;
  onReply: () => void;
  onShare: () => void;
  onSave: () => void;
};

/**
 * Icon-only Status action rail: React (heart, tap = Love, long-press = reaction
 * tray), Reply (message bubble), Share (paper plane). Replaces the previous
 * plain-text "React / Reply / Share" buttons. See mission report:
 * reports/pulsesoc_native_status_futuristic_icon_actions_2026-07-19.md
 */
export function StatusActionRail({ reactionCount, selectedReaction, reactionPending, shareBusy, saved, savePending, onReact, onReply, onShare, onSave }: Props) {
  const reduceMotion = useLogiNexusReducedMotion();
  const [trayOpen, setTrayOpen] = useState(false);
  const heartScale = useRef(new Animated.Value(1)).current;
  const countAnim = useRef(new Animated.Value(0)).current;
  const previousCount = useRef(reactionCount);

  useEffect(() => {
    if (reactionCount === previousCount.current) return;
    previousCount.current = reactionCount;
    if (reduceMotion) return;
    countAnim.setValue(1);
    Animated.timing(countAnim, { toValue: 0, duration: 220, useNativeDriver: true }).start();
  }, [reactionCount, reduceMotion, countAnim]);

  const selectedMeta = STATUS_REACTIONS.find((item) => item.type === selectedReaction);
  const heartIcon = (selectedMeta ? selectedMeta.iconFilled : "heart-outline") as never;
  const heartColor = selectedMeta ? selectedMeta.color : colors.text;

  function pulsePress() {
    if (reduceMotion) return;
    heartScale.setValue(0.92);
    Animated.sequence([
      Animated.timing(heartScale, { toValue: 1.06, duration: 90, useNativeDriver: true }),
      Animated.timing(heartScale, { toValue: 1, duration: 90, useNativeDriver: true })
    ]).start();
  }

  function handleReactPress() {
    pulsePress();
    Haptics.impactAsync(Haptics.ImpactFeedbackStyle.Light).catch(() => undefined);
    onReact(DEFAULT_STATUS_REACTION);
    AccessibilityInfo.announceForAccessibility?.(`${STATUS_REACTIONS[0].label} reaction selected`);
  }

  function handleLongPressReact() {
    Haptics.impactAsync(Haptics.ImpactFeedbackStyle.Medium).catch(() => undefined);
    setTrayOpen(true);
  }

  function selectTrayReaction(type: StatusReactionType, label: string) {
    setTrayOpen(false);
    pulsePress();
    Haptics.impactAsync(Haptics.ImpactFeedbackStyle.Light).catch(() => undefined);
    onReact(type);
    AccessibilityInfo.announceForAccessibility?.(`${label} reaction selected`);
  }

  return (
    <>
      <View style={styles.rail}>
        <View style={styles.item}>
          <Pressable
            testID="status-action-react"
            accessibilityRole="button"
            accessibilityLabel="React to Status"
            accessibilityHint="Applies a Love reaction. Double tap and hold for more reactions."
            accessibilityState={{ selected: Boolean(selectedMeta), busy: Boolean(reactionPending) }}
            style={styles.iconButton}
            onPress={handleReactPress}
            onLongPress={handleLongPressReact}
            delayLongPress={280}
          >
            <Animated.View style={{ transform: [{ scale: heartScale }] }}>
              <Ionicons name={heartIcon} size={24} color={heartColor} />
            </Animated.View>
          </Pressable>
          {reactionCount > 0 ? (
            <Animated.Text
              accessibilityLabel={`${reactionCount} reaction${reactionCount === 1 ? "" : "s"}`}
              style={[
                styles.count,
                {
                  opacity: countAnim.interpolate({ inputRange: [0, 1], outputRange: [1, 0.35] }),
                  transform: [{ translateY: countAnim.interpolate({ inputRange: [0, 1], outputRange: [0, -4] }) }]
                }
              ]}
            >
              {reactionCount}
            </Animated.Text>
          ) : null}
        </View>

        <Pressable accessibilityRole="button" accessibilityLabel="Reply to Status" style={[styles.item, styles.iconButton]} onPress={onReply}>
          <Ionicons name="chatbubble-outline" size={22} color={colors.text} />
        </Pressable>

        <Pressable
          accessibilityRole="button"
          accessibilityLabel="Share Status"
          accessibilityState={{ disabled: Boolean(shareBusy) }}
          style={[styles.item, styles.iconButton, shareBusy ? styles.disabled : undefined]}
          disabled={shareBusy}
          onPress={onShare}
        >
          <Ionicons name="paper-plane-outline" size={22} color={colors.text} />
        </Pressable>

        {/*
          Status was savable everywhere except here: the web viewer has had a
          Save control for as long as the generic saved library has existed, and
          the native viewer offered React, Reply and Share and nothing else. The
          same icon-button shape as its neighbours, so the rail is unchanged in
          everything but length.
        */}
        <Pressable
          testID="status-action-save"
          accessibilityRole="button"
          accessibilityLabel={saved ? "Remove Status from Saved" : "Save Status"}
          accessibilityHint={saved ? "Removes this Status from your Saved collection" : "Adds this Status to your Saved collection"}
          accessibilityState={{ selected: Boolean(saved), busy: Boolean(savePending), disabled: Boolean(savePending) }}
          style={[styles.item, styles.iconButton, savePending ? styles.disabled : undefined]}
          disabled={savePending}
          onPress={onSave}
        >
          <Ionicons name={saved ? "bookmark" : "bookmark-outline"} size={22} color={saved ? colors.accent : colors.text} />
        </Pressable>
      </View>

      {trayOpen ? (
        <>
          <Pressable accessibilityLabel="Close reaction options" style={styles.trayBackdrop} onPress={() => setTrayOpen(false)} />
          <View accessibilityRole="menu" accessibilityLabel="Open reaction options" style={styles.tray}>
            {STATUS_REACTIONS.map((reaction) => {
              const active = selectedReaction === reaction.type;
              return (
                <Pressable
                  key={reaction.type}
                  testID={`status-reaction-${reaction.type}`}
                  accessibilityRole="menuitem"
                  accessibilityLabel={`${reaction.label} reaction`}
                  accessibilityState={{ selected: active }}
                  style={[styles.trayItem, active ? styles.trayItemActive : undefined]}
                  onPress={() => selectTrayReaction(reaction.type, reaction.label)}
                >
                  <Ionicons name={(active ? reaction.iconFilled : reaction.icon) as never} size={20} color={active ? reaction.color : colors.text} />
                </Pressable>
              );
            })}
          </View>
        </>
      ) : null}
    </>
  );
}

const styles = StyleSheet.create({
  rail: {
    alignItems: "center",
    gap: 9,
    position: "absolute",
    right: 10,
    top: "38%",
    zIndex: 7
  },
  item: {
    alignItems: "center"
  },
  iconButton: {
    alignItems: "center",
    backgroundColor: "rgba(8, 15, 28, 0.62)",
    borderColor: "rgba(97, 234, 246, 0.22)",
    borderRadius: 16,
    borderWidth: 1,
    height: 48,
    justifyContent: "center",
    width: 48
  },
  count: {
    color: colors.muted,
    fontSize: 11,
    fontWeight: "800",
    marginTop: 4,
    textAlign: "center"
  },
  disabled: {
    opacity: 0.45
  },
  trayBackdrop: {
    ...StyleSheet.absoluteFillObject,
    zIndex: 8
  },
  tray: {
    alignItems: "center",
    backgroundColor: "rgba(8, 15, 28, 0.88)",
    borderColor: "rgba(97, 234, 246, 0.35)",
    borderRadius: 20,
    borderWidth: 1,
    gap: 8,
    padding: 8,
    position: "absolute",
    right: 66,
    top: "26%",
    zIndex: 9
  },
  trayItem: {
    alignItems: "center",
    borderRadius: 12,
    height: 40,
    justifyContent: "center",
    width: 40
  },
  trayItemActive: {
    backgroundColor: "rgba(255, 95, 168, 0.18)"
  }
});
