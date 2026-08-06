import * as Haptics from "expo-haptics";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  AccessibilityInfo,
  Animated,
  Easing,
  Modal,
  Pressable,
  StyleSheet,
  Text,
  TextInput,
  View
} from "react-native";
import { getEngineerAccessStatus, verifyEngineerAccess } from "../../api/engineerAccess";
import { engineerDevFallbackEnabled } from "../../security/engineerAccessDevFallback";
import { emitEngineerAccessDiagnostic } from "../../security/engineerAccessDiagnostics";

const PASSCODE_LENGTH = 8;

type Props = {
  visible: boolean;
  userId: number;
  onCancel: () => void;
  onGranted: () => void;
};

type Phase = "entry" | "verifying" | "warning" | "locked";

/**
 * Secure passcode challenge for engineer access.
 *
 * The entered digits live in a single `useState` string and are wiped on every
 * exit path — cancel, failure, success, unmount. They are never written to a
 * ref that outlives the modal, never placed in navigation params, never logged,
 * and never included in the outcome object returned by the API layer.
 *
 * The modal renders over the Galactic Construction screen and never navigates,
 * so a cancelled or failed attempt leaves the underlying screen exactly as it
 * was, including its scroll and navigation state.
 */
export function EngineerAccessModal({ visible, userId, onCancel, onGranted }: Props) {
  const [passcode, setPasscode] = useState("");
  const [phase, setPhase] = useState<Phase>("entry");
  const [lockSeconds, setLockSeconds] = useState(0);
  const [needsReauth, setNeedsReauth] = useState(false);
  const [reduceMotion, setReduceMotion] = useState(false);
  const inputRef = useRef<TextInput>(null);

  const lift = useRef(new Animated.Value(0)).current;
  const shake = useRef(new Animated.Value(0)).current;

  useEffect(() => {
    let active = true;
    AccessibilityInfo.isReduceMotionEnabled().then((enabled) => active && setReduceMotion(Boolean(enabled)));
    const subscription = AccessibilityInfo.addEventListener("reduceMotionChanged", (enabled) =>
      setReduceMotion(Boolean(enabled))
    );
    return () => { active = false; subscription?.remove?.(); };
  }, []);

  // Entry motion: fade and lift, 300ms, collapsed to an instant state change
  // when Reduce Motion is on.
  useEffect(() => {
    if (!visible) return;
    lift.setValue(reduceMotion ? 1 : 0);
    if (reduceMotion) return;
    Animated.timing(lift, { toValue: 1, duration: 300, easing: Easing.out(Easing.cubic), useNativeDriver: true }).start();
  }, [visible, reduceMotion, lift]);

  /**
   * Ask the server for standing whenever the modal opens. This is what stops an
   * app restart from clearing a lockout: the countdown is the server's, and a
   * relaunch simply re-reads it.
   */
  useEffect(() => {
    if (!visible) return;
    setPasscode("");
    setPhase("entry");
    setLockSeconds(0);
    emitEngineerAccessDiagnostic({ stage: "modal_opened" });
    let active = true;
    getEngineerAccessStatus().then((status) => {
      if (!active) return;
      setNeedsReauth(status.requiresReauthentication);
      // A lockout is a server verdict about server attempts. When the local
      // development fallback is compiled in, the passcode it accepts is not one
      // of those attempts, so a countdown left over from earlier failures must
      // not hide the input and make a valid passcode unenterable.
      if (status.lockedSecondsRemaining > 0 && !engineerDevFallbackEnabled()) {
        setLockSeconds(status.lockedSecondsRemaining);
        setPhase("locked");
      }
    });
    return () => { active = false; };
  }, [visible]);

  // Lockout countdown.
  useEffect(() => {
    if (phase !== "locked" || lockSeconds <= 0) return;
    const timer = setTimeout(() => {
      const next = lockSeconds - 1;
      setLockSeconds(next);
      if (next <= 0) setPhase("entry");
    }, 1000);
    return () => clearTimeout(timer);
  }, [phase, lockSeconds]);

  // Announce the countdown periodically rather than every second, which would
  // flood VoiceOver and make the dialog unusable.
  useEffect(() => {
    if (phase !== "locked" || lockSeconds <= 0) return;
    if (lockSeconds % 15 === 0 || lockSeconds === 5) {
      AccessibilityInfo.announceForAccessibility(`Engineer access locked. ${lockSeconds} seconds remaining.`);
    }
  }, [phase, lockSeconds]);

  const runShake = useCallback(() => {
    if (reduceMotion) return;
    shake.setValue(0);
    Animated.sequence([
      Animated.timing(shake, { toValue: 1, duration: 60, useNativeDriver: true }),
      Animated.timing(shake, { toValue: -1, duration: 60, useNativeDriver: true }),
      Animated.timing(shake, { toValue: 0.6, duration: 60, useNativeDriver: true }),
      Animated.timing(shake, { toValue: 0, duration: 60, useNativeDriver: true })
    ]).start();
  }, [reduceMotion, shake]);

  const canVerify = passcode.length === PASSCODE_LENGTH && phase === "entry";

  const submit = useCallback(async () => {
    if (!canVerify) return;
    setPhase("verifying");
    const attempt = passcode;
    emitEngineerAccessDiagnostic({ stage: "input_length", inputLength: attempt.length });
    // Clear the field before awaiting so the digits are not sitting in state
    // across the network round trip.
    setPasscode("");
    const outcome = await verifyEngineerAccess(userId, attempt);

    if (outcome.authorized) {
      // Reset before handing control back: the host closes the modal, and a
      // later reopen must not find "verifying" or a leftover countdown.
      setPhase("entry");
      setLockSeconds(0);
      await Haptics.notificationAsync(Haptics.NotificationFeedbackType.Success).catch(() => undefined);
      onGranted();
      return;
    }

    await Haptics.notificationAsync(Haptics.NotificationFeedbackType.Error).catch(() => undefined);
    runShake();
    setNeedsReauth(outcome.requiresReauthentication);
    if (outcome.retryAfterSeconds > 0) {
      setLockSeconds(outcome.retryAfterSeconds);
      setPhase("locked");
      return;
    }
    setPhase("warning");
  }, [canVerify, passcode, userId, onGranted, runShake]);

  const dismiss = useCallback(() => {
    setPasscode("");
    setPhase("entry");
    onCancel();
  }, [onCancel]);

  const translateY = lift.interpolate({ inputRange: [0, 1], outputRange: [24, 0] });
  const translateX = shake.interpolate({ inputRange: [-1, 1], outputRange: [-9, 9] });

  const dots = useMemo(
    () => Array.from({ length: PASSCODE_LENGTH }, (_, index) => index < passcode.length),
    [passcode.length]
  );

  return (
    <Modal visible={visible} transparent animationType={reduceMotion ? "none" : "fade"} onRequestClose={dismiss} statusBarTranslucent>
      <View style={styles.backdrop}>
        <Animated.View
          style={[styles.card, { opacity: lift, transform: [{ translateY }, { translateX }] }]}
          accessibilityViewIsModal
          accessibilityRole="alert"
        >
          {phase === "warning" ? (
            <WarningPanel
              onAcknowledge={() => { setPhase("entry"); setPasscode(""); inputRef.current?.focus(); }}
            />
          ) : phase === "locked" ? (
            <LockoutPanel seconds={lockSeconds} needsReauth={needsReauth} onReturn={dismiss} />
          ) : (
            <>
              <Text style={styles.title} accessibilityRole="header">Engineer Access</Text>
              <Text style={styles.subtitle}>Enter the engineer passcode to continue.</Text>

              <Pressable onPress={() => inputRef.current?.focus()} style={styles.dotRow} accessible={false} importantForAccessibility="no-hide-descendants">
                {dots.map((filled, index) => (
                  <View key={index} style={[styles.dot, filled && styles.dotFilled]} />
                ))}
              </Pressable>

              <TextInput
                ref={inputRef}
                value={passcode}
                onChangeText={(text) => setPasscode(text.replace(/\D/g, "").slice(0, PASSCODE_LENGTH))}
                keyboardType="number-pad"
                inputMode="numeric"
                secureTextEntry
                autoFocus
                maxLength={PASSCODE_LENGTH}
                editable={phase === "entry"}
                caretHidden
                // contextMenuHidden blocks the copy/paste callout; disabling
                // autofill and correction keeps the digits out of the keyboard's
                // learned-text store and out of iOS password autofill.
                contextMenuHidden
                autoCorrect={false}
                autoComplete="off"
                textContentType="none"
                importantForAutofill="no"
                spellCheck={false}
                selectTextOnFocus={false}
                style={styles.hiddenInput}
                accessibilityLabel="Engineer passcode"
                accessibilityHint={`Enter the ${PASSCODE_LENGTH} digit engineer passcode. Digits are hidden.`}
                onSubmitEditing={submit}
              />

              <View style={styles.actions}>
                <Pressable
                  onPress={dismiss}
                  accessibilityRole="button"
                  accessibilityLabel="Cancel"
                  style={({ pressed }) => [styles.action, styles.cancel, pressed && styles.pressed]}
                >
                  <Text style={styles.cancelText}>Cancel</Text>
                </Pressable>
                <Pressable
                  onPress={submit}
                  disabled={!canVerify}
                  accessibilityRole="button"
                  accessibilityLabel="Verify Access"
                  accessibilityState={{ disabled: !canVerify }}
                  style={({ pressed }) => [styles.action, styles.verify, !canVerify && styles.verifyDisabled, pressed && styles.pressed]}
                >
                  <Text style={[styles.verifyText, !canVerify && styles.verifyTextDisabled]}>
                    {phase === "verifying" ? "Verifying…" : "Verify Access"}
                  </Text>
                </Pressable>
              </View>
            </>
          )}
        </Animated.View>
      </View>
    </Modal>
  );
}

/**
 * Strict denial. Says only that the attempt failed — never which check failed,
 * how many digits matched, or whether the value was close.
 */
function WarningPanel({ onAcknowledge }: { onAcknowledge: () => void }) {
  useEffect(() => {
    AccessibilityInfo.announceForAccessibility("Warning. Access denied. Unauthorized engineer access attempt detected.");
  }, []);
  return (
    <View style={styles.danger}>
      {/* The warning icon and the word "Warning" carry the meaning; colour is
          reinforcement only, so the state survives a colour-vision difference. */}
      <Text style={styles.dangerIcon} accessibilityElementsHidden importantForAccessibility="no">⚠</Text>
      <Text style={styles.dangerTitle} accessibilityRole="header">Warning: Access Denied</Text>
      <Text style={styles.dangerBody}>
        Unauthorized engineer access attempt detected. This protected system is monitored.
        Continued failed attempts will temporarily disable access.
      </Text>
      <Pressable
        onPress={onAcknowledge}
        accessibilityRole="button"
        accessibilityLabel="Understood"
        style={({ pressed }) => [styles.action, styles.understood, pressed && styles.pressed]}
      >
        <Text style={styles.understoodText}>Understood</Text>
      </Pressable>
    </View>
  );
}

function LockoutPanel({ seconds, needsReauth, onReturn }: { seconds: number; needsReauth: boolean; onReturn: () => void }) {
  const label = seconds > 60 ? `${Math.ceil(seconds / 60)} minutes` : `${seconds} seconds`;
  return (
    <View style={styles.danger}>
      <Text style={styles.dangerIcon} accessibilityElementsHidden importantForAccessibility="no">⏳</Text>
      <Text style={styles.dangerTitle} accessibilityRole="header">Engineer Access Temporarily Locked</Text>
      <Text style={styles.dangerBody}>
        Too many unsuccessful access attempts were detected. Try again after the security timer expires.
      </Text>
      <Text
        style={styles.countdown}
        accessibilityLabel={`Locked. ${label} remaining.`}
        accessibilityLiveRegion="polite"
      >
        {label}
      </Text>
      {needsReauth ? (
        <Text style={styles.dangerBody}>Sign out and sign in again to request access.</Text>
      ) : null}
      <Pressable
        onPress={onReturn}
        accessibilityRole="button"
        accessibilityLabel="Return"
        style={({ pressed }) => [styles.action, styles.understood, pressed && styles.pressed]}
      >
        <Text style={styles.understoodText}>Return</Text>
      </Pressable>
    </View>
  );
}

const styles = StyleSheet.create({
  backdrop: { flex: 1, backgroundColor: "rgba(2,5,14,0.82)", alignItems: "center", justifyContent: "center", paddingHorizontal: 24 },
  card: { width: "100%", maxWidth: 380, borderRadius: 22, paddingVertical: 26, paddingHorizontal: 22, backgroundColor: "#0A1024", borderWidth: 1, borderColor: "#2B3566" },
  title: { color: "#FFFFFF", fontSize: 20, fontWeight: "900", textAlign: "center" },
  subtitle: { color: "#AEBBD2", fontSize: 14, lineHeight: 20, textAlign: "center", marginTop: 8 },
  dotRow: { flexDirection: "row", justifyContent: "center", gap: 10, marginTop: 24, marginBottom: 8, flexWrap: "wrap" },
  dot: { width: 14, height: 14, borderRadius: 7, borderWidth: 1.4, borderColor: "#4C5A93", backgroundColor: "transparent" },
  dotFilled: { backgroundColor: "#6FE5FF", borderColor: "#6FE5FF" },
  // Kept on-screen but visually collapsed: an input with display:none or zero
  // opacity loses focus on some Android builds and cannot be reached by the
  // keyboard, so it is sized to a hairline instead of hidden outright.
  hiddenInput: { height: 1, width: 1, opacity: 0.01, color: "transparent", padding: 0 },
  actions: { flexDirection: "row", gap: 12, marginTop: 22 },
  action: { flex: 1, minHeight: 48, borderRadius: 24, alignItems: "center", justifyContent: "center", paddingHorizontal: 14 },
  cancel: { borderWidth: 1.4, borderColor: "#3A4676" },
  cancelText: { color: "#C3CEE6", fontSize: 15, fontWeight: "800" },
  verify: { backgroundColor: "#6FE5FF" },
  verifyDisabled: { backgroundColor: "#1B2445" },
  verifyText: { color: "#04101F", fontSize: 15, fontWeight: "900" },
  verifyTextDisabled: { color: "#5D6B95" },
  pressed: { opacity: 0.82, transform: [{ scale: 0.985 }] },
  danger: { alignItems: "center" },
  dangerIcon: { fontSize: 34, marginBottom: 8 },
  dangerTitle: { color: "#FFD9C2", fontSize: 18, fontWeight: "900", textAlign: "center" },
  dangerBody: { color: "#E4CFC6", fontSize: 14, lineHeight: 21, textAlign: "center", marginTop: 10 },
  countdown: { color: "#FF9A6B", fontSize: 26, fontWeight: "900", marginTop: 14, letterSpacing: 1 },
  understood: { marginTop: 20, alignSelf: "stretch", backgroundColor: "#FF7A4D" },
  understoodText: { color: "#1A0B04", fontSize: 15, fontWeight: "900" }
});

export const ENGINEER_PASSCODE_LENGTH = PASSCODE_LENGTH;
