import { BottomTabBarProps } from "@react-navigation/bottom-tabs";
import { NavigationProp, ParamListBase } from "@react-navigation/native";
import { Image, Pressable, StyleSheet, Text, View } from "react-native";
import { useSafeAreaInsets } from "react-native-safe-area-context";
import { LogiNexusBadge, LogiNexusSignalIndicator } from "../components/LogiNexus";
import { colors } from "../theme/colors";
import { logiNexus } from "../theme/logiNexus";
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
  symbol: string;
  accessibilityLabel: string;
}> = [
  { name: "Home", routeName: "Home", label: "Home", symbol: "⌂", accessibilityLabel: "Open Home" },
  { name: "Reels", routeName: "Reels", label: "Reels", symbol: "▶", accessibilityLabel: "Open Reels" },
  { name: "Create", routeName: "Create", label: "Create", symbol: "+", accessibilityLabel: "Open Create" },
  { name: "Messenger", routeName: "Messenger", label: "Messages", symbol: "☵", accessibilityLabel: "Open Messages" },
  { name: "Profile", routeName: "Profile", label: "Profile", symbol: "◉", accessibilityLabel: "Open Profile" }
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
          <IconButton label="Back" symbol="‹" testID="global-header-back" onPress={onBack} />
        ) : showDrawer ? (
          <IconButton label="Open PulseSoc navigation drawer" symbol="☰" testID="global-header-drawer" onPress={onOpenDrawer} />
        ) : (
          <View style={styles.iconButtonSpacer} />
        )}

        <View style={[styles.titleBlock, homeMode && styles.titleBlockHome]}>
          <View style={styles.brandRow}>
            {homeMode ? null : <LogiNexusSignalIndicator active tone={intelligenceMode ? "intelligence" : "default"} />}
            <Text style={[styles.headerTitle, homeMode && styles.headerTitleHome]} numberOfLines={1}>
              {title}
            </Text>
          </View>
          {subtitle ? (
            <Text style={[styles.headerSubtitle, homeMode && styles.headerSubtitleHome]} numberOfLines={1}>
              {subtitle}
            </Text>
          ) : null}
        </View>

        <View style={styles.headerActions}>
          {onOpenSearch ? <IconButton label="Search PulseSoc" symbol="⌕" testID="global-header-search" onPress={onOpenSearch} /> : null}
          {onOpenMessages ? <IconButton label="Open Messages" symbol="☏" badge={messageCount} testID="global-header-messages" onPress={onOpenMessages} /> : null}
          {onOpenActivity ? <IconButton label="Open Activity Inbox" symbol="◔" badge={activityCount} testID="global-header-activity" onPress={onOpenActivity} /> : null}
          {onOpenProfile ? (
            <Pressable
              accessibilityRole="button"
              accessibilityLabel="Open Profile"
              testID="global-header-profile"
              style={({ pressed }) => [styles.avatarButton, pressed && styles.pressed]}
              onPress={onOpenProfile}
            >
              {identity?.avatarUrl ? <Image source={{ uri: identity.avatarUrl }} style={styles.avatarImage} /> : <Text style={styles.avatarText}>{initials}</Text>}
              {identity?.verified || identity?.premium ? <View style={styles.avatarSignal} /> : null}
            </Pressable>
          ) : null}
        </View>
      </View>
      {!homeMode ? <View style={styles.headerMetaRow}>
        <LogiNexusBadge label={intelligenceMode ? "UNDX" : "LogiNexus"} tone={intelligenceMode ? "intelligence" : "default"} />
        {identity?.attention ? <LogiNexusBadge label="attention" tone="warning" /> : null}
        {badges?.alerts ? <LogiNexusBadge label={`${formatBadge(badges.alerts)} alerts`} tone="intelligence" /> : null}
      </View> : null}
    </View>
  );
}

export function LogiNexusBottomNavigation({ state, descriptors, navigation, badges }: BottomTabBarProps & { badges?: GlobalNavigationBadges }) {
  const insets = useSafeAreaInsets();
  const activeRoute = state.routes[state.index]?.name as keyof AppTabParamList | undefined;

  return (
    <View style={[styles.bottomShell, { paddingBottom: Math.max(insets.bottom, 10) }]} testID="global-bottom-navigation">
      <View style={styles.bottomPanel}>
        {PRIMARY_TABS.map((item) => {
          const route = state.routes.find((candidate) => candidate.name === item.routeName);
          const active = activeRoute === item.routeName || (item.name === "Create" && activeRoute === "Create");
          const badge = item.name === "Messenger" ? normalizeBadgeCount(badges?.messages) : undefined;
          const options = route ? descriptors[route.key]?.options : undefined;
          const disabled = !route && item.name !== "Create";
          return (
            <Pressable
              key={item.name}
              accessibilityRole="tab"
              accessibilityLabel={item.accessibilityLabel}
              accessibilityState={{ selected: active, disabled }}
              testID={`global-bottom-${String(item.name).toLowerCase()}`}
              disabled={disabled}
              style={({ pressed }) => [
                styles.bottomItem,
                item.name === "Create" && styles.bottomCreateItem,
                active && styles.bottomItemActive,
                pressed && styles.pressed
              ]}
              onPress={() => {
                if (item.name === "Create") {
                  navigation.navigate("Home", { openComposer: true });
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
                <Text style={[styles.bottomSymbolText, active && styles.bottomSymbolTextActive]}>{item.symbol}</Text>
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
    </View>
  );
}

export function openPrimaryCreate(navigation: NavigationProp<ParamListBase>) {
  (navigation as any).navigate("Home", { openComposer: true });
}

function IconButton({ label, symbol, badge, testID, onPress }: { label: string; symbol: string; badge?: number; testID?: string; onPress?: () => void }) {
  return (
    <Pressable
      accessibilityRole="button"
      accessibilityLabel={label}
      disabled={!onPress}
      testID={testID}
      style={({ pressed }) => [styles.iconButton, pressed && styles.pressed, !onPress && styles.disabled]}
      onPress={onPress}
    >
      <Text style={styles.iconText}>{symbol}</Text>
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
    backgroundColor: "rgba(255,255,255,0.08)",
    borderColor: "rgba(255,255,255,0.18)",
    borderRadius: logiNexus.radius.circular,
    borderWidth: 1,
    height: 38,
    justifyContent: "center",
    overflow: "hidden",
    width: 38
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
    borderRadius: 24,
    flex: 1,
    gap: 4,
    justifyContent: "center",
    minHeight: 58,
    paddingHorizontal: 2,
    paddingVertical: 6
  },
  bottomItemActive: {
    backgroundColor: "rgba(50, 230, 179, 0.1)"
  },
  bottomCreateItem: {
    marginTop: -14,
    minHeight: 74
  },
  bottomLabel: {
    ...logiNexus.typography.metadata,
    color: colors.muted,
    fontSize: 11,
    lineHeight: 14,
    textAlign: "center"
  },
  bottomLabelActive: {
    color: colors.accent
  },
  bottomPanel: {
    alignItems: "center",
    backgroundColor: "rgba(3, 8, 18, 0.92)",
    borderColor: "rgba(121, 210, 255, 0.18)",
    borderRadius: 28,
    borderWidth: 1,
    flexDirection: "row",
    gap: 2,
    minHeight: 74,
    padding: 6
  },
  bottomShell: {
    backgroundColor: "transparent",
    borderTopColor: "transparent",
    borderTopWidth: 0,
    paddingHorizontal: logiNexus.spacing.md,
    paddingTop: 6
  },
  bottomSymbol: {
    alignItems: "center",
    backgroundColor: "transparent",
    borderColor: "transparent",
    borderRadius: logiNexus.radius.circular,
    borderWidth: 1,
    height: 32,
    justifyContent: "center",
    width: 32
  },
  bottomSymbolActive: {
    backgroundColor: colors.accent,
    borderColor: colors.accent,
    shadowColor: colors.accent,
    shadowOpacity: 0.35,
    shadowRadius: 18
  },
  bottomCreateSymbol: {
    backgroundColor: "rgba(50, 230, 179, 0.2)",
    borderColor: colors.accent,
    borderWidth: 1,
    height: 54,
    width: 54
  },
  bottomSymbolText: {
    color: colors.text,
    fontSize: 17,
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
    paddingBottom: 6
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
    fontSize: 21,
    lineHeight: 24,
    textAlign: "center"
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
    backgroundColor: "rgba(255,255,255,0.055)",
    borderColor: "rgba(255,255,255,0.16)",
    borderRadius: 14,
    borderWidth: 1,
    height: 36,
    justifyContent: "center",
    width: 36
  },
  iconButtonSpacer: {
    height: 36,
    width: 36
  },
  iconText: {
    color: colors.text,
    fontSize: 18,
    fontWeight: "900"
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
    gap: 8,
    justifyContent: "center"
  }
});
