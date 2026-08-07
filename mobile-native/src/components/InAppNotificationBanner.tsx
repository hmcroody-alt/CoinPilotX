import * as Notifications from "expo-notifications";
import { useEffect, useReducer, useRef } from "react";
import {
  AccessibilityInfo,
  Animated,
  PanResponder,
  Platform,
  Pressable,
  StyleSheet,
  Text,
  View
} from "react-native";
import { useSafeAreaInsets } from "react-native-safe-area-context";
import {
  BannerNotification,
  BannerState,
  DismissReason,
  dismissBanner,
  initialBannerState,
  presentBanner,
  resolveAutoDismissMs
} from "../navigation/notificationBannerLifecycle";
import { markNotificationSeen, notificationStableId } from "../navigation/notificationDedupe";
import { notificationTargetFromData, routeNotificationTarget } from "../navigation/notificationRouting";
import { colors } from "../theme/colors";
import { createThemedStyles } from "../theme/themedStyles";

/**
 * Foreground in-app notification banner.
 *
 * Fixes Issue 4 ("banner stuck at the top until manually dismissed"): the
 * foreground presentation now has a single owned auto-dismiss timer, a new
 * banner supersedes the previous one, and the timer is cancelled on unmount and
 * whenever the banner is replaced — so nothing is ever left stuck or mounted
 * invisibly. It pairs with the push handler suppressing the OS foreground banner
 * so a notification never renders both a native and an in-app banner.
 */

type BannerAction =
  | { type: "present"; banner: BannerNotification }
  | { type: "dismiss"; token?: number };

function reducer(state: BannerState, action: BannerAction): BannerState {
  switch (action.type) {
    case "present":
      return presentBanner(state, action.banner);
    case "dismiss":
      return dismissBanner(state, action.token);
    default:
      return state;
  }
}

/** Map a foreground notification into a banner, or null if it should not surface one. */
export function bannerFromNotification(notification: Notifications.Notification): BannerNotification | null {
  const content = notification.request?.content;
  if (!content) return null;
  const data = (content.data || {}) as Record<string, unknown>;
  // Incoming calls have their own full-screen layer — don't double-surface them.
  if (data.call_id || data.callId) return null;
  const title = String(content.title || content.subtitle || "PulseSoc").trim();
  const body = String(content.body || "").trim();
  if (!title && !body) return null;
  return {
    id: String(notification.request.identifier || `${Date.now()}`),
    title: title || "PulseSoc",
    body: body || undefined,
    target: notificationTargetFromData(content.data)
  };
}

export function InAppNotificationBanner() {
  const insets = useSafeAreaInsets();
  const [state, dispatch] = useReducer(reducer, undefined, initialBannerState);
  const screenReaderRef = useRef(false);
  const translateY = useRef(new Animated.Value(-160)).current;
  const dragY = useRef(new Animated.Value(0)).current;

  useEffect(() => {
    let mounted = true;
    AccessibilityInfo.isScreenReaderEnabled()
      .then((enabled) => {
        if (mounted) screenReaderRef.current = enabled;
      })
      .catch(() => undefined);
    const sub = AccessibilityInfo.addEventListener("screenReaderChanged", (enabled) => {
      screenReaderRef.current = enabled;
    });
    return () => {
      mounted = false;
      sub?.remove?.();
    };
  }, []);

  useEffect(() => {
    const received = Notifications.addNotificationReceivedListener((notification) => {
      const banner = bannerFromNotification(notification);
      if (!banner) return;
      // Surface each message once: drop a repeat of the same server-issued
      // notification/message id (second token, replay, double listener). Keyed on
      // stable ids only — never on the banner text.
      const stableId = notificationStableId(
        notification.request?.content?.data as Record<string, unknown>,
        notification.request?.identifier
      );
      if (!markNotificationSeen(stableId)) return;
      dispatch({ type: "present", banner });
    });
    return () => received.remove();
  }, []);

  // Owned auto-dismiss timer, re-armed for each presented banner and cancelled on
  // replacement/unmount. Keyed on token so a stale timer can never fire late.
  useEffect(() => {
    if (!state.banner) return undefined;
    dragY.setValue(0);
    Animated.spring(translateY, { toValue: 0, useNativeDriver: true, bounciness: 6 }).start();
    const ms = resolveAutoDismissMs(state.banner, { screenReaderEnabled: screenReaderRef.current });
    if (ms === null) return undefined;
    const token = state.token;
    const timer = setTimeout(() => dispatch({ type: "dismiss", token }), ms);
    return () => clearTimeout(timer);
  }, [state.banner, state.token, translateY, dragY]);

  const close = (_reason: DismissReason) => {
    Animated.timing(translateY, { toValue: -160, duration: 160, useNativeDriver: true }).start(() => {
      dispatch({ type: "dismiss" });
    });
  };

  const panResponder = useRef(
    PanResponder.create({
      onMoveShouldSetPanResponder: (_evt, gesture) => gesture.dy < -6 && Math.abs(gesture.dy) > Math.abs(gesture.dx),
      onPanResponderMove: (_evt, gesture) => {
        if (gesture.dy < 0) dragY.setValue(gesture.dy);
      },
      onPanResponderRelease: (_evt, gesture) => {
        if (gesture.dy < -40) close("swipe");
        else Animated.spring(dragY, { toValue: 0, useNativeDriver: true }).start();
      }
    })
  ).current;

  if (!state.banner) return null;
  const banner = state.banner;

  const onPress = () => {
    if (banner.target) routeNotificationTarget(banner.target).catch(() => undefined);
    close("tap");
  };

  return (
    <Animated.View
      pointerEvents="box-none"
      style={[styles.host, { top: insets.top + 6 }]}
    >
      <Animated.View
        style={[styles.card, { transform: [{ translateY: Animated.add(translateY, dragY) }] }]}
        {...panResponder.panHandlers}
      >
        <Pressable
          onPress={onPress}
          accessibilityRole="button"
          accessibilityLabel={`${banner.title}${banner.body ? `. ${banner.body}` : ""}. Double tap to open, swipe up to dismiss.`}
          style={styles.pressable}
        >
          <View style={styles.dot} />
          <View style={styles.textCol}>
            <Text style={styles.title} numberOfLines={1}>
              {banner.title}
            </Text>
            {banner.body ? (
              <Text style={styles.body} numberOfLines={2}>
                {banner.body}
              </Text>
            ) : null}
          </View>
        </Pressable>
      </Animated.View>
    </Animated.View>
  );
}

const styles = createThemedStyles(() => ({
  host: {
    position: "absolute",
    left: 10,
    right: 10,
    zIndex: 9000,
    elevation: 9000,
    alignItems: "stretch"
  },
  card: {
    backgroundColor: colors.glassStrong,
    borderRadius: 16,
    borderWidth: StyleSheet.hairlineWidth,
    borderColor: colors.border,
    shadowColor: "#000",
    shadowOffset: { width: 0, height: 6 },
    shadowOpacity: 0.3,
    shadowRadius: 12,
    ...Platform.select({ android: { elevation: 12 } })
  },
  pressable: {
    flexDirection: "row",
    alignItems: "center",
    paddingVertical: 12,
    paddingHorizontal: 14,
    gap: 12
  },
  dot: {
    width: 10,
    height: 10,
    borderRadius: 5,
    backgroundColor: colors.accentStrong
  },
  textCol: { flex: 1 },
  title: { color: colors.text, fontSize: 15, fontWeight: "700" },
  body: { color: colors.muted, fontSize: 13, marginTop: 2 }
}));
