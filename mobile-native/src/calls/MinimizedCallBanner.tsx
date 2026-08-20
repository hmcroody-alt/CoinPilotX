/**
 * MinimizedCallBanner — the global "you are still in a call" pill.
 *
 * Rendered once at the navigator root and driven entirely by the module-scope
 * call session store: it appears whenever a call session is alive and the Call
 * screen is not focused (minimized or navigated away), shows who the call is
 * with and how long it has been connected, returns to the Call screen on tap,
 * and can hang up directly. It owns no media — hang-up goes through
 * `hangupCallSession`, the same single teardown path the Call screen uses.
 */
import { Ionicons } from "@expo/vector-icons";
import { useEffect, useState } from "react";
import { Pressable, StyleSheet, Text, View } from "react-native";
import { useSafeAreaInsets } from "react-native-safe-area-context";
import { useTranslation } from "../i18n";
import { navigationRef } from "../navigation/notificationRouting";
import { colors } from "../theme/colors";
import { hangupCallSession, useCallSession } from "./callSessionStore";

export function MinimizedCallBanner() {
  const session = useCallSession();
  const { t } = useTranslation();
  const insets = useSafeAreaInsets();
  const [nowMs, setNowMs] = useState(() => Date.now());
  const visible = session.sessionActive && Boolean(session.callId) && !session.callScreenFocused;
  const connectedAtMs = session.connectedAtMs;

  useEffect(() => {
    if (!visible || !connectedAtMs) return undefined;
    setNowMs(Date.now());
    const timer = setInterval(() => setNowMs(Date.now()), 1000);
    return () => clearInterval(timer);
  }, [connectedAtMs, visible]);

  if (!visible) return null;

  const statusText = connectedAtMs
    ? formatBannerDuration(Math.max(0, Math.floor((nowMs - connectedAtMs) / 1000)))
    : session.reconnecting
      ? t("messaging:calls.reconnecting")
      : String(session.call?.status || "").toLowerCase() === "ringing"
        ? t("messaging:calls.ringing")
        : t("messaging:calls.calling");

  const returnToCall = () => {
    if (!navigationRef.isReady()) return;
    navigationRef.navigate("Call", {
      callId: session.callId,
      conversationId: session.conversationId,
      callType: session.callType,
      direction: session.direction || undefined,
      title: session.title || undefined
    });
  };

  return (
    <View pointerEvents="box-none" style={[styles.layer, { top: Math.max(insets.top + 6, 14) }]}>
      <Pressable
        accessibilityRole="button"
        accessibilityLabel={t("messaging:calls.returnToCall")}
        style={({ pressed }) => [styles.pill, pressed && styles.pressed]}
        onPress={returnToCall}
      >
        <View style={styles.liveDot} />
        <View style={styles.copy}>
          <Text style={styles.title} numberOfLines={1}>{session.title || t("messaging:calls.ongoingCall")}</Text>
          <Text style={styles.status} numberOfLines={1}>{statusText}</Text>
        </View>
        <Pressable
          accessibilityRole="button"
          accessibilityLabel={t("messaging:calls.endCall")}
          hitSlop={8}
          style={({ pressed }) => [styles.hangup, pressed && styles.pressed]}
          onPress={() => { hangupCallSession("native_hangup").catch(() => undefined); }}
        >
          <Ionicons name="call" color="#fff" size={18} style={styles.hangupIcon} />
        </Pressable>
      </Pressable>
    </View>
  );
}

function formatBannerDuration(totalSeconds: number) {
  const minutes = Math.floor(totalSeconds / 60);
  const seconds = totalSeconds % 60;
  return `${String(minutes).padStart(2, "0")}:${String(seconds).padStart(2, "0")}`;
}

const styles = StyleSheet.create({
  layer: { position: "absolute", left: 0, right: 0, alignItems: "center", zIndex: 60, elevation: 12 },
  pill: {
    flexDirection: "row",
    alignItems: "center",
    gap: 10,
    maxWidth: "92%",
    minHeight: 48,
    borderRadius: 24,
    paddingLeft: 16,
    paddingRight: 8,
    paddingVertical: 6,
    backgroundColor: "rgba(7,24,37,0.96)",
    borderWidth: 1,
    borderColor: "rgba(48,230,185,0.45)",
    shadowColor: colors.accent,
    shadowOpacity: 0.35,
    shadowRadius: 14
  },
  liveDot: { width: 9, height: 9, borderRadius: 5, backgroundColor: colors.accent },
  copy: { flexShrink: 1, minWidth: 0 },
  title: { color: colors.text, fontSize: 13, fontWeight: "900" },
  status: { color: "#aef4df", fontSize: 12, fontWeight: "700", marginTop: 1 },
  hangup: {
    width: 34,
    height: 34,
    borderRadius: 17,
    alignItems: "center",
    justifyContent: "center",
    backgroundColor: "#e53f64",
    marginLeft: 4
  },
  hangupIcon: { transform: [{ rotate: "135deg" }] },
  pressed: { opacity: 0.72 }
});
