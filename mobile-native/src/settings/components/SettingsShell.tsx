/**
 * Structural chrome shared by every settings surface.
 *
 * `SettingsShell` owns the three things that were inconsistent across the old
 * screens: bottom-navigation scroll behaviour, safe-area/dock clearance, and
 * save-state feedback. Screens supply content; they never manage scroll
 * plumbing themselves, which is what keeps the dock behaviour identical
 * everywhere.
 */

import { Children, ReactNode, useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  ActivityIndicator,
  Animated,
  LayoutAnimation,
  Platform,
  Pressable,
  RefreshControl,
  ScrollView,
  StyleSheet,
  Text,
  TextInput,
  UIManager,
  View
} from "react-native";
import { Ionicons } from "@expo/vector-icons";
import { useSafeAreaInsets } from "react-native-safe-area-context";
import { BOTTOM_NAV_CONTENT_CLEARANCE, useBottomNavScrollVisibility } from "../../navigation/BottomNavVisibility";
import { useTheme } from "../../theme/ThemeContext";
import { SyncStatus, usePreferences } from "../store";

if (Platform.OS === "android" && UIManager.setLayoutAnimationEnabledExperimental) {
  UIManager.setLayoutAnimationEnabledExperimental(true);
}

/* -------------------------------------------------------------------------- */
/*                                   Shell                                     */
/* -------------------------------------------------------------------------- */

export function SettingsShell({
  children,
  onRefresh,
  refreshing = false,
  /** Set false on nested stack screens that render above the tab dock. */
  bottomDock = true,
  footer
}: {
  children: ReactNode;
  onRefresh?: () => void | Promise<void>;
  refreshing?: boolean;
  bottomDock?: boolean;
  footer?: ReactNode;
}) {
  const theme = useTheme();
  const insets = useSafeAreaInsets();
  const bottomNavScroll = useBottomNavScrollVisibility({ enabled: bottomDock });
  const [pulling, setPulling] = useState(false);

  const handleRefresh = useCallback(async () => {
    if (!onRefresh) return;
    setPulling(true);
    try {
      await onRefresh();
    } finally {
      setPulling(false);
    }
  }, [onRefresh]);

  const paddingBottom = useMemo(
    () => Math.max(insets.bottom, 12) + (bottomDock ? BOTTOM_NAV_CONTENT_CLEARANCE : 24),
    [bottomDock, insets.bottom]
  );

  return (
    <View style={[styles.root, { backgroundColor: theme.colors.background }]}>
      <SyncStatusBar />
      <ScrollView
        style={styles.root}
        contentContainerStyle={[styles.content, { paddingBottom }]}
        keyboardShouldPersistTaps="handled"
        keyboardDismissMode={Platform.OS === "ios" ? "interactive" : "on-drag"}
        onScroll={bottomNavScroll.onScroll}
        onScrollBeginDrag={bottomNavScroll.onScrollBeginDrag}
        scrollEventThrottle={bottomNavScroll.scrollEventThrottle}
        // Keeps the dock from flickering when content is barely scrollable.
        alwaysBounceVertical={false}
        refreshControl={
          onRefresh ? (
            <RefreshControl
              refreshing={refreshing || pulling}
              onRefresh={handleRefresh}
              tintColor={theme.colors.accent}
              colors={[theme.colors.accent]}
              progressBackgroundColor={theme.colors.surface}
            />
          ) : undefined
        }
      >
        {children}
        {footer ? <View style={styles.footer}>{footer}</View> : null}
      </ScrollView>
    </View>
  );
}

/* -------------------------------------------------------------------------- */
/*                                  Sections                                   */
/* -------------------------------------------------------------------------- */

/**
 * A grouped card. Children are rendered with hairline separators between them
 * (iOS inset-grouped convention) so individual rows never draw their own
 * borders and double-up at the seams.
 */
export function SettingsSection({
  title,
  description,
  footnote,
  children,
  busy = false
}: {
  title?: string;
  description?: string;
  footnote?: string;
  children: ReactNode;
  busy?: boolean;
}) {
  const theme = useTheme();
  /**
   * `Children.toArray` rather than a hand-rolled `Array.isArray` check: it
   * flattens nested arrays and fragments, drops null/undefined/boolean, and
   * assigns stable keys. That matters because separators are drawn *between*
   * items — a screen that mixes a static row with a `.map()` of rows would
   * otherwise see the whole mapped array collapse into a single item and lose
   * every separator inside it.
   */
  const items = Children.toArray(children);

  return (
    <View style={[styles.section, { marginTop: theme.metrics.sectionGap }]}>
      {title ? (
        <View style={styles.sectionHeader}>
          <Text
            style={[
              styles.sectionTitle,
              {
                color: theme.colors.muted,
                fontSize: theme.scaleFont(12),
                fontWeight: theme.metrics.titleWeight
              }
            ]}
          >
            {title.toUpperCase()}
          </Text>
          {busy ? <ActivityIndicator size="small" color={theme.colors.accent} /> : null}
        </View>
      ) : null}
      {description ? (
        <Text style={[styles.sectionDescription, { color: theme.colors.muted, fontSize: theme.scaleFont(13) }]}>
          {description}
        </Text>
      ) : null}
      <View
        style={[
          styles.sectionCard,
          {
            backgroundColor: theme.colors.surface,
            borderColor: theme.colors.border,
            borderRadius: theme.metrics.radius
          }
        ]}
      >
        {items.map((child, index) => (
          <View key={(child as { key?: string }).key ?? index}>
            {index > 0 ? (
              <View style={[styles.separator, { backgroundColor: theme.colors.border, marginLeft: theme.metrics.rowPaddingHorizontal }]} />
            ) : null}
            {child}
          </View>
        ))}
      </View>
      {footnote ? (
        <Text style={[styles.footnote, { color: theme.colors.muted, fontSize: theme.scaleFont(12) }]}>{footnote}</Text>
      ) : null}
    </View>
  );
}

/** Large page heading used at the top of each settings screen. */
export function SettingsHeader({ title, subtitle }: { title: string; subtitle?: string }) {
  const theme = useTheme();
  return (
    <View style={styles.pageHeader}>
      <Text
        style={{
          color: theme.colors.text,
          fontSize: theme.scaleFont(30),
          fontWeight: "900",
          letterSpacing: -0.5
        }}
      >
        {title}
      </Text>
      {subtitle ? (
        <Text style={{ color: theme.colors.muted, fontSize: theme.scaleFont(14), lineHeight: theme.scaleFont(20), marginTop: 6 }}>
          {subtitle}
        </Text>
      ) : null}
    </View>
  );
}

/* -------------------------------------------------------------------------- */
/*                                Sync feedback                                */
/* -------------------------------------------------------------------------- */

const STATUS_PRESENTATION: Record<SyncStatus, { label: string; icon: keyof typeof Ionicons.glyphMap; tone: "accent" | "danger" | "warning" } | null> = {
  idle: null,
  saved: { label: "Saved", icon: "checkmark-circle", tone: "accent" },
  saving: { label: "Saving…", icon: "sync", tone: "accent" },
  error: { label: "Couldn't save", icon: "alert-circle", tone: "danger" },
  offline: { label: "Offline — will retry", icon: "cloud-offline", tone: "warning" }
};

/**
 * Non-blocking save indicator. Auto-hides after a successful save so the user
 * gets confirmation without a permanent banner eating vertical space.
 */
export function SyncStatusBar() {
  const { status, error, clearError } = usePreferences();
  const theme = useTheme();
  const opacity = useRef(new Animated.Value(0)).current;
  const [visibleStatus, setVisibleStatus] = useState<SyncStatus>("idle");
  const hideTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  const presentation = STATUS_PRESENTATION[visibleStatus];

  useEffect(() => {
    if (hideTimer.current) {
      clearTimeout(hideTimer.current);
      hideTimer.current = null;
    }
    setVisibleStatus(status);
    Animated.timing(opacity, {
      toValue: status === "idle" ? 0 : 1,
      duration: theme.duration(180),
      useNativeDriver: true
    }).start();

    // "Saved" is a confirmation, not a state — fade it out so the banner does
    // not permanently occupy the top of every settings screen.
    if (status === "saved") {
      hideTimer.current = setTimeout(() => {
        hideTimer.current = null;
        Animated.timing(opacity, { toValue: 0, duration: theme.duration(220), useNativeDriver: true }).start(({ finished }) => {
          if (finished) setVisibleStatus("idle");
        });
      }, 1400);
    }

    return () => {
      if (hideTimer.current) {
        clearTimeout(hideTimer.current);
        hideTimer.current = null;
      }
    };
  }, [status, opacity, theme]);

  if (!presentation) return null;

  const tone =
    presentation.tone === "danger" ? theme.colors.danger : presentation.tone === "warning" ? theme.colors.warning : theme.colors.accent;

  const dismissible = visibleStatus === "error" || visibleStatus === "offline";

  return (
    <Animated.View
      accessibilityLiveRegion="polite"
      style={[
        styles.syncBar,
        {
          opacity,
          backgroundColor: theme.colors.surfaceRaised,
          borderBottomColor: theme.colors.border
        }
      ]}
    >
      <Ionicons name={presentation.icon} size={theme.scaleFont(15)} color={tone} />
      <Text style={[styles.syncText, { color: theme.colors.text, fontSize: theme.scaleFont(13) }]} numberOfLines={1}>
        {error && dismissible ? error : presentation.label}
      </Text>
      {dismissible ? (
        <Pressable accessibilityRole="button" accessibilityLabel="Dismiss" hitSlop={10} onPress={clearError}>
          <Ionicons name="close" size={theme.scaleFont(16)} color={theme.colors.muted} />
        </Pressable>
      ) : null}
    </Animated.View>
  );
}

/* -------------------------------------------------------------------------- */
/*                                   Search                                    */
/* -------------------------------------------------------------------------- */

export function SettingsSearchField({
  value,
  onChangeText,
  placeholder = "Search settings"
}: {
  value: string;
  onChangeText: (next: string) => void;
  placeholder?: string;
}) {
  const theme = useTheme();
  return (
    <View
      style={[
        styles.search,
        {
          backgroundColor: theme.colors.surfaceRaised,
          borderColor: theme.colors.border,
          borderRadius: theme.metrics.radius
        }
      ]}
    >
      <Ionicons name="search" size={theme.scaleFont(17)} color={theme.colors.muted} />
      <TextInput
        value={value}
        onChangeText={onChangeText}
        placeholder={placeholder}
        placeholderTextColor={theme.colors.muted}
        autoCorrect={false}
        autoCapitalize="none"
        returnKeyType="search"
        clearButtonMode="while-editing"
        accessibilityLabel={placeholder}
        style={[styles.searchInput, { color: theme.colors.text, fontSize: theme.scaleFont(16) }]}
      />
      {value.length > 0 && Platform.OS !== "ios" ? (
        <Pressable accessibilityRole="button" accessibilityLabel="Clear search" hitSlop={10} onPress={() => onChangeText("")}>
          <Ionicons name="close-circle" size={theme.scaleFont(17)} color={theme.colors.muted} />
        </Pressable>
      ) : null}
    </View>
  );
}

/** Animate the next layout change unless the user asked for reduced motion. */
export function animateNextLayout(reduceMotion: boolean) {
  if (reduceMotion) return;
  LayoutAnimation.configureNext(LayoutAnimation.create(200, LayoutAnimation.Types.easeInEaseOut, LayoutAnimation.Properties.opacity));
}

export function SettingsEmptyState({
  icon = "file-tray-outline",
  title,
  body,
  action
}: {
  icon?: keyof typeof Ionicons.glyphMap;
  title: string;
  body?: string;
  action?: ReactNode;
}) {
  const theme = useTheme();
  return (
    <View style={styles.empty}>
      <View style={[styles.emptyGlyph, { borderColor: theme.colors.border, backgroundColor: theme.colors.surfaceRaised }]}>
        <Ionicons name={icon} size={theme.scaleFont(26)} color={theme.colors.muted} />
      </View>
      <Text style={{ color: theme.colors.text, fontSize: theme.scaleFont(17), fontWeight: theme.metrics.titleWeight, marginTop: 14 }}>
        {title}
      </Text>
      {body ? (
        <Text
          style={{
            color: theme.colors.muted,
            fontSize: theme.scaleFont(14),
            lineHeight: theme.scaleFont(20),
            textAlign: "center",
            marginTop: 6
          }}
        >
          {body}
        </Text>
      ) : null}
      {action ? <View style={{ marginTop: 16 }}>{action}</View> : null}
    </View>
  );
}

const styles = StyleSheet.create({
  root: { flex: 1 },
  content: { paddingHorizontal: 16, paddingTop: 8 },
  pageHeader: { paddingTop: 10, paddingBottom: 2 },
  section: { width: "100%" },
  sectionHeader: {
    alignItems: "center",
    flexDirection: "row",
    justifyContent: "space-between",
    marginBottom: 8,
    paddingHorizontal: 4
  },
  sectionTitle: { letterSpacing: 0.8 },
  sectionDescription: { marginBottom: 10, paddingHorizontal: 4, lineHeight: 18 },
  sectionCard: { borderWidth: StyleSheet.hairlineWidth, overflow: "hidden" },
  separator: { height: StyleSheet.hairlineWidth },
  footnote: { marginTop: 8, paddingHorizontal: 4, lineHeight: 17 },
  footer: { marginTop: 28, alignItems: "center" },
  syncBar: {
    alignItems: "center",
    borderBottomWidth: StyleSheet.hairlineWidth,
    flexDirection: "row",
    gap: 8,
    paddingHorizontal: 16,
    paddingVertical: 8
  },
  syncText: { flex: 1, fontWeight: "600" },
  search: {
    alignItems: "center",
    borderWidth: StyleSheet.hairlineWidth,
    flexDirection: "row",
    gap: 8,
    marginTop: 14,
    paddingHorizontal: 12,
    minHeight: 44
  },
  searchInput: { flex: 1, paddingVertical: 10 },
  empty: { alignItems: "center", paddingHorizontal: 24, paddingVertical: 44 },
  emptyGlyph: {
    alignItems: "center",
    borderRadius: 30,
    borderWidth: StyleSheet.hairlineWidth,
    height: 60,
    justifyContent: "center",
    width: 60
  }
});
