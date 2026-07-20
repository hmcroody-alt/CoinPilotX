import { Ionicons } from "@expo/vector-icons";
import * as Haptics from "expo-haptics";
import { forwardRef, ReactNode, useEffect, useRef, useState } from "react";
import {
  ActivityIndicator,
  Animated,
  GestureResponderEvent,
  Modal,
  Pressable,
  StyleSheet,
  Text,
  View,
  useWindowDimensions
} from "react-native";
import { useSafeAreaInsets } from "react-native-safe-area-context";
import { colors } from "../theme/colors";
import { useLogiNexusReducedMotion } from "../theme/logiNexusMotion";

export type StatusReactionType = "like" | "love" | "fire" | "funny" | "wow" | "rocket";

type Props = {
  reactionCount: number;
  selectedReaction?: string | null;
  reactionPending?: boolean;
  replyPending?: boolean;
  sharePending?: boolean;
  onReact: (reactionType: StatusReactionType) => void;
  onReply: () => void;
  onShare: () => void;
};

const REACTIONS: ReadonlyArray<{ key: StatusReactionType; label: string; icon: keyof typeof Ionicons.glyphMap }> = [
  { key: "love", label: "Love", icon: "heart" },
  { key: "like", label: "Like", icon: "thumbs-up" },
  { key: "fire", label: "Fire", icon: "flame" },
  { key: "funny", label: "Funny", icon: "happy" },
  { key: "wow", label: "Wow", icon: "sparkles" },
  { key: "rocket", label: "Rocket", icon: "rocket" }
];

export function StatusActionRail({
  reactionCount,
  selectedReaction,
  reactionPending,
  replyPending,
  sharePending,
  onReact,
  onReply,
  onShare
}: Props) {
  const insets = useSafeAreaInsets();
  const { height } = useWindowDimensions();
  const reducedMotion = useLogiNexusReducedMotion();
  const reactionButtonRef = useRef<View>(null);
  const [trayOpen, setTrayOpen] = useState(false);
  const [trayTop, setTrayTop] = useState(Math.round(height * 0.36));

  function openReactionTray(event: GestureResponderEvent) {
    event.stopPropagation();
    Haptics.selectionAsync().catch(() => undefined);
    reactionButtonRef.current?.measureInWindow((_x, y, _width, measuredHeight) => {
      const desiredTop = y + measuredHeight / 2 - 28;
      setTrayTop(Math.max(insets.top + 72, Math.min(desiredTop, height - insets.bottom - 118)));
    });
    setTrayOpen(true);
  }

  return (
    <>
      <View
        testID="status-action-rail"
        accessibilityRole="toolbar"
        accessibilityLabel="Status actions"
        pointerEvents="box-none"
        style={[styles.rail, { right: Math.max(10, insets.right + 8), bottom: Math.max(122, insets.bottom + 104) }]}
      >
        <ActionControl
          ref={reactionButtonRef}
          testID="status-action-react"
          icon={selectedReaction ? "heart" : "heart-outline"}
          accessibilityLabel={`${selectedReaction ? "React to Status, selected" : "React to Status"}. ${reactionCount} ${reactionCount === 1 ? "reaction" : "reactions"}. Long press to open reaction options.`}
          accessibilityHint="Tap to apply the default Love reaction. Long press to choose another reaction."
          selected={Boolean(selectedReaction)}
          pending={reactionPending}
          reducedMotion={reducedMotion}
          onPress={() => onReact("love")}
          onLongPress={openReactionTray}
        >
          <AnimatedCount value={reactionCount} reducedMotion={reducedMotion} />
        </ActionControl>

        <ActionControl
          testID="status-action-reply"
          icon="chatbubble-ellipses-outline"
          accessibilityLabel="Reply to Status"
          accessibilityHint="Opens the Status reply composer and focuses the input."
          pending={replyPending}
          reducedMotion={reducedMotion}
          tone="violet"
          onPress={onReply}
        />

        <ActionControl
          testID="status-action-share"
          icon="paper-plane-outline"
          accessibilityLabel="Share Status"
          accessibilityHint="Opens the existing Status share flow."
          pending={sharePending}
          reducedMotion={reducedMotion}
          tone="cyan"
          onPress={onShare}
        />
      </View>

      {trayOpen ? (
        <Modal transparent visible animationType="none" onRequestClose={() => setTrayOpen(false)}>
          <Pressable testID="status-reaction-tray-backdrop" style={styles.trayBackdrop} onPress={() => setTrayOpen(false)}>
            <Animated.View
              testID="status-reaction-tray"
              accessibilityRole="toolbar"
              accessibilityLabel="Open reaction options"
              style={[styles.tray, { right: Math.max(68, insets.right + 64), top: trayTop }]}
            >
              {REACTIONS.map((reaction) => (
                <Pressable
                  key={reaction.key}
                  testID={`status-reaction-${reaction.key}`}
                  accessibilityRole="button"
                  accessibilityLabel={`${reaction.label} reaction`}
                  accessibilityState={{ selected: selectedReaction === reaction.key }}
                  style={({ pressed }) => [
                    styles.reactionChoice,
                    selectedReaction === reaction.key && styles.reactionChoiceSelected,
                    pressed && styles.reactionChoicePressed
                  ]}
                  onPress={(event) => {
                    event?.stopPropagation?.();
                    setTrayOpen(false);
                    Haptics.selectionAsync().catch(() => undefined);
                    onReact(reaction.key);
                  }}
                >
                  <Ionicons name={reaction.icon} size={21} color={selectedReaction === reaction.key ? "#03120f" : colors.text} />
                </Pressable>
              ))}
            </Animated.View>
          </Pressable>
        </Modal>
      ) : null}
    </>
  );
}

type ActionControlProps = {
  testID: string;
  icon: keyof typeof Ionicons.glyphMap;
  accessibilityLabel: string;
  accessibilityHint: string;
  selected?: boolean;
  pending?: boolean;
  reducedMotion: boolean;
  tone?: "mint" | "violet" | "cyan";
  children?: ReactNode;
  onPress: () => void;
  onLongPress?: (event: GestureResponderEvent) => void;
};

const ActionControl = forwardRef<View, ActionControlProps>(({
  testID,
  icon,
  accessibilityLabel,
  accessibilityHint,
  selected,
  pending,
  reducedMotion,
  tone = "mint",
  children,
  onPress,
  onLongPress
}, ref) => {
  const scale = useRef(new Animated.Value(1)).current;
  const bloom = useRef(new Animated.Value(0)).current;
  const longPressTriggered = useRef(false);
  const accent = tone === "violet" ? "#aa86ff" : tone === "cyan" ? "#62dcff" : colors.accent;

  function animatePress() {
    if (reducedMotion) return;
    Animated.sequence([
      Animated.timing(scale, { toValue: 0.92, duration: 55, useNativeDriver: true }),
      Animated.timing(scale, { toValue: 1.06, duration: 75, useNativeDriver: true }),
      Animated.timing(scale, { toValue: 1, duration: 55, useNativeDriver: true })
    ]).start();
    if (selected || testID === "status-action-react") {
      bloom.setValue(0.52);
      Animated.timing(bloom, { toValue: 0, duration: 210, useNativeDriver: true }).start();
    }
  }

  return (
    <Animated.View style={{ transform: [{ scale }] }}>
      <Pressable
        ref={ref}
        testID={testID}
        accessibilityRole="button"
        accessibilityLabel={accessibilityLabel}
        accessibilityHint={accessibilityHint}
        accessibilityState={{ selected: Boolean(selected), disabled: Boolean(pending), busy: Boolean(pending) }}
        disabled={pending}
        delayLongPress={360}
        style={({ pressed }) => [styles.control, { borderColor: `${accent}${selected ? "D9" : "70"}` }, selected && styles.controlSelected, pressed && styles.controlPressed]}
        onLongPress={onLongPress ? (event) => {
          longPressTriggered.current = true;
          onLongPress(event);
        } : undefined}
        onPress={(event) => {
          event?.stopPropagation?.();
          if (longPressTriggered.current) {
            longPressTriggered.current = false;
            return;
          }
          animatePress();
          if (testID === "status-action-react") Haptics.impactAsync(Haptics.ImpactFeedbackStyle.Light).catch(() => undefined);
          onPress();
        }}
      >
        <Animated.View pointerEvents="none" style={[styles.bloom, { backgroundColor: accent, opacity: bloom }]} />
        {pending ? <ActivityIndicator testID={`${testID}-pending`} size="small" color={accent} /> : <Ionicons testID={`${testID}-icon`} name={icon} size={24} color={selected ? colors.accent : colors.text} />}
        {children}
      </Pressable>
    </Animated.View>
  );
});

ActionControl.displayName = "StatusActionControl";

function AnimatedCount({ value, reducedMotion }: { value: number; reducedMotion: boolean }) {
  const opacity = useRef(new Animated.Value(1)).current;
  const translateY = useRef(new Animated.Value(0)).current;
  const previous = useRef(value);

  useEffect(() => {
    if (previous.current === value) return;
    previous.current = value;
    if (reducedMotion) return;
    opacity.setValue(0);
    translateY.setValue(4);
    Animated.parallel([
      Animated.timing(opacity, { toValue: 1, duration: 150, useNativeDriver: true }),
      Animated.timing(translateY, { toValue: 0, duration: 150, useNativeDriver: true })
    ]).start();
  }, [opacity, reducedMotion, translateY, value]);

  return (
    <Animated.Text
      testID="status-action-reaction-count"
      accessibilityLabel={`${value} ${value === 1 ? "reaction" : "reactions"}`}
      style={[styles.count, { opacity, transform: [{ translateY }] }]}
    >
      {value}
    </Animated.Text>
  );
}

const styles = StyleSheet.create({
  bloom: {
    borderRadius: 28,
    height: 54,
    position: "absolute",
    width: 54
  },
  control: {
    alignItems: "center",
    backgroundColor: "rgba(3, 10, 20, 0.74)",
    borderRadius: 18,
    borderWidth: 1,
    height: 56,
    justifyContent: "center",
    overflow: "hidden",
    width: 56
  },
  controlPressed: {
    backgroundColor: "rgba(11, 30, 42, 0.9)"
  },
  controlSelected: {
    backgroundColor: "rgba(11, 46, 46, 0.82)",
    shadowColor: colors.accent,
    shadowOffset: { width: 0, height: 0 },
    shadowOpacity: 0.28,
    shadowRadius: 9
  },
  count: {
    color: colors.text,
    fontSize: 10,
    fontVariant: ["tabular-nums"],
    fontWeight: "900",
    lineHeight: 12,
    marginTop: 1
  },
  rail: {
    gap: 10,
    position: "absolute",
    zIndex: 9
  },
  reactionChoice: {
    alignItems: "center",
    borderColor: "rgba(117, 234, 255, 0.24)",
    borderRadius: 17,
    borderWidth: 1,
    height: 44,
    justifyContent: "center",
    width: 44
  },
  reactionChoicePressed: {
    opacity: 0.72,
    transform: [{ scale: 0.94 }]
  },
  reactionChoiceSelected: {
    backgroundColor: colors.accent,
    borderColor: colors.accent
  },
  tray: {
    backgroundColor: "rgba(3, 10, 20, 0.94)",
    borderColor: "rgba(100, 228, 255, 0.52)",
    borderRadius: 24,
    borderWidth: 1,
    flexDirection: "row",
    flexWrap: "wrap",
    gap: 6,
    padding: 8,
    position: "absolute",
    shadowColor: "#55ddff",
    shadowOffset: { width: 0, height: 0 },
    shadowOpacity: 0.24,
    shadowRadius: 14,
    width: 164
  },
  trayBackdrop: {
    ...StyleSheet.absoluteFillObject,
    backgroundColor: "rgba(0, 0, 0, 0.08)"
  }
});
