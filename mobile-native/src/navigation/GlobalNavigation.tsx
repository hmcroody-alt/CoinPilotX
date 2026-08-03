import { BottomTabBarProps } from "@react-navigation/bottom-tabs";
import { NavigationProp, ParamListBase } from "@react-navigation/native";
import { Ionicons } from "@expo/vector-icons";
import * as Haptics from "expo-haptics";
import { useEffect, useRef, useState } from "react";
import { AccessibilityInfo, Animated, Image, LayoutChangeEvent, Pressable, StyleSheet, Text, View } from "react-native";
import { useSafeAreaInsets } from "react-native-safe-area-context";
import { LogiNexusBadge, LogiNexusSignalIndicator } from "../components/LogiNexus";
import { getPulseRadioState, playNextTrack, PulseRadioState, subscribePulseRadio, togglePulseRadio } from "../core/pulseRadio";
// The scope wording lives with the counts, in the unread store — not here, where
// it could drift from the number it describes.
import { badgeSpokenLabel, scopedBadgesEnabled } from "../core/unreadCounts";
import { colors } from "../theme/colors";
import { logiNexus } from "../theme/logiNexus";
import { useBottomNavVisibility } from "./BottomNavVisibility";
import {
  BOTTOM_NAV_CREATE_MARGIN_TOP,
  BOTTOM_NAV_CREATE_MIN_HEIGHT,
  BOTTOM_NAV_DOCK_PADDING_TOP,
  BOTTOM_NAV_DOCK_PANEL_MIN_HEIGHT,
  BOTTOM_NAV_DOCK_PANEL_PADDING
} from "./bottomNavMetrics";
import { resolveBottomNavPolicy } from "./bottomNavPolicy";
import {
  cancelRefreshTapWindow,
  RefreshDestination,
  resolveNavigationTap,
  scrollRefreshDestinationToTop,
  triggerRefreshDestination
} from "./refreshCoordinator";
import { AppTabParamList } from "./types";

export type GlobalNavigationBadges = {
  activity?: number;
  messages?: number;
  alerts?: number;
};

export type GlobalNavigationIdentity = {
  displayName?: string;
  username?: string;
  avatarUrl?: string;
  verified?: boolean;
  premium?: boolean;
  attention?: boolean;
};

type HeaderProps = {
  title: string;
  subtitle?: string;
  mode?: "home" | "standard" | "intelligence";
  canGoBack?: boolean;
  showDrawer?: boolean;
  onBack?: () => void;
  onOpenDrawer?: () => void;
  onOpenSearch?: () => void;
  onOpenActivity?: () => void;
  onOpenMessages?: () => void;
  onOpenProfile?: () => void;
  badges?: GlobalNavigationBadges;
  identity?: GlobalNavigationIdentity;
  testID?: string;
};

const PRIMARY_TABS: Array<{
  name: keyof AppTabParamList;
  label: string;
  routeName: keyof AppTabParamList;
  icon: keyof typeof Ionicons.glyphMap;
  accessibilityLabel: string;
  refreshDestination: RefreshDestination | null;
}> = [
  { name: "Home", routeName: "Home", label: "Home", icon: "home-outline", accessibilityLabel: "Open Home", refreshDestination: "home" },
  { name: "Reels", routeName: "Reels", label: "Reels", icon: "play-circle-outline", accessibilityLabel: "Open Reels", refreshDestination: "reels" },
  { name: "Create", routeName: "Create", label: "Create", icon: "add", accessibilityLabel: "Open Create", refreshDestination: null },
  { name: "Messenger", routeName: "Messenger", label: "Messages", icon: "chatbubble-ellipses-outline", accessibilityLabel: "Open Messages", refreshDestination: "social-messages" },
  { name: "Profile", routeName: "Profile", label: "Profile", icon: "person-circle-outline", accessibilityLabel: "Open Profile", refreshDestination: "profile" }
];

export function LogiNexusGlobalHeader({
  title,
  subtitle,
  mode = "standard",
  canGoBack,
  showDrawer = true,
  onBack,
  onOpenDrawer,
  onOpenSearch,
  onOpenActivity,
  onOpenMessages,
  onOpenProfile,
  badges,
  identity,
  testID = "global-command-strip"
}: HeaderProps) {
  const insets = useSafeAreaInsets();
  const activityCount = normalizeBadgeCount(badges?.activity);
  const messageCount = normalizeBadgeCount(badges?.messages);
  const initials = initialsFor(identity?.displayName || identity?.username || "PulseSoc");
  const intelligenceMode = mode === "intelligence";
  const homeMode = mode === "home";

  return (
    <View style={[styles.headerShell, homeMode && styles.headerShellHome, { paddingTop: Math.max(insets.top, 10) }]} testID={testID}>
      <View style={styles.headerRow}>
        {canGoBack ? (
          <IconButton label="Back" icon="chevron-back" home={homeMode} testID="global-header-back" onPress={onBack} />
        ) : showDrawer ? (
          <IconButton label="Open PulseSoc navigation drawer" icon="menu" home={homeMode} testID="global-header-drawer" onPress={onOpenDrawer} />
        ) : (
          <View style={[styles.iconButtonSpacer, homeMode && styles.iconButtonSpacerHome]} />
        )}

        <View style={[styles.titleBlock, homeMode && styles.titleBlockHome]}>
          <View style={styles.brandRow}>
            {homeMode ? null : <LogiNexusSignalIndicator active tone={intelligenceMode ? "intelligence" : "default"} />}
            {homeMode && title === "PulseSoc" ? (
              <Text style={[styles.headerTitle, styles.headerTitleHome]} numberOfLines={1}>
                Pulse<Text style={styles.headerTitleHomeAccent}>Soc</Text>
              </Text>
            ) : (
              <Text style={[styles.headerTitle, homeMode && styles.headerTitleHome]} numberOfLines={1}>
                {title}
              </Text>
            )}
          </View>
          {homeMode ? (
            <View pointerEvents="none" style={styles.homeBrandSignal}>
              <View style={styles.homeBrandSignalPrimary} />
              <Text style={styles.homeBrandPulse}>⌁</Text>
              <View style={styles.homeBrandSignalSecondary} />
            </View>
          ) : null}
          {subtitle ? (
            <Text style={[styles.headerSubtitle, homeMode && styles.headerSubtitleHome]} numberOfLines={1}>
              {subtitle}
            </Text>
          ) : null}
        </View>

        <View style={styles.headerActions}>
          {onOpenSearch ? <IconButton label="Search PulseSoc" icon="search" home={homeMode} testID="global-header-search" onPress={onOpenSearch} /> : null}
          {/* Each badge names its own scope. A bare number beside an icon is
              ambiguous sighted and meaningless spoken, and it was the reason
              nobody noticed the bell was counting messages too. The scope text
              comes from the unread store, next to the number's definition, so a
              label cannot drift from the count it labels. */}
          {onOpenMessages ? (
            <IconButton
              label="Open Messages"
              scopeLabel={badgeSpokenLabel("messages", messageCount)}
              icon="chatbubble-ellipses-outline"
              home={homeMode}
              badge={messageCount}
              testID="global-header-messages"
              onPress={onOpenMessages}
            />
          ) : null}
          {onOpenActivity ? (
            <IconButton
              label="Open Activity Inbox"
              scopeLabel={badgeSpokenLabel(
                scopedBadgesEnabled() ? "notifications" : "combined",
                activityCount
              )}
              icon="notifications-outline"
              home={homeMode}
              badge={activityCount}
              testID="global-header-activity"
              onPress={onOpenActivity}
            />
          ) : null}
          {onOpenProfile ? (
            <Pressable
              accessibilityRole="button"
              accessibilityLabel="Open Profile"
              testID="global-header-profile"
              style={({ pressed }) => [styles.avatarButton, homeMode && styles.avatarButtonHome, pressed && styles.pressed]}
              onPress={onOpenProfile}
            >
              {identity?.avatarUrl ? <Image source={{ uri: identity.avatarUrl }} style={styles.avatarImage} /> : <Text style={styles.avatarText}>{initials}</Text>}
              {identity?.verified || identity?.premium ? <View style={styles.avatarSignal} /> : null}
            </Pressable>
          ) : null}
        </View>
      </View>
      {!homeMode ? <View style={styles.headerMetaRow}>
        <LogiNexusBadge label={intelligenceMode ? "UNDX" : "PulseSoc"} tone={intelligenceMode ? "intelligence" : "default"} />
        {identity?.attention ? <LogiNexusBadge label="attention" tone="warning" /> : null}
        {/* "3 alerts" named a fourth thing that matched none of the three
            numbers above it. It is the notification count, so it says so. */}
        {badges?.alerts ? (
          <LogiNexusBadge
            label={
              scopedBadgesEnabled()
                ? badgeSpokenLabel("notifications", normalizeBadgeCount(badges.alerts))
                : `${formatBadge(badges.alerts)} alerts`
            }
            tone="intelligence"
          />
        ) : null}
      </View> : null}
    </View>
  );
}

export function LogiNexusBottomNavigation({ state, descriptors, navigation, badges }: BottomTabBarProps & { badges?: GlobalNavigationBadges }) {
  const insets = useSafeAreaInsets();
  const activeRoute = state.routes[state.index]?.name as keyof AppTabParamList | undefined;
  const { hidden: requestedHidden, showBottomNav } = useBottomNavVisibility();
  const hidden = resolveBottomNavPolicy(activeRoute) === "always-visible" ? false : requestedHidden;
  const hiddenProgress = useRef(new Animated.Value(0)).current;
  const lastCreateTapRef = useRef(0);
  const [shellHeight, setShellHeight] = useState(0);
  const [reduceMotion, setReduceMotion] = useState(false);

  useEffect(() => {
    AccessibilityInfo.isReduceMotionEnabled().then(setReduceMotion).catch(() => undefined);
    const subscription = AccessibilityInfo.addEventListener("reduceMotionChanged", setReduceMotion);
    return () => subscription.remove();
  }, []);

  useEffect(() => {
    Animated.timing(hiddenProgress, {
      duration: reduceMotion ? 0 : hidden ? 180 : 210,
      toValue: hidden ? 1 : 0,
      useNativeDriver: true
    }).start();
  }, [hidden, hiddenProgress, reduceMotion]);

  useEffect(() => {
    cancelRefreshTapWindow();
    showBottomNav();
  }, [activeRoute, showBottomNav]);

  const offscreenDistance = Math.max(shellHeight, 180) + Math.max(insets.bottom, 10) + 64;
  const translateY = hiddenProgress.interpolate({
    inputRange: [0, 1],
    outputRange: [0, offscreenDistance]
  });
  const opacity = hiddenProgress.interpolate({
    inputRange: [0, 0.72, 1],
    outputRange: [1, 0.48, 0]
  });

  return (
    <Animated.View
      accessibilityElementsHidden={hidden}
      importantForAccessibility={hidden ? "no-hide-descendants" : "auto"}
      onLayout={(event: LayoutChangeEvent) => {
        const nextHeight = Math.ceil(event.nativeEvent.layout.height);
        setShellHeight((current) => (current === nextHeight ? current : nextHeight));
      }}
      pointerEvents={hidden ? "none" : "auto"}
      style={[styles.bottomShell, { paddingBottom: Math.max(insets.bottom, 10), opacity, transform: [{ translateY }] }]}
      testID="global-bottom-navigation"
    >
      <PulseMiniPlayerBar navigation={navigation} />
      <View pointerEvents="auto" style={styles.bottomPanel}>
        {PRIMARY_TABS.map((item) => {
          const route = state.routes.find((candidate) => candidate.name === item.routeName);
          const active = activeRoute === item.routeName || (item.name === "Create" && activeRoute === "Create");
          const badge = item.name === "Messenger" ? normalizeBadgeCount(badges?.messages) : undefined;
          const options = route ? descriptors[route.key]?.options : undefined;
          const disabled = !route && item.name !== "Create";
          const refreshDestination = item.refreshDestination;
          return (
            <Pressable
              key={item.name}
              accessibilityRole="tab"
              // The Messages tab is the only one that badges, and it says what
              // the number is. Every other tab keeps its plain label.
              accessibilityLabel={
                badge === undefined
                  ? item.accessibilityLabel
                  : `${item.accessibilityLabel}, ${badgeSpokenLabel("messages", badge)}`
              }
              accessibilityState={{ selected: active, disabled }}
              accessibilityActions={refreshDestination && active ? [{ name: "refresh", label: `Refresh ${item.label}` }] : undefined}
              onAccessibilityAction={
                refreshDestination && active
                  ? (event) => {
                      if (event.nativeEvent.actionName === "refresh") {
                        triggerRefreshDestination({
                          destination: refreshDestination,
                          source: "double-tap",
                          scrollToTop: true,
                          preserveFilters: true,
                          preserveDrafts: true
                        });
                      }
                    }
                  : undefined
              }
              testID={`global-bottom-${String(item.name).toLowerCase()}`}
              disabled={disabled}
              style={({ pressed }) => [
                styles.bottomItem,
                item.name === "Create" && styles.bottomCreateItem,
                active && styles.bottomItemActive,
                pressed && styles.pressed
              ]}
              onPress={() => {
                Haptics.selectionAsync().catch(() => undefined);
                if (item.name === "Create") {
                  const now = Date.now();
                  if (now - lastCreateTapRef.current <= 600) return;
                  lastCreateTapRef.current = now;
                  navigation.navigate("Home", { openComposer: true });
                  return;
                }
                const tap = resolveNavigationTap({
                  active,
                  destination: item.refreshDestination,
                  controlId: `bottom:${String(item.name)}`
                });
                if (tap.type === "root") {
                  scrollRefreshDestinationToTop(tap.destination);
                  return;
                }
                if (tap.type === "refresh") {
                  Haptics.impactAsync(Haptics.ImpactFeedbackStyle.Light).catch(() => undefined);
                  triggerRefreshDestination(tap.intent);
                  return;
                }
                const event = route
                  ? navigation.emit({
                      type: "tabPress",
                      target: route.key,
                      canPreventDefault: true
                    })
                  : null;
                if (!event?.defaultPrevented) navigation.navigate(item.routeName);
              }}
              onLongPress={() => {
                if (route) navigation.emit({ type: "tabLongPress", target: route.key });
              }}
            >
              <View style={[styles.bottomSymbol, item.name === "Create" && styles.bottomCreateSymbol, active && styles.bottomSymbolActive]}>
                <Ionicons name={item.icon} size={item.name === "Create" ? 34 : 26} style={[styles.bottomSymbolText, active && styles.bottomSymbolTextActive]} />
                {badge ? (
                  <View style={styles.bottomBadge}>
                    <Text style={styles.bottomBadgeText}>{formatBadge(badge)}</Text>
                  </View>
                ) : null}
              </View>
              <Text style={[styles.bottomLabel, active && styles.bottomLabelActive]} numberOfLines={1}>
                {options?.title || item.label}
              </Text>
            </Pressable>
          );
        })}
      </View>
    </Animated.View>
  );
}

// Persistent "continue listening across pages" strip: shown above the tab
// bar on every primary tab screen whenever Pulse Radio has a loaded track,
// regardless of which page the user is on. Tapping it opens the queue
// screen; the play/pause and next controls work in place without navigating.
function PulseMiniPlayerBar({ navigation }: { navigation: BottomTabBarProps["navigation"] }) {
  const [radio, setRadio] = useState<PulseRadioState>(getPulseRadioState());
  useEffect(() => subscribePulseRadio(setRadio), []);

  if (!radio.track) return null;

  const playing = radio.status === "playing";
  const busy = radio.status === "connecting" || radio.status === "buffering";
  const progress = radio.durationMillis > 0 ? Math.min(1, Math.max(0, radio.positionMillis / radio.durationMillis)) : 0;
  const hasNext = radio.queueIndex < radio.queue.length - 1 || radio.repeatMode !== "off";

  return (
    <Pressable
      accessibilityRole="button"
      accessibilityLabel={`Now playing: ${radio.track.title} by ${radio.track.artist}. Open queue`}
      testID="global-mini-player"
      style={styles.miniPlayer}
      onPress={() => (navigation as any).getParent()?.navigate("PulseQueue")}
    >
      <View style={styles.miniPlayerProgressTrack}>
        <View style={[styles.miniPlayerProgressFill, { width: `${progress * 100}%` }]} />
      </View>
      <View style={styles.miniPlayerRow}>
        <View style={styles.miniPlayerText}>
          <Text style={styles.miniPlayerTitle} numberOfLines={1}>
            {radio.track.title}
          </Text>
          <Text style={styles.miniPlayerArtist} numberOfLines={1}>
            {radio.track.artist}
          </Text>
        </View>
        <Pressable
          accessibilityRole="button"
          accessibilityLabel={playing ? "Pause" : "Play"}
          testID="global-mini-player-play-pause"
          style={styles.miniPlayerButton}
          onPress={(event) => {
            event.stopPropagation();
            Haptics.selectionAsync().catch(() => undefined);
            togglePulseRadio().catch(() => undefined);
          }}
        >
          <Ionicons name={playing ? "pause" : busy ? "hourglass-outline" : "play"} size={18} color={colors.background} />
        </Pressable>
        <Pressable
          accessibilityRole="button"
          accessibilityLabel="Next track"
          disabled={!hasNext}
          testID="global-mini-player-next"
          style={[styles.miniPlayerButton, styles.miniPlayerButtonSecondary, !hasNext && styles.disabled]}
          onPress={(event) => {
            event.stopPropagation();
            Haptics.selectionAsync().catch(() => undefined);
            playNextTrack().catch(() => undefined);
          }}
        >
          <Ionicons name="play-skip-forward" size={16} color={colors.text} />
        </Pressable>
      </View>
    </Pressable>
  );
}

export function openPrimaryCreate(navigation: NavigationProp<ParamListBase>) {
  (navigation as any).navigate("Home", { openComposer: true });
}

function IconButton({
  label,
  scopeLabel,
  icon,
  badge,
  testID,
  home,
  onPress
}: {
  label: string;
  /** What the badge counts, appended to the spoken label. Omit for no badge. */
  scopeLabel?: string;
  icon: keyof typeof Ionicons.glyphMap;
  badge?: number;
  testID?: string;
  home?: boolean;
  onPress?: () => void;
}) {
  return (
    <Pressable
      accessibilityRole="button"
      accessibilityLabel={scopeLabel ? `${label}, ${scopeLabel}` : label}
      disabled={!onPress}
      testID={testID}
      style={({ pressed }) => [styles.iconButton, home && styles.iconButtonHome, pressed && styles.pressed, !onPress && styles.disabled]}
      onPress={() => {
        Haptics.selectionAsync().catch(() => undefined);
        onPress?.();
      }}
    >
      <Ionicons name={icon} size={home ? 29 : 25} style={[styles.iconText, home && styles.iconTextHome]} />
      {badge ? (
        <View style={styles.iconBadge}>
          <Text style={styles.iconBadgeText}>{formatBadge(badge)}</Text>
        </View>
      ) : null}
    </Pressable>
  );
}

function normalizeBadgeCount(value?: number) {
  const count = Number(value || 0);
  return Number.isFinite(count) && count > 0 ? count : 0;
}

function formatBadge(value: number) {
  if (value > 99) return "99+";
  return String(value);
}

function initialsFor(value: string) {
  const parts = value.trim().split(/\s+/).filter(Boolean);
  if (!parts.length) return "PS";
  return parts.slice(0, 2).map((part) => part[0]?.toUpperCase()).join("");
}

const styles = StyleSheet.create({
  avatarButton: {
    alignItems: "center",
    backgroundColor: "rgba(159, 124, 255, 0.2)",
    borderColor: "rgba(121, 210, 255, 0.32)",
    borderRadius: 19,
    borderWidth: 1,
    height: 38,
    justifyContent: "center",
    overflow: "hidden",
    width: 38
  },
  avatarButtonHome: {
    borderRadius: 23,
    height: 46,
    shadowColor: "#9f7cff",
    shadowOpacity: 0.24,
    shadowRadius: 14,
    width: 46
  },
  avatarImage: {
    height: "100%",
    width: "100%"
  },
  avatarSignal: {
    backgroundColor: colors.accent,
    borderRadius: 5,
    borderColor: colors.background,
    borderWidth: 2,
    bottom: 1,
    height: 10,
    position: "absolute",
    right: 3,
    width: 10
  },
  avatarText: {
    ...logiNexus.typography.label,
    color: colors.text
  },
  bottomBadge: {
    alignItems: "center",
    backgroundColor: colors.accent,
    borderColor: colors.background,
    borderRadius: logiNexus.radius.capsule,
    borderWidth: 1,
    minWidth: 20,
    paddingHorizontal: 5,
    position: "absolute",
    right: -9,
    top: -8
  },
  bottomBadgeText: {
    color: colors.background,
    fontSize: 10,
    fontWeight: "900"
  },
  bottomItem: {
    alignItems: "center",
    borderRadius: 30,
    flex: 1,
    gap: 4,
    justifyContent: "center",
    minHeight: 72,
    paddingHorizontal: 2,
    paddingVertical: 6
  },
  bottomItemActive: {
    backgroundColor: "rgba(50, 230, 179, 0.14)"
  },
  bottomCreateItem: {
    // Shared with the overhang the clearance accounts for — see
    // `bottomNavMetrics.ts`. Changing these here changes that there.
    marginTop: BOTTOM_NAV_CREATE_MARGIN_TOP,
    minHeight: BOTTOM_NAV_CREATE_MIN_HEIGHT
  },
  bottomLabel: {
    ...logiNexus.typography.metadata,
    color: colors.muted,
    fontSize: 12,
    lineHeight: 16,
    textAlign: "center"
  },
  bottomLabelActive: {
    color: colors.accent
  },
  bottomPanel: {
    alignItems: "center",
    backgroundColor: "rgba(8, 16, 29, 0.9)",
    borderColor: "rgba(121, 210, 255, 0.24)",
    borderRadius: 38,
    borderWidth: 1,
    flexDirection: "row",
    gap: 4,
    // Shared with the clearance every scrollable surface reserves — see
    // `bottomNavMetrics.ts`. Changing this here changes that there.
    minHeight: BOTTOM_NAV_DOCK_PANEL_MIN_HEIGHT,
    padding: BOTTOM_NAV_DOCK_PANEL_PADDING,
    shadowColor: colors.accent,
    shadowOpacity: 0.16,
    shadowRadius: 22
  },
  bottomShell: {
    backgroundColor: "transparent",
    borderTopColor: "transparent",
    borderTopWidth: 0,
    bottom: 0,
    elevation: 40,
    left: 0,
    paddingHorizontal: logiNexus.spacing.md,
    paddingTop: BOTTOM_NAV_DOCK_PADDING_TOP,
    position: "absolute",
    right: 0,
    zIndex: 40
  },
  bottomSymbol: {
    alignItems: "center",
    backgroundColor: "transparent",
    borderColor: "transparent",
    borderRadius: logiNexus.radius.circular,
    borderWidth: 1,
    height: 46,
    justifyContent: "center",
    width: 46
  },
  bottomSymbolActive: {
    backgroundColor: colors.accent,
    borderColor: colors.accent,
    shadowColor: colors.accent,
    shadowOpacity: 0.35,
    shadowRadius: 22
  },
  bottomCreateSymbol: {
    backgroundColor: "rgba(3, 10, 21, 0.88)",
    borderColor: "rgba(121, 210, 255, 0.95)",
    borderWidth: 1,
    height: 82,
    shadowColor: "#9f7cff",
    shadowOpacity: 0.2,
    shadowRadius: 18,
    width: 82
  },
  bottomSymbolText: {
    color: colors.text,
    fontSize: 22,
    fontWeight: "900"
  },
  bottomSymbolTextActive: {
    color: colors.background
  },
  disabled: {
    opacity: 0.4
  },
  headerActions: {
    alignItems: "center",
    flexDirection: "row",
    gap: 6
  },
  headerMetaRow: {
    flexDirection: "row",
    flexWrap: "wrap",
    gap: 8,
    marginTop: 6
  },
  headerRow: {
    alignItems: "center",
    flexDirection: "row",
    gap: 8
  },
  headerShell: {
    backgroundColor: "rgba(3, 9, 18, 0.96)",
    borderBottomColor: colors.border,
    borderBottomWidth: StyleSheet.hairlineWidth,
    paddingBottom: 8,
    paddingHorizontal: logiNexus.spacing.md
  },
  headerShellHome: {
    backgroundColor: logiNexus.colors.home.backgroundDeepSpace,
    borderBottomColor: "transparent",
    paddingBottom: 18
  },
  headerSubtitle: {
    ...logiNexus.typography.metadata,
    color: colors.muted
  },
  headerSubtitleHome: {
    color: colors.accent,
    fontSize: 9,
    letterSpacing: 4,
    lineHeight: 12,
    textAlign: "center"
  },
  headerTitle: {
    ...logiNexus.typography.sectionTitle,
    color: colors.text,
    flexShrink: 1
  },
  headerTitleHome: {
    ...logiNexus.typography.home.brand,
    fontSize: 25,
    lineHeight: 31,
    textAlign: "center"
  },
  headerTitleHomeAccent: {
    color: "#9f7cff"
  },
  homeBrandPulse: {
    color: colors.accent,
    fontSize: 21,
    fontWeight: "900",
    lineHeight: 16,
    marginHorizontal: -2,
    marginTop: -4
  },
  homeBrandSignal: {
    alignItems: "center",
    flexDirection: "row",
    height: 10,
    justifyContent: "center",
    marginTop: 3,
    width: 120
  },
  homeBrandSignalPrimary: {
    backgroundColor: colors.accent,
    height: 1,
    width: 58
  },
  homeBrandSignalSecondary: {
    backgroundColor: "#9f7cff",
    height: 1,
    width: 58
  },
  iconBadge: {
    alignItems: "center",
    backgroundColor: colors.danger,
    borderRadius: logiNexus.radius.capsule,
    minWidth: 20,
    paddingHorizontal: 5,
    position: "absolute",
    right: -5,
    top: -5
  },
  iconBadgeText: {
    color: colors.text,
    fontSize: 10,
    fontWeight: "900"
  },
  iconButton: {
    alignItems: "center",
    backgroundColor: "rgba(255,255,255,0.045)",
    borderColor: "rgba(255,255,255,0.18)",
    borderRadius: 19,
    borderWidth: 1,
    height: 38,
    justifyContent: "center",
    width: 38
  },
  iconButtonHome: {
    backgroundColor: "rgba(255,255,255,0.055)",
    borderColor: "rgba(255,255,255,0.2)",
    borderRadius: 23,
    height: 46,
    shadowColor: colors.accentStrong,
    shadowOpacity: 0.11,
    shadowRadius: 14,
    width: 46
  },
  iconButtonSpacer: {
    height: 38,
    width: 38
  },
  iconButtonSpacerHome: {
    height: 46,
    width: 46
  },
  iconText: {
    color: colors.text,
    fontSize: 19,
    fontWeight: "900"
  },
  iconTextHome: {
    fontSize: 25
  },
  miniPlayer: {
    backgroundColor: "rgba(8, 16, 29, 0.94)",
    borderColor: "rgba(121, 210, 255, 0.24)",
    borderRadius: 20,
    borderWidth: 1,
    marginBottom: 8,
    overflow: "hidden"
  },
  miniPlayerProgressTrack: {
    backgroundColor: "rgba(255,255,255,0.08)",
    height: 2,
    width: "100%"
  },
  miniPlayerProgressFill: {
    backgroundColor: colors.accent,
    height: 2
  },
  miniPlayerRow: {
    alignItems: "center",
    flexDirection: "row",
    gap: 10,
    paddingHorizontal: 14,
    paddingVertical: 10
  },
  miniPlayerText: {
    flex: 1,
    minWidth: 0
  },
  miniPlayerTitle: {
    color: colors.text,
    fontSize: 13,
    fontWeight: "700"
  },
  miniPlayerArtist: {
    color: colors.muted,
    fontSize: 11,
    marginTop: 1
  },
  miniPlayerButton: {
    alignItems: "center",
    backgroundColor: colors.accent,
    borderRadius: 17,
    height: 34,
    justifyContent: "center",
    width: 34
  },
  miniPlayerButtonSecondary: {
    backgroundColor: "rgba(255,255,255,0.08)"
  },
  pressed: {
    opacity: 0.72,
    transform: [{ scale: 0.98 }]
  },
  titleBlock: {
    flex: 1,
    minWidth: 0
  },
  titleBlockHome: {
    alignItems: "center"
  },
  brandRow: {
    alignItems: "center",
    flexDirection: "row",
    gap: 4,
    justifyContent: "center"
  }
});
