import { BottomTabBarProps } from "@react-navigation/bottom-tabs";
import { Ionicons } from "@expo/vector-icons";
import * as Haptics from "expo-haptics";
import { useEffect, useRef, useState } from "react";
import { Animated, Pressable, StyleSheet, Text, useWindowDimensions, View } from "react-native";
import { useSafeAreaInsets } from "react-native-safe-area-context";

import { colors } from "../theme/colors";
import { logiNexus } from "../theme/logiNexus";
import { createThemedStyles } from "../theme/themedStyles";
import { useTheme } from "../theme/ThemeContext";
import { closeCreateConsole, useCreateConsoleOpen } from "./createConsoleStore";
import { spatialCreateEnabled } from "./flags";

/**
 * The Spatial Create Console (mission §17).
 *
 * A flag-gated overlay that replaces the Create button's composer jump: the
 * context dims (existing background color, opacity only), the bottom nav stays
 * visible above it (the + has morphed to ×), and a fanned, touch-only
 * horizontal carousel presents exactly six creation modes. Tilt never
 * interacts with this surface — `useTiltNavigation` suspends on the
 * `create-console` reason the moment the store opens.
 *
 * Go Live sits at the far end behind an explicit confirmation step and only
 * ever opens the existing Live setup route. Nothing here can start a
 * broadcast, publish, or send.
 */

export type CreateConsoleModeId = "photo" | "video" | "signal" | "camera" | "reel" | "live";

export interface CreateConsoleMode {
  id: CreateConsoleModeId;
  title: string;
  subtitle: string;
  icon: keyof typeof Ionicons.glyphMap;
  /** Go Live only: requires the confirmation step before navigating. */
  requiresConfirmation?: boolean;
}

/**
 * Exactly six modes, in mission order. Exported (with `buildModeNavigation`)
 * so tests can pin the order, the reel subtitle, and the Go Live confirmation
 * contract without rendering the carousel.
 */
export const CREATE_CONSOLE_MODES: readonly CreateConsoleMode[] = [
  { id: "photo", title: "Photo", subtitle: "Capture and share", icon: "image-outline" },
  { id: "video", title: "Video", subtitle: "Record for your feed", icon: "videocam-outline" },
  { id: "signal", title: "Create a Signal", subtitle: "Write a post", icon: "flash-outline" },
  { id: "camera", title: "Camera", subtitle: "Open the camera studio", icon: "camera-outline" },
  { id: "reel", title: "Create Reel", subtitle: "Record or upload clips", icon: "film-outline" },
  { id: "live", title: "Go Live", subtitle: "Confirmation required", icon: "radio-outline", requiresConfirmation: true }
];

export interface ModeNavigation {
  route: "Home" | "CameraStudio" | "LiveStudio";
  params: Record<string, unknown>;
}

/**
 * Every mode routes into an existing flow — the same params the legacy
 * entry points already send. Pure so the routing contract is unit-testable:
 * no mode may target a broadcast/publish route, and `live` may only open the
 * Live setup studio.
 */
export function buildModeNavigation(id: CreateConsoleModeId): ModeNavigation {
  switch (id) {
    case "photo":
      return {
        route: "CameraStudio",
        params: { target: "feed", mode: "photo", captureMode: "photo", returnToComposer: true, composerMode: "post", title: "Camera" }
      };
    case "video":
      return {
        route: "CameraStudio",
        params: { target: "feed", mode: "video", captureMode: "video", returnToComposer: true, composerMode: "post", title: "Video Camera" }
      };
    case "signal":
      return { route: "Home", params: { openComposer: true, composerMode: "post" } };
    case "camera":
      return { route: "CameraStudio", params: { target: "feed", mode: "photo", title: "Camera Studio" } };
    case "reel":
      return {
        route: "CameraStudio",
        params: { target: "reel", mode: "reel", captureMode: "video", returnToComposer: true, composerMode: "reel", title: "Reel Camera" }
      };
    case "live":
      // Setup only. This route hosts the existing pre-broadcast studio; going
      // live from there still requires the user's own explicit action.
      return { route: "LiveStudio", params: { title: "Live Studio" } };
  }
}

const CARD_WIDTH = 148;
const CARD_GAP = 14;
const SNAP = CARD_WIDTH + CARD_GAP;

export function SpatialCreateConsole({ navigation }: { navigation: BottomTabBarProps["navigation"] }) {
  const open = useCreateConsoleOpen();
  if (!open || !spatialCreateEnabled()) return null;
  return <ConsoleBody navigation={navigation} />;
}

function ConsoleBody({ navigation }: { navigation: BottomTabBarProps["navigation"] }) {
  const insets = useSafeAreaInsets();
  const { width } = useWindowDimensions();
  const { reduceMotion } = useTheme();
  const [liveArmed, setLiveArmed] = useState(false);

  const entrance = useRef(new Animated.Value(0)).current;
  const scrollX = useRef(new Animated.Value(0)).current;

  useEffect(() => {
    Animated.timing(entrance, {
      duration: reduceMotion ? 0 : 220,
      toValue: 1,
      useNativeDriver: true
    }).start();
  }, [entrance, reduceMotion]);

  const sidePadding = Math.max((width - CARD_WIDTH) / 2, CARD_GAP);

  const navigateToMode = (id: CreateConsoleModeId) => {
    const target = buildModeNavigation(id);
    closeCreateConsole();
    if (target.route === "Home") {
      (navigation as any).navigate("Home", target.params);
      return;
    }
    // Root-stack routes bubble up from the tab navigator to its parent stack.
    (navigation as any).getParent()?.navigate(target.route, target.params);
  };

  const selectMode = (mode: CreateConsoleMode) => {
    Haptics.selectionAsync().catch(() => undefined);
    if (mode.requiresConfirmation) {
      // Go Live never navigates on first tap — arm the confirmation step.
      setLiveArmed(true);
      return;
    }
    setLiveArmed(false);
    navigateToMode(mode.id);
  };

  return (
    <Animated.View style={[StyleSheet.absoluteFill, styles.root, { opacity: entrance }]} testID="spatial-create-console">
      {/* Dimmed context: the existing background color at partial opacity —
          depth via opacity only, no new colors (mission §22). */}
      <Pressable
        accessibilityRole="button"
        accessibilityLabel="Close create console"
        style={styles.backdrop}
        testID="spatial-create-console-backdrop"
        onPress={() => closeCreateConsole()}
      />

      <Animated.View
        pointerEvents="box-none"
        style={[
          styles.content,
          { paddingTop: Math.max(insets.top, 12) },
          {
            transform: [
              {
                translateY: entrance.interpolate({ inputRange: [0, 1], outputRange: [24, 0] })
              }
            ]
          }
        ]}
      >
        <Text style={styles.title}>Create</Text>
        <Text style={styles.hint}>Swipe the fan, tap a mode. Tap outside or × to close.</Text>

        <Animated.ScrollView
          horizontal
          contentContainerStyle={{ paddingHorizontal: sidePadding, alignItems: "center", gap: CARD_GAP }}
          decelerationRate="fast"
          showsHorizontalScrollIndicator={false}
          snapToInterval={SNAP}
          style={styles.carousel}
          onScroll={Animated.event([{ nativeEvent: { contentOffset: { x: scrollX } } }], { useNativeDriver: true })}
          scrollEventThrottle={16}
          testID="spatial-create-carousel"
        >
          {CREATE_CONSOLE_MODES.map((mode, index) => {
            const inputRange = [(index - 1) * SNAP, index * SNAP, (index + 1) * SNAP];
            const fan = reduceMotion
              ? undefined
              : {
                  opacity: scrollX.interpolate({ inputRange, outputRange: [0.55, 1, 0.55], extrapolate: "clamp" }),
                  transform: [
                    { translateY: scrollX.interpolate({ inputRange, outputRange: [18, 0, 18], extrapolate: "clamp" }) },
                    { scale: scrollX.interpolate({ inputRange, outputRange: [0.92, 1, 0.92], extrapolate: "clamp" }) },
                    {
                      rotate: scrollX.interpolate({
                        inputRange,
                        outputRange: ["7deg", "0deg", "-7deg"],
                        extrapolate: "clamp"
                      })
                    }
                  ]
                };
            const live = mode.id === "live";
            return (
              <Animated.View key={mode.id} style={fan}>
                <Pressable
                  accessibilityRole="button"
                  accessibilityLabel={live ? `${mode.title}. ${mode.subtitle}` : mode.title}
                  accessibilityHint={live ? "Opens a confirmation step. Never starts a broadcast directly." : undefined}
                  style={({ pressed }) => [
                    styles.card,
                    live && styles.cardLive,
                    live && liveArmed && styles.cardLiveArmed,
                    pressed && styles.cardPressed
                  ]}
                  testID={`spatial-create-mode-${mode.id}`}
                  onPress={() => selectMode(mode)}
                >
                  <View style={[styles.cardIconShell, live && styles.cardIconShellLive]}>
                    <Ionicons name={mode.icon} size={30} color={live ? colors.danger : colors.text} />
                  </View>
                  <Text style={[styles.cardTitle, live && styles.cardTitleLive]} numberOfLines={1}>
                    {mode.title}
                  </Text>
                  <Text style={[styles.cardSubtitle, live && styles.cardSubtitleLive]} numberOfLines={2}>
                    {mode.subtitle}
                  </Text>
                </Pressable>
              </Animated.View>
            );
          })}
        </Animated.ScrollView>

        {liveArmed ? (
          <View style={styles.liveConfirm} testID="spatial-create-live-confirm">
            <Text style={styles.liveConfirmTitle}>Go Live — confirmation required</Text>
            <Text style={styles.liveConfirmBody}>
              This opens the Live setup studio. You will not be live until you start the broadcast yourself.
            </Text>
            <View style={styles.liveConfirmRow}>
              <Pressable
                accessibilityRole="button"
                accessibilityLabel="Cancel Go Live"
                style={({ pressed }) => [styles.liveCancelButton, pressed && styles.cardPressed]}
                testID="spatial-create-live-cancel"
                onPress={() => setLiveArmed(false)}
              >
                <Text style={styles.liveCancelText}>Cancel</Text>
              </Pressable>
              <Pressable
                accessibilityRole="button"
                accessibilityLabel="Open Live setup"
                style={({ pressed }) => [styles.liveConfirmButton, pressed && styles.cardPressed]}
                testID="spatial-create-live-open-setup"
                onPress={() => {
                  Haptics.selectionAsync().catch(() => undefined);
                  setLiveArmed(false);
                  navigateToMode("live");
                }}
              >
                <Text style={styles.liveConfirmButtonText}>Open Live setup</Text>
              </Pressable>
            </View>
          </View>
        ) : null}
      </Animated.View>
    </Animated.View>
  );
}

const styles = createThemedStyles(() => ({
  backdrop: {
    ...StyleSheet.absoluteFillObject,
    backgroundColor: colors.background,
    opacity: 0.86
  },
  card: {
    backgroundColor: "rgba(7, 14, 32, 0.95)",
    borderColor: "rgba(77, 150, 255, 0.25)",
    borderRadius: 24,
    borderWidth: 1,
    gap: 8,
    minHeight: 190,
    padding: 16,
    width: CARD_WIDTH
  },
  cardIconShell: {
    alignItems: "center",
    backgroundColor: "rgba(18, 26, 61, 0.44)",
    borderColor: "rgba(100, 160, 255, 0.6)",
    borderRadius: logiNexus.radius.circular,
    borderWidth: 1,
    height: 56,
    justifyContent: "center",
    width: 56
  },
  cardIconShellLive: {
    borderColor: colors.danger
  },
  cardLive: {
    borderColor: colors.danger
  },
  cardLiveArmed: {
    backgroundColor: "rgba(18, 26, 61, 0.44)"
  },
  cardPressed: {
    opacity: 0.72,
    transform: [{ scale: 0.98 }]
  },
  cardSubtitle: {
    ...logiNexus.typography.metadata,
    color: colors.muted,
    fontSize: 12,
    lineHeight: 16
  },
  cardSubtitleLive: {
    color: colors.danger,
    fontWeight: "700"
  },
  cardTitle: {
    ...logiNexus.typography.label,
    color: colors.text,
    fontSize: 15,
    fontWeight: "800"
  },
  cardTitleLive: {
    color: colors.danger
  },
  carousel: {
    flexGrow: 0,
    marginTop: 22
  },
  content: {
    alignItems: "center",
    flex: 1,
    justifyContent: "center"
  },
  hint: {
    ...logiNexus.typography.metadata,
    color: colors.muted,
    marginTop: 6,
    textAlign: "center"
  },
  liveCancelButton: {
    alignItems: "center",
    backgroundColor: "rgba(255,255,255,0.045)",
    borderColor: "rgba(255,255,255,0.18)",
    borderRadius: 19,
    borderWidth: 1,
    minHeight: 44,
    justifyContent: "center",
    paddingHorizontal: 18
  },
  liveCancelText: {
    color: colors.text,
    fontSize: 14,
    fontWeight: "700"
  },
  liveConfirm: {
    backgroundColor: "rgba(7, 14, 32, 0.95)",
    borderColor: colors.danger,
    borderRadius: 24,
    borderWidth: 1,
    gap: 8,
    marginHorizontal: logiNexus.spacing.md,
    marginTop: 20,
    maxWidth: 420,
    padding: 16
  },
  liveConfirmBody: {
    ...logiNexus.typography.metadata,
    color: colors.muted,
    lineHeight: 18
  },
  liveConfirmButton: {
    alignItems: "center",
    backgroundColor: colors.danger,
    borderRadius: 19,
    flex: 1,
    minHeight: 44,
    justifyContent: "center",
    paddingHorizontal: 18
  },
  liveConfirmButtonText: {
    color: colors.text,
    fontSize: 14,
    fontWeight: "800"
  },
  liveConfirmRow: {
    flexDirection: "row",
    gap: 10,
    marginTop: 4
  },
  liveConfirmTitle: {
    color: colors.danger,
    fontSize: 15,
    fontWeight: "800"
  },
  root: {
    zIndex: 30
  },
  title: {
    ...logiNexus.typography.sectionTitle,
    color: colors.text,
    fontSize: 24
  }
}))
