/**
 * Private Office lock gate — the one component that stands between a screen
 * and Office content.
 *
 * ## What this gate does and does not decide
 *
 * The server refuses Office data without a valid unlock grant no matter what
 * this component renders (Stage 15's 423). The gate exists so that refusal is
 * never the user's first contact with the lock: it asks
 * `/security/status` up front and draws the correct door — first-run setup
 * (Stage 1), the unlock screen (Stage 23), or the content itself. A build with
 * this file deleted still leaks nothing; it just shows worse errors.
 *
 * ## The doors
 *
 *   CHECKING     status request in flight; neutral splash, no content behind it
 *   UNAVAILABLE  we could not look — a 503 or a dead network. Retry is honest
 *                here, because retrying is the thing that might work.
 *   UPGRADE_REQ  we looked, and the answer was no: the membership does not
 *                reach the Office. Offers renewal, never a retry — see the
 *                note on `GateDoor` for why this must not share a door with
 *                UNAVAILABLE.
 *   SETUP        no passcode exists yet — the 4-step first-entry flow.
 *                "Not now" exits the Office entirely (Stage 1: the lock is not
 *                optional; entering without one is).
 *   LOCKED       passcode exists, no live grant — passcode field, Face ID
 *                button when armed, cooldown countdown, forgot-passcode reset
 *   UNLOCKED     children render
 *
 * ## Lifecycle (Stages 6, 19, 20)
 *
 * An AppState listener stamps background/foreground through
 * `noteOfficeBackgrounded` / `noteOfficeForegrounded`, so the relock preference
 * is enforced even while the user is elsewhere in the app. While the app is
 * not active, an opaque overlay covers the subtree — the app-switcher snapshot
 * shows a lock mark, not a net worth. Deep links need no special casing: both
 * Office screens render inside this gate, so a cold link lands on the correct
 * door and the destination resumes after unlock (Stage 19).
 *
 * ## Face ID is a shortcut to the same door (Stages 7-8)
 *
 * The Face ID button reads the office passcode from the biometry-gated
 * keychain item and submits it to `/unlock` like a typed one. A wrong-passcode
 * answer from that path means the credential is stale (passcode changed on
 * another device), so it is disarmed rather than retried.
 */

import { ReactNode, useCallback, useEffect, useRef, useState, useSyncExternalStore } from "react";
import {
  ActivityIndicator,
  AppState,
  AppStateStatus,
  Keyboard,
  KeyboardAvoidingView,
  Modal,
  Platform,
  Pressable,
  ScrollView,
  StyleSheet,
  Text,
  TextInput,
  View
} from "react-native";
import { Ionicons } from "@expo/vector-icons";
import {
  OfficeSecurityStatus,
  OfficeSecurityWriteResult,
  getOfficeSecurityStatus,
  resetOfficePasscode,
  setOfficeBiometricPreference,
  setupOfficePasscode,
  unlockOffice
} from "../api/privateOffice";
import { useTranslation } from "../i18n";
import { useAuth } from "../session/auth";
import { getBiometricCapability } from "../session/biometricAuth";
import { getSessionEnvelope } from "../session/sessionStore";
import { colors } from "../theme/colors";
import {
  disableOfficeBiometric,
  enableOfficeBiometric,
  getOfficeBiometricUserId,
  isOfficeUnlocked,
  getOfficeLockSnapshot,
  noteOfficeBackgrounded,
  noteOfficeForegrounded,
  readOfficeBiometricPasscode,
  reconcileOfficeOwner,
  subscribeOfficeLock
} from "./officeLock";

/** Client-side mirror of the server's minimum; the server remains the judge. */
const MIN_PASSCODE_DIGITS = 6;
const PASSCODE_MAX_LENGTH = 12;

/**
 * `UPGRADE_REQUIRED` is a separate door from `UNAVAILABLE` on purpose. A lapsed
 * membership and an unreachable server need different sentences and different
 * buttons: retrying a 403 can never succeed, and offering "Try again" to
 * someone whose subscription expired is a dead end that hides the real reason.
 */
type GateDoor =
  | "CHECKING"
  | "UNAVAILABLE"
  | "UPGRADE_REQUIRED"
  | "SETUP"
  | "LOCKED"
  | "UNLOCKED";

type SetupStep = "INTRO" | "CREATE" | "CONFIRM" | "BIOMETRIC";

type Props = {
  children: ReactNode;
  /** "Not now" on first-entry setup. Screens pass `navigation.goBack`. */
  onDismiss?: () => void;
  /**
   * Where "Renew membership" goes when the server refuses with 403. Optional:
   * without it the upgrade door still states the real reason, it just omits
   * the button rather than rendering one that goes nowhere.
   */
  onRenew?: () => void;
};

function digitsOnly(value: string): string {
  return value.replace(/[^0-9]/g, "").slice(0, PASSCODE_MAX_LENGTH);
}

export function PrivateOfficeLockGate({ children, onDismiss, onRenew }: Props) {
  const { t } = useTranslation();
  const { authState } = useAuth();
  const lock = useSyncExternalStore(subscribeOfficeLock, getOfficeLockSnapshot);
  // The envelope disappears when the bearer session dies, but the member can
  // still be signed in via the web cookie — the auth context knows who they
  // are either way. Without this fallback, reconcileOfficeOwner(0) relocks
  // the Office on every gate mount for a cookie-authenticated member.
  const authUserId = Number(authState.user?.user_id ?? 0);

  const [door, setDoor] = useState<GateDoor>("CHECKING");
  const [userId, setUserId] = useState(0);
  const [busy, setBusy] = useState(false);
  const [passcode, setPasscode] = useState("");
  const [failure, setFailure] = useState("");
  const [cooldown, setCooldown] = useState(0);
  const [biometricArmed, setBiometricArmed] = useState(false);
  const [biometricKind, setBiometricKind] = useState<"faceId" | "touchId" | "none">("none");
  const [appActive, setAppActive] = useState(AppState.currentState === "active");

  // Setup flow
  const [setupStep, setSetupStep] = useState<SetupStep>("INTRO");
  const [setupPasscode, setSetupPasscode] = useState("");
  const [setupConfirm, setSetupConfirm] = useState("");

  // Forgot-passcode reset
  const [resetOpen, setResetOpen] = useState(false);
  const [resetPassword, setResetPassword] = useState("");
  const [resetNew, setResetNew] = useState("");
  const [resetConfirm, setResetConfirm] = useState("");
  const [resetFailure, setResetFailure] = useState("");

  const mounted = useRef(true);
  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
    };
  }, []);

  const failureText = useCallback(
    (result: OfficeSecurityWriteResult | { state: "UNLOCKED" }): string => {
      switch (result.state) {
        case "OK":
        case "UNLOCKED":
          return "";
        case "WRONG_PASSCODE":
          return t("premium:privateOffice.lock.wrong");
        case "COOLDOWN":
          return t("premium:privateOffice.lock.cooldown", {
            seconds: Math.max(1, result.retryAfterSeconds)
          });
        case "POLICY":
          return t("premium:privateOffice.lock.policy", { digits: MIN_PASSCODE_DIGITS });
        case "REVERIFY_FAILED":
          return t("premium:privateOffice.lock.reset.failed");
        case "UNAVAILABLE":
          return t("premium:privateOffice.lock.unavailable.body");
        default:
          return t("premium:privateOffice.lock.error");
      }
    },
    [t]
  );

  const applyStatus = useCallback(
    (status: OfficeSecurityStatus, currentUserId: number) => {
      if (!mounted.current) return;
      setCooldown(status.cooldownSeconds);
      if (status.state === "UPGRADE_REQUIRED") {
        setDoor("UPGRADE_REQUIRED");
        return;
      }
      if (status.state === "UNAVAILABLE") {
        setDoor("UNAVAILABLE");
        return;
      }
      if (!status.passcodeSet) {
        setSetupStep("INTRO");
        setDoor("SETUP");
        return;
      }
      setDoor(isOfficeUnlocked(currentUserId) ? "UNLOCKED" : "LOCKED");
    },
    []
  );

  const check = useCallback(async () => {
    setDoor("CHECKING");
    const envelope = await getSessionEnvelope();
    const currentUserId = envelope?.userId ?? authUserId;
    if (mounted.current) setUserId(currentUserId);
    reconcileOfficeOwner(currentUserId);
    const [status, armedFor, capability] = await Promise.all([
      getOfficeSecurityStatus(),
      getOfficeBiometricUserId(),
      getBiometricCapability()
    ]);
    if (!mounted.current) return;
    setBiometricArmed(Boolean(armedFor && armedFor === currentUserId && capability.available));
    setBiometricKind(
      capability.kind === "faceId" || capability.kind === "touchId" ? capability.kind : "none"
    );
    applyStatus(status, currentUserId);
  }, [applyStatus, authUserId]);

  useEffect(() => {
    void check();
  }, [check]);

  // The store is the authority on unlocked-ness; the gate follows it both ways
  // so a relock (background timer, account switch, manual Lock) flips the door
  // without a network round trip.
  useEffect(() => {
    if (door === "UNLOCKED" && !lock.unlocked) setDoor("LOCKED");
    if (door === "LOCKED" && isOfficeUnlocked(userId)) setDoor("UNLOCKED");
  }, [door, lock, userId]);

  // Stage 6 + 20: relock lifecycle and snapshot privacy.
  useEffect(() => {
    const onChange = (next: AppStateStatus) => {
      setAppActive(next === "active");
      if (next === "active") void noteOfficeForegrounded();
      else void noteOfficeBackgrounded();
    };
    const subscription = AppState.addEventListener("change", onChange);
    return () => subscription.remove();
  }, []);

  // Server-clock countdown. Rendering the server's number, decremented locally
  // for display only — a fresh attempt still gets the server's answer.
  useEffect(() => {
    if (cooldown <= 0) return;
    const timer = setInterval(() => {
      setCooldown((current) => (current > 1 ? current - 1 : 0));
    }, 1000);
    return () => clearInterval(timer);
  }, [cooldown]);

  const submitUnlock = useCallback(
    async (candidate: string, viaBiometric: boolean) => {
      if (!candidate || busy) return;
      setBusy(true);
      setFailure("");
      try {
        const result = await unlockOffice(candidate, userId);
        if (!mounted.current) return;
        if (result.state === "UNLOCKED") {
          setPasscode("");
          setDoor("UNLOCKED");
          return;
        }
        if (result.state === "COOLDOWN") setCooldown(Math.max(1, result.retryAfterSeconds));
        if (result.state === "WRONG_PASSCODE" && viaBiometric) {
          // Stale credential — the passcode changed since Face ID was armed.
          await disableOfficeBiometric();
          if (mounted.current) setBiometricArmed(false);
        }
        if (result.state === "NOT_SET") {
          // Reset happened elsewhere; fall back to setup.
          setSetupStep("INTRO");
          setDoor("SETUP");
          return;
        }
        setFailure(failureText(result));
      } finally {
        if (mounted.current) setBusy(false);
      }
    },
    [busy, failureText, userId]
  );

  const unlockWithBiometric = useCallback(async () => {
    if (busy) return;
    const read = await readOfficeBiometricPasscode(
      userId,
      t("premium:privateOffice.lock.biometricPrompt")
    );
    if (!mounted.current) return;
    if (read.status === "unlocked") {
      await submitUnlock(read.passcode, true);
      return;
    }
    if (read.status === "missing") setBiometricArmed(false);
    // "denied" (including cancel) is not an error worth a banner.
  }, [busy, submitUnlock, t, userId]);

  const submitSetup = useCallback(async () => {
    if (busy) return;
    if (setupPasscode !== setupConfirm) {
      setFailure(t("premium:privateOffice.lock.setup.mismatch"));
      setSetupConfirm("");
      setSetupStep("CONFIRM");
      return;
    }
    setBusy(true);
    setFailure("");
    try {
      const result = await setupOfficePasscode(setupPasscode, setupConfirm);
      if (!mounted.current) return;
      if (result.state === "OK") {
        // Unlock immediately with the passcode just created, then offer Face ID.
        const unlocked = await unlockOffice(setupPasscode, userId);
        if (!mounted.current) return;
        if (unlocked.state !== "UNLOCKED") {
          setFailure(failureText(unlocked));
          setDoor("LOCKED");
          return;
        }
        if (biometricKind !== "none") {
          setSetupStep("BIOMETRIC");
        } else {
          setSetupPasscode("");
          setSetupConfirm("");
          setDoor("UNLOCKED");
        }
        return;
      }
      if (result.state === "ALREADY_SET") {
        // Set up on another device meanwhile — this door is now the lock.
        setDoor("LOCKED");
        return;
      }
      setFailure(failureText(result));
      setSetupPasscode("");
      setSetupConfirm("");
      setSetupStep("CREATE");
    } finally {
      if (mounted.current) setBusy(false);
    }
  }, [biometricKind, busy, failureText, setupConfirm, setupPasscode, t, userId]);

  const finishSetupBiometric = useCallback(
    async (enable: boolean) => {
      if (busy) return;
      setBusy(true);
      try {
        if (enable) {
          const armed = await enableOfficeBiometric(userId, setupPasscode);
          if (armed) {
            setBiometricArmed(true);
            await setOfficeBiometricPreference(true);
          }
        } else {
          await setOfficeBiometricPreference(false);
        }
      } finally {
        if (mounted.current) {
          setSetupPasscode("");
          setSetupConfirm("");
          setBusy(false);
          setDoor("UNLOCKED");
        }
      }
    },
    [busy, setupPasscode, userId]
  );

  const submitReset = useCallback(async () => {
    if (busy) return;
    if (resetNew !== resetConfirm) {
      setResetFailure(t("premium:privateOffice.lock.setup.mismatch"));
      return;
    }
    setBusy(true);
    setResetFailure("");
    try {
      const result = await resetOfficePasscode(resetPassword, resetNew, resetConfirm);
      if (!mounted.current) return;
      if (result.state === "OK") {
        // Reset revokes all grants and disarms nothing locally by itself; the
        // stored Face ID credential now holds the old passcode, so drop it.
        await disableOfficeBiometric();
        setBiometricArmed(false);
        const unlocked = await unlockOffice(resetNew, userId);
        if (!mounted.current) return;
        setResetOpen(false);
        setResetPassword("");
        setResetNew("");
        setResetConfirm("");
        setDoor(unlocked.state === "UNLOCKED" ? "UNLOCKED" : "LOCKED");
        return;
      }
      if (result.state === "COOLDOWN") {
        setResetFailure(
          t("premium:privateOffice.lock.cooldown", { seconds: Math.max(1, result.retryAfterSeconds) })
        );
        return;
      }
      setResetFailure(failureText(result));
    } finally {
      if (mounted.current) setBusy(false);
    }
  }, [busy, failureText, resetConfirm, resetNew, resetPassword, t, userId]);

  const biometricLabel =
    biometricKind === "touchId"
      ? t("premium:privateOffice.lock.biometricTouchId")
      : t("premium:privateOffice.lock.biometricFaceId");

  const canSubmitUnlock = passcode.length >= MIN_PASSCODE_DIGITS && cooldown <= 0 && !busy;

  /* --- render ------------------------------------------------------------- */

  const privacyOverlay = !appActive ? (
    <View style={styles.privacyOverlay} pointerEvents="none">
      <Ionicons name="lock-closed" size={34} color={colors.accent} />
    </View>
  ) : null;

  if (door === "UNLOCKED") {
    return (
      <View style={styles.root}>
        {children}
        {privacyOverlay}
      </View>
    );
  }

  return (
    <View style={styles.root}>
      <ScrollView
        style={styles.root}
        contentContainerStyle={styles.content}
        keyboardShouldPersistTaps="handled"
      >
        {door === "CHECKING" ? (
          <View style={styles.panel} accessibilityRole="progressbar">
            <ActivityIndicator color={colors.accent} />
            <Text style={styles.panelText}>{t("premium:privateOffice.lock.checking")}</Text>
          </View>
        ) : null}

        {door === "UNAVAILABLE" ? (
          <View style={styles.panel}>
            <Ionicons name="cloud-offline-outline" size={22} color={colors.warning} />
            <Text style={styles.panelTitle}>{t("premium:privateOffice.lock.unavailable.title")}</Text>
            <Text style={styles.panelText}>{t("premium:privateOffice.lock.unavailable.body")}</Text>
            <Pressable style={styles.retry} onPress={() => void check()} accessibilityRole="button">
              <Text style={styles.retryText}>{t("premium:privateOffice.retry")}</Text>
            </Pressable>
          </View>
        ) : null}

        {door === "UPGRADE_REQUIRED" ? (
          <View style={styles.panel}>
            <Ionicons name="lock-closed-outline" size={26} color={colors.accent} />
            <Text style={styles.panelTitle}>{t("premium:privateOffice.lock.upgrade.title")}</Text>
            <Text style={styles.panelText}>{t("premium:privateOffice.lock.upgrade.body")}</Text>
            {onRenew ? (
              <Pressable style={styles.primaryButton} onPress={onRenew} accessibilityRole="button">
                <Text style={styles.primaryButtonText}>
                  {t("premium:privateOffice.lock.upgrade.action")}
                </Text>
              </Pressable>
            ) : null}
            {onDismiss ? (
              <Pressable style={styles.linkButton} onPress={onDismiss} accessibilityRole="button">
                <Text style={styles.linkText}>
                  {t("premium:privateOffice.lock.setup.intro.notNow")}
                </Text>
              </Pressable>
            ) : null}
          </View>
        ) : null}

        {door === "SETUP" && setupStep === "INTRO" ? (
          <View style={styles.panel}>
            <Ionicons name="shield-checkmark-outline" size={26} color={colors.accent} />
            <Text style={styles.panelTitle}>{t("premium:privateOffice.lock.setup.intro.title")}</Text>
            <Text style={styles.panelText}>{t("premium:privateOffice.lock.setup.intro.body")}</Text>
            <Pressable
              style={styles.primaryButton}
              onPress={() => {
                setFailure("");
                setSetupStep("CREATE");
              }}
              accessibilityRole="button"
            >
              <Text style={styles.primaryButtonText}>
                {t("premium:privateOffice.lock.setup.intro.start")}
              </Text>
            </Pressable>
            {onDismiss ? (
              <Pressable style={styles.linkButton} onPress={onDismiss} accessibilityRole="button">
                <Text style={styles.linkText}>
                  {t("premium:privateOffice.lock.setup.intro.notNow")}
                </Text>
              </Pressable>
            ) : null}
          </View>
        ) : null}

        {door === "SETUP" && (setupStep === "CREATE" || setupStep === "CONFIRM") ? (
          <View style={styles.panel}>
            <Ionicons name="keypad-outline" size={24} color={colors.accent} />
            <Text style={styles.panelTitle}>
              {setupStep === "CREATE"
                ? t("premium:privateOffice.lock.setup.create.title")
                : t("premium:privateOffice.lock.setup.confirm.title")}
            </Text>
            <Text style={styles.panelText}>
              {setupStep === "CREATE"
                ? t("premium:privateOffice.lock.setup.create.body", { digits: MIN_PASSCODE_DIGITS })
                : t("premium:privateOffice.lock.setup.confirm.body")}
            </Text>
            <TextInput
              key={setupStep}
              style={styles.passcodeInput}
              value={setupStep === "CREATE" ? setupPasscode : setupConfirm}
              onChangeText={(value) => {
                setFailure("");
                if (setupStep === "CREATE") setSetupPasscode(digitsOnly(value));
                else setSetupConfirm(digitsOnly(value));
              }}
              keyboardType="number-pad"
              secureTextEntry
              autoFocus
              maxLength={PASSCODE_MAX_LENGTH}
              accessibilityLabel={t("premium:privateOffice.lock.placeholder")}
              placeholder={t("premium:privateOffice.lock.placeholder")}
              placeholderTextColor={colors.muted}
            />
            {failure ? <Text style={styles.failureText}>{failure}</Text> : null}
            <Pressable
              style={[
                styles.primaryButton,
                (setupStep === "CREATE" ? setupPasscode : setupConfirm).length <
                MIN_PASSCODE_DIGITS
                  ? styles.buttonDisabled
                  : null
              ]}
              disabled={
                busy ||
                (setupStep === "CREATE" ? setupPasscode : setupConfirm).length <
                  MIN_PASSCODE_DIGITS
              }
              onPress={() => {
                if (setupStep === "CREATE") {
                  setSetupConfirm("");
                  setSetupStep("CONFIRM");
                } else {
                  void submitSetup();
                }
              }}
              accessibilityRole="button"
            >
              {busy ? (
                <ActivityIndicator color={colors.background} />
              ) : (
                <Text style={styles.primaryButtonText}>
                  {t("premium:privateOffice.lock.setup.continue")}
                </Text>
              )}
            </Pressable>
            <Pressable
              style={styles.linkButton}
              onPress={() => {
                setFailure("");
                if (setupStep === "CONFIRM") {
                  setSetupConfirm("");
                  setSetupStep("CREATE");
                } else {
                  setSetupPasscode("");
                  setSetupStep("INTRO");
                }
              }}
              accessibilityRole="button"
            >
              <Text style={styles.linkText}>{t("premium:privateOffice.lock.setup.back")}</Text>
            </Pressable>
          </View>
        ) : null}

        {door === "SETUP" && setupStep === "BIOMETRIC" ? (
          <View style={styles.panel}>
            <Ionicons
              name={biometricKind === "touchId" ? "finger-print-outline" : "scan-outline"}
              size={26}
              color={colors.accent}
            />
            <Text style={styles.panelTitle}>
              {t("premium:privateOffice.lock.setup.biometric.title", { method: biometricLabel })}
            </Text>
            <Text style={styles.panelText}>
              {t("premium:privateOffice.lock.setup.biometric.body")}
            </Text>
            <Pressable
              style={styles.primaryButton}
              disabled={busy}
              onPress={() => void finishSetupBiometric(true)}
              accessibilityRole="button"
            >
              <Text style={styles.primaryButtonText}>
                {t("premium:privateOffice.lock.setup.biometric.enable", { method: biometricLabel })}
              </Text>
            </Pressable>
            <Pressable
              style={styles.linkButton}
              disabled={busy}
              onPress={() => void finishSetupBiometric(false)}
              accessibilityRole="button"
            >
              <Text style={styles.linkText}>
                {t("premium:privateOffice.lock.setup.biometric.skip")}
              </Text>
            </Pressable>
          </View>
        ) : null}

        {door === "LOCKED" ? (
          <View style={styles.panel}>
            <Ionicons name="lock-closed-outline" size={26} color={colors.accent} />
            <Text style={styles.panelTitle}>{t("premium:privateOffice.lock.locked.title")}</Text>
            <Text style={styles.panelText}>{t("premium:privateOffice.lock.locked.body")}</Text>
            <TextInput
              style={styles.passcodeInput}
              value={passcode}
              onChangeText={(value) => {
                setFailure("");
                setPasscode(digitsOnly(value));
              }}
              keyboardType="number-pad"
              secureTextEntry
              maxLength={PASSCODE_MAX_LENGTH}
              editable={cooldown <= 0 && !busy}
              accessibilityLabel={t("premium:privateOffice.lock.placeholder")}
              placeholder={t("premium:privateOffice.lock.placeholder")}
              placeholderTextColor={colors.muted}
              onSubmitEditing={() => {
                if (canSubmitUnlock) void submitUnlock(passcode, false);
              }}
            />
            {cooldown > 0 ? (
              <Text style={styles.failureText}>
                {t("premium:privateOffice.lock.cooldown", { seconds: cooldown })}
              </Text>
            ) : failure ? (
              <Text style={styles.failureText}>{failure}</Text>
            ) : null}
            <Pressable
              style={[styles.primaryButton, !canSubmitUnlock ? styles.buttonDisabled : null]}
              disabled={!canSubmitUnlock}
              onPress={() => void submitUnlock(passcode, false)}
              accessibilityRole="button"
            >
              {busy ? (
                <ActivityIndicator color={colors.background} />
              ) : (
                <Text style={styles.primaryButtonText}>{t("premium:privateOffice.lock.unlock")}</Text>
              )}
            </Pressable>
            {biometricArmed ? (
              <Pressable
                style={styles.secondaryButton}
                disabled={busy || cooldown > 0}
                onPress={() => void unlockWithBiometric()}
                accessibilityRole="button"
              >
                <Ionicons
                  name={biometricKind === "touchId" ? "finger-print-outline" : "scan-outline"}
                  size={16}
                  color={colors.accentStrong}
                />
                <Text style={styles.secondaryButtonText}>{biometricLabel}</Text>
              </Pressable>
            ) : null}
            <Pressable
              style={styles.linkButton}
              onPress={() => {
                setResetFailure("");
                setResetOpen(true);
              }}
              accessibilityRole="button"
            >
              <Text style={styles.linkText}>{t("premium:privateOffice.lock.forgot")}</Text>
            </Pressable>
          </View>
        ) : null}
      </ScrollView>

      <Modal
        visible={resetOpen}
        transparent
        animationType="slide"
        onRequestClose={() => setResetOpen(false)}
      >
        <KeyboardAvoidingView
          style={styles.sheetBackdrop}
          behavior={Platform.OS === "ios" ? "padding" : undefined}
        >
          {/* The number-pad has no Done key on iOS; tapping above the sheet is
              the only way to lower the keyboard and reach the buttons. */}
          <Pressable
            style={styles.sheetBackdropDismiss}
            onPress={() => Keyboard.dismiss()}
            accessible={false}
          />
          <View style={styles.sheet}>
            <Text style={styles.sheetTitle}>{t("premium:privateOffice.lock.reset.title")}</Text>
            <Text style={styles.panelText}>{t("premium:privateOffice.lock.reset.body")}</Text>
            <TextInput
              style={styles.textInput}
              value={resetPassword}
              onChangeText={(value) => {
                setResetFailure("");
                setResetPassword(value);
              }}
              secureTextEntry
              autoCapitalize="none"
              accessibilityLabel={t("premium:privateOffice.lock.reset.password")}
              placeholder={t("premium:privateOffice.lock.reset.password")}
              placeholderTextColor={colors.muted}
            />
            <TextInput
              style={styles.textInput}
              value={resetNew}
              onChangeText={(value) => {
                setResetFailure("");
                setResetNew(digitsOnly(value));
              }}
              keyboardType="number-pad"
              secureTextEntry
              maxLength={PASSCODE_MAX_LENGTH}
              accessibilityLabel={t("premium:privateOffice.lock.reset.newPasscode")}
              placeholder={t("premium:privateOffice.lock.reset.newPasscode")}
              placeholderTextColor={colors.muted}
            />
            <TextInput
              style={styles.textInput}
              value={resetConfirm}
              onChangeText={(value) => {
                setResetFailure("");
                setResetConfirm(digitsOnly(value));
              }}
              keyboardType="number-pad"
              secureTextEntry
              maxLength={PASSCODE_MAX_LENGTH}
              accessibilityLabel={t("premium:privateOffice.lock.reset.confirmPasscode")}
              placeholder={t("premium:privateOffice.lock.reset.confirmPasscode")}
              placeholderTextColor={colors.muted}
            />
            {resetFailure ? <Text style={styles.failureText}>{resetFailure}</Text> : null}
            <Pressable
              style={[
                styles.primaryButton,
                !resetPassword || resetNew.length < MIN_PASSCODE_DIGITS || busy
                  ? styles.buttonDisabled
                  : null
              ]}
              disabled={!resetPassword || resetNew.length < MIN_PASSCODE_DIGITS || busy}
              onPress={() => void submitReset()}
              accessibilityRole="button"
            >
              {busy ? (
                <ActivityIndicator color={colors.background} />
              ) : (
                <Text style={styles.primaryButtonText}>
                  {t("premium:privateOffice.lock.reset.submit")}
                </Text>
              )}
            </Pressable>
            <Pressable
              style={styles.linkButton}
              onPress={() => setResetOpen(false)}
              accessibilityRole="button"
            >
              <Text style={styles.linkText}>{t("premium:privateOffice.lock.reset.cancel")}</Text>
            </Pressable>
          </View>
        </KeyboardAvoidingView>
      </Modal>

      {privacyOverlay}
    </View>
  );
}

const styles = StyleSheet.create({
  root: { flex: 1, backgroundColor: colors.background },
  content: { padding: 18, gap: 16, flexGrow: 1, justifyContent: "center" },
  panel: {
    backgroundColor: colors.surface,
    borderColor: colors.border,
    borderWidth: 1,
    borderRadius: 16,
    padding: 20,
    gap: 10,
    alignItems: "stretch"
  },
  panelTitle: { color: colors.text, fontSize: 17, fontWeight: "800" },
  panelText: { color: colors.muted, fontSize: 13, lineHeight: 19 },
  failureText: { color: colors.danger, fontSize: 13, fontWeight: "600" },
  passcodeInput: {
    backgroundColor: colors.surfaceRaised,
    borderColor: colors.border,
    borderWidth: 1,
    borderRadius: 12,
    paddingHorizontal: 16,
    paddingVertical: 12,
    color: colors.text,
    fontSize: 22,
    letterSpacing: 8,
    textAlign: "center"
  },
  textInput: {
    backgroundColor: colors.surfaceRaised,
    borderColor: colors.border,
    borderWidth: 1,
    borderRadius: 12,
    paddingHorizontal: 14,
    paddingVertical: 12,
    color: colors.text,
    fontSize: 15
  },
  primaryButton: {
    marginTop: 4,
    borderRadius: 999,
    backgroundColor: colors.accent,
    paddingVertical: 12,
    alignItems: "center"
  },
  primaryButtonText: { color: colors.background, fontSize: 14, fontWeight: "800" },
  buttonDisabled: { opacity: 0.45 },
  secondaryButton: {
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "center",
    gap: 8,
    borderRadius: 999,
    borderColor: colors.border,
    borderWidth: 1,
    backgroundColor: colors.surfaceRaised,
    paddingVertical: 11
  },
  secondaryButtonText: { color: colors.accentStrong, fontSize: 13, fontWeight: "700" },
  linkButton: { alignItems: "center", paddingVertical: 8 },
  linkText: { color: colors.muted, fontSize: 13, fontWeight: "600" },
  retry: {
    marginTop: 6,
    paddingHorizontal: 14,
    paddingVertical: 8,
    borderRadius: 999,
    backgroundColor: colors.surfaceRaised,
    borderColor: colors.border,
    borderWidth: 1,
    alignSelf: "flex-start"
  },
  retryText: { color: colors.accentStrong, fontSize: 13, fontWeight: "700" },
  sheetBackdrop: {
    flex: 1,
    backgroundColor: "rgba(0,0,0,0.6)",
    justifyContent: "flex-end"
  },
  sheetBackdropDismiss: { flex: 1 },
  sheet: {
    backgroundColor: colors.surfaceRaised,
    borderTopLeftRadius: 20,
    borderTopRightRadius: 20,
    padding: 20,
    gap: 12
  },
  sheetTitle: { color: colors.text, fontSize: 16, fontWeight: "800" },
  privacyOverlay: {
    ...StyleSheet.absoluteFillObject,
    backgroundColor: colors.background,
    alignItems: "center",
    justifyContent: "center"
  }
});

export default PrivateOfficeLockGate;
