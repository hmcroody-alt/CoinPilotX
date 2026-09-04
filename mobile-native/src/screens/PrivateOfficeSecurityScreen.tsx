/**
 * Private Office security settings (Stage 25).
 *
 * Everything here manages the second lock; none of it bypasses it. The screen
 * itself renders inside the lock gate, so reaching these controls already
 * required an unlock — changing the passcode demands the current one on top
 * (Stage 12), and "Lock now" revokes every grant server-side before dropping
 * the local one, so the other devices die with this tap, not on their next
 * foreground.
 *
 * The Face ID toggle is honest in both directions: enabling arms the local
 * biometry-gated credential first and records the preference server-side
 * second; if arming fails the switch never reads ON. Disabling clears the
 * credential unconditionally — a preference the server failed to record is a
 * cosmetic problem, a credential left behind after "off" is not.
 */

import { NativeStackScreenProps } from "@react-navigation/native-stack";
import { useCallback, useEffect, useState } from "react";
import {
  ActivityIndicator,
  Pressable,
  ScrollView,
  StyleSheet,
  Switch,
  Text,
  TextInput,
  View
} from "react-native";
import { Ionicons } from "@expo/vector-icons";
import { useSafeAreaInsets } from "react-native-safe-area-context";
import {
  changeOfficePasscode,
  lockOffice,
  setOfficeBiometricPreference,
  unlockOffice
} from "../api/privateOffice";
import { useTranslation } from "../i18n";
import { BOTTOM_NAV_CONTENT_CLEARANCE } from "../navigation/BottomNavVisibility";
import { RootStackParamList } from "../navigation/types";
import { PrivateOfficeLockGate } from "../privateOffice/PrivateOfficeLockGate";
import {
  OfficeRelockTiming,
  RELOCK_TIMINGS,
  disableOfficeBiometric,
  enableOfficeBiometric,
  getOfficeBiometricUserId,
  getOfficeRelockTiming,
  lockOfficeLocally,
  setOfficeRelockTiming
} from "../privateOffice/officeLock";
import { getBiometricCapability } from "../session/biometricAuth";
import { getSessionEnvelope } from "../session/sessionStore";
import { colors } from "../theme/colors";

type Props = NativeStackScreenProps<RootStackParamList, "PrivateOfficeSecurity">;

const MIN_PASSCODE_DIGITS = 6;
const PASSCODE_MAX_LENGTH = 12;

function digitsOnly(value: string): string {
  return value.replace(/[^0-9]/g, "").slice(0, PASSCODE_MAX_LENGTH);
}

function SecuritySettings() {
  const { t } = useTranslation();
  const insets = useSafeAreaInsets();

  const [userId, setUserId] = useState(0);
  const [biometricAvailable, setBiometricAvailable] = useState(false);
  const [biometricKind, setBiometricKind] = useState<"faceId" | "touchId" | "none">("none");
  const [biometricOn, setBiometricOn] = useState(false);
  const [relock, setRelock] = useState<OfficeRelockTiming>("immediate");
  const [busy, setBusy] = useState(false);

  // Change-passcode form
  const [currentPasscode, setCurrentPasscode] = useState("");
  const [newPasscode, setNewPasscode] = useState("");
  const [confirmPasscode, setConfirmPasscode] = useState("");
  const [changeMessage, setChangeMessage] = useState<{ ok: boolean; text: string } | null>(null);

  // Enabling Face ID from settings needs the passcode to store; ask for it.
  const [armPasscode, setArmPasscode] = useState("");
  const [armOpen, setArmOpen] = useState(false);
  const [armFailure, setArmFailure] = useState("");

  useEffect(() => {
    let cancelled = false;
    (async () => {
      const [envelope, capability, armedFor, timing] = await Promise.all([
        getSessionEnvelope(),
        getBiometricCapability(),
        getOfficeBiometricUserId(),
        getOfficeRelockTiming()
      ]);
      if (cancelled) return;
      const currentUserId = envelope?.userId ?? 0;
      setUserId(currentUserId);
      setBiometricAvailable(capability.available);
      setBiometricKind(
        capability.kind === "faceId" || capability.kind === "touchId" ? capability.kind : "none"
      );
      setBiometricOn(Boolean(armedFor && armedFor === currentUserId));
      setRelock(timing);
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  const biometricLabel =
    biometricKind === "touchId"
      ? t("premium:privateOffice.lock.biometricTouchId")
      : t("premium:privateOffice.lock.biometricFaceId");

  const onToggleBiometric = useCallback(
    async (next: boolean) => {
      if (busy) return;
      if (next) {
        // Need the office passcode to place behind the biometric gate.
        setArmFailure("");
        setArmPasscode("");
        setArmOpen(true);
        return;
      }
      setBusy(true);
      try {
        await disableOfficeBiometric();
        setBiometricOn(false);
        await setOfficeBiometricPreference(false);
      } finally {
        setBusy(false);
      }
    },
    [busy]
  );

  const onArmBiometric = useCallback(async () => {
    if (busy || armPasscode.length < MIN_PASSCODE_DIGITS) return;
    setBusy(true);
    setArmFailure("");
    try {
      // Prove the passcode server-side before storing it locally: arming Face
      // ID with a wrong passcode would fabricate a credential that can never
      // unlock anything. A real unlock is the proof — we are already unlocked,
      // so the fresh grant simply replaces the current one.
      const proof = await unlockOffice(armPasscode, userId);
      if (proof.state !== "UNLOCKED") {
        if (proof.state === "WRONG_PASSCODE") {
          setArmFailure(t("premium:privateOffice.lock.wrong"));
        } else if (proof.state === "COOLDOWN") {
          setArmFailure(
            t("premium:privateOffice.lock.cooldown", {
              seconds: Math.max(1, proof.retryAfterSeconds)
            })
          );
        } else {
          setArmFailure(t("premium:privateOffice.lock.error"));
        }
        return;
      }
      const armed = await enableOfficeBiometric(userId, armPasscode);
      if (!armed) {
        setArmFailure(t("premium:privateOffice.lock.error"));
        return;
      }
      setBiometricOn(true);
      setArmOpen(false);
      setArmPasscode("");
      await setOfficeBiometricPreference(true);
    } finally {
      setBusy(false);
    }
  }, [armPasscode, busy, t, userId]);

  const onSelectRelock = useCallback(async (timing: OfficeRelockTiming) => {
    setRelock(timing);
    await setOfficeRelockTiming(timing);
  }, []);

  const onLockNow = useCallback(async () => {
    if (busy) return;
    setBusy(true);
    try {
      await lockOffice(true);
    } finally {
      // Local lock regardless: even if the server call failed, this device
      // should not keep rendering Office content after the user said "lock".
      lockOfficeLocally();
      setBusy(false);
    }
  }, [busy]);

  const canSubmitChange =
    currentPasscode.length >= MIN_PASSCODE_DIGITS &&
    newPasscode.length >= MIN_PASSCODE_DIGITS &&
    confirmPasscode.length >= MIN_PASSCODE_DIGITS &&
    !busy;

  const onChangePasscode = useCallback(async () => {
    if (!canSubmitChange) return;
    if (newPasscode !== confirmPasscode) {
      setChangeMessage({ ok: false, text: t("premium:privateOffice.lock.setup.mismatch") });
      return;
    }
    setBusy(true);
    setChangeMessage(null);
    try {
      const result = await changeOfficePasscode(currentPasscode, newPasscode, confirmPasscode);
      if (result.state === "OK") {
        // Change revokes every grant everywhere (including ours) and makes the
        // stored Face ID credential stale. Re-arm is a fresh decision.
        await disableOfficeBiometric();
        setBiometricOn(false);
        lockOfficeLocally();
        setCurrentPasscode("");
        setNewPasscode("");
        setConfirmPasscode("");
        setChangeMessage({ ok: true, text: t("premium:privateOffice.security.change.success") });
        return;
      }
      const text =
        result.state === "WRONG_PASSCODE"
          ? t("premium:privateOffice.lock.wrong")
          : result.state === "COOLDOWN"
            ? t("premium:privateOffice.lock.cooldown", {
                seconds: Math.max(1, result.retryAfterSeconds)
              })
            : result.state === "POLICY"
              ? t("premium:privateOffice.lock.policy", { digits: MIN_PASSCODE_DIGITS })
              : t("premium:privateOffice.lock.error");
      setChangeMessage({ ok: false, text });
    } finally {
      setBusy(false);
    }
  }, [canSubmitChange, confirmPasscode, currentPasscode, newPasscode, t]);

  return (
    <ScrollView
      style={styles.root}
      contentContainerStyle={[
        styles.content,
        { paddingBottom: Math.max(insets.bottom, 18) + BOTTOM_NAV_CONTENT_CLEARANCE }
      ]}
      keyboardShouldPersistTaps="handled"
    >
      <View style={styles.header}>
        <Text style={styles.title}>{t("premium:privateOffice.security.title")}</Text>
        <Text style={styles.subtitle}>{t("premium:privateOffice.security.subtitle")}</Text>
      </View>

      {/* Relock timing (Stage 6) */}
      <View style={styles.section}>
        <Text style={styles.sectionTitle}>{t("premium:privateOffice.security.relock.label")}</Text>
        <Text style={styles.hint}>{t("premium:privateOffice.security.relock.hint")}</Text>
        {RELOCK_TIMINGS.map((timing) => (
          <Pressable
            key={timing}
            style={[styles.optionRow, relock === timing ? styles.optionRowActive : null]}
            onPress={() => void onSelectRelock(timing)}
            accessibilityRole="radio"
            accessibilityState={{ selected: relock === timing }}
          >
            <Text style={relock === timing ? styles.optionTextActive : styles.optionText}>
              {t(`premium:privateOffice.security.relock.${timing}`)}
            </Text>
            {relock === timing ? (
              <Ionicons name="checkmark-circle" size={18} color={colors.accent} />
            ) : null}
          </Pressable>
        ))}
      </View>

      {/* Face ID (Stages 7-8) */}
      {biometricAvailable ? (
        <View style={styles.section}>
          <Text style={styles.sectionTitle}>
            {t("premium:privateOffice.security.biometric.label", { method: biometricLabel })}
          </Text>
          <View style={styles.switchRow}>
            <Text style={styles.hintWide}>
              {t("premium:privateOffice.security.biometric.hint")}
            </Text>
            <Switch
              value={biometricOn}
              disabled={busy}
              onValueChange={(next) => void onToggleBiometric(next)}
              trackColor={{ true: colors.accent, false: colors.border }}
            />
          </View>
          {armOpen && !biometricOn ? (
            <View style={styles.armBox}>
              <TextInput
                style={styles.textInput}
                value={armPasscode}
                onChangeText={(value) => {
                  setArmFailure("");
                  setArmPasscode(digitsOnly(value));
                }}
                keyboardType="number-pad"
                secureTextEntry
                maxLength={PASSCODE_MAX_LENGTH}
                accessibilityLabel={t("premium:privateOffice.lock.placeholder")}
                placeholder={t("premium:privateOffice.lock.placeholder")}
                placeholderTextColor={colors.muted}
              />
              {armFailure ? <Text style={styles.failureText}>{armFailure}</Text> : null}
              <Pressable
                style={[
                  styles.primaryButton,
                  armPasscode.length < MIN_PASSCODE_DIGITS || busy ? styles.buttonDisabled : null
                ]}
                disabled={armPasscode.length < MIN_PASSCODE_DIGITS || busy}
                onPress={() => void onArmBiometric()}
                accessibilityRole="button"
              >
                {busy ? (
                  <ActivityIndicator color={colors.background} />
                ) : (
                  <Text style={styles.primaryButtonText}>
                    {t("premium:privateOffice.security.biometric.confirm")}
                  </Text>
                )}
              </Pressable>
            </View>
          ) : null}
        </View>
      ) : null}

      {/* Change passcode (Stage 12) */}
      <View style={styles.section}>
        <Text style={styles.sectionTitle}>{t("premium:privateOffice.security.change.label")}</Text>
        <TextInput
          style={styles.textInput}
          value={currentPasscode}
          onChangeText={(value) => {
            setChangeMessage(null);
            setCurrentPasscode(digitsOnly(value));
          }}
          keyboardType="number-pad"
          secureTextEntry
          maxLength={PASSCODE_MAX_LENGTH}
          accessibilityLabel={t("premium:privateOffice.security.change.current")}
          placeholder={t("premium:privateOffice.security.change.current")}
          placeholderTextColor={colors.muted}
        />
        <TextInput
          style={styles.textInput}
          value={newPasscode}
          onChangeText={(value) => {
            setChangeMessage(null);
            setNewPasscode(digitsOnly(value));
          }}
          keyboardType="number-pad"
          secureTextEntry
          maxLength={PASSCODE_MAX_LENGTH}
          accessibilityLabel={t("premium:privateOffice.security.change.new")}
          placeholder={t("premium:privateOffice.security.change.new")}
          placeholderTextColor={colors.muted}
        />
        <TextInput
          style={styles.textInput}
          value={confirmPasscode}
          onChangeText={(value) => {
            setChangeMessage(null);
            setConfirmPasscode(digitsOnly(value));
          }}
          keyboardType="number-pad"
          secureTextEntry
          maxLength={PASSCODE_MAX_LENGTH}
          accessibilityLabel={t("premium:privateOffice.security.change.confirm")}
          placeholder={t("premium:privateOffice.security.change.confirm")}
          placeholderTextColor={colors.muted}
        />
        {changeMessage ? (
          <Text style={changeMessage.ok ? styles.successText : styles.failureText}>
            {changeMessage.text}
          </Text>
        ) : null}
        <Pressable
          style={[styles.primaryButton, !canSubmitChange ? styles.buttonDisabled : null]}
          disabled={!canSubmitChange}
          onPress={() => void onChangePasscode()}
          accessibilityRole="button"
        >
          {busy ? (
            <ActivityIndicator color={colors.background} />
          ) : (
            <Text style={styles.primaryButtonText}>
              {t("premium:privateOffice.security.change.submit")}
            </Text>
          )}
        </Pressable>
      </View>

      {/* Lock now (Stage 25) */}
      <View style={styles.section}>
        <Pressable
          style={styles.dangerButton}
          disabled={busy}
          onPress={() => void onLockNow()}
          accessibilityRole="button"
        >
          <Ionicons name="lock-closed" size={16} color={colors.danger} />
          <Text style={styles.dangerText}>{t("premium:privateOffice.security.lockNow")}</Text>
        </Pressable>
        <Text style={styles.hint}>{t("premium:privateOffice.security.lockNowHint")}</Text>
      </View>
    </ScrollView>
  );
}

export function PrivateOfficeSecurityScreen({ navigation }: Props) {
  return (
    <PrivateOfficeLockGate onDismiss={() => navigation.goBack()}>
      <SecuritySettings />
    </PrivateOfficeLockGate>
  );
}

const styles = StyleSheet.create({
  root: { flex: 1, backgroundColor: colors.background },
  content: { padding: 18, gap: 20 },
  header: { gap: 6 },
  title: { color: colors.text, fontSize: 24, fontWeight: "800" },
  subtitle: { color: colors.muted, fontSize: 13, lineHeight: 19 },
  section: { gap: 10 },
  sectionTitle: { color: colors.muted, fontSize: 11, fontWeight: "800", letterSpacing: 1.4 },
  hint: { color: colors.muted, fontSize: 12, lineHeight: 17 },
  hintWide: { color: colors.muted, fontSize: 12, lineHeight: 17, flex: 1, paddingRight: 12 },
  optionRow: {
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "space-between",
    backgroundColor: colors.surface,
    borderColor: colors.border,
    borderWidth: 1,
    borderRadius: 12,
    paddingHorizontal: 14,
    paddingVertical: 12
  },
  optionRowActive: { borderColor: colors.accent },
  optionText: { color: colors.muted, fontSize: 14, fontWeight: "600" },
  optionTextActive: { color: colors.text, fontSize: 14, fontWeight: "700" },
  switchRow: {
    flexDirection: "row",
    alignItems: "center",
    backgroundColor: colors.surface,
    borderColor: colors.border,
    borderWidth: 1,
    borderRadius: 12,
    paddingHorizontal: 14,
    paddingVertical: 12
  },
  armBox: { gap: 8 },
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
  failureText: { color: colors.danger, fontSize: 13, fontWeight: "600" },
  successText: { color: colors.accent, fontSize: 13, fontWeight: "600" },
  primaryButton: {
    borderRadius: 999,
    backgroundColor: colors.accent,
    paddingVertical: 12,
    alignItems: "center"
  },
  primaryButtonText: { color: colors.background, fontSize: 14, fontWeight: "800" },
  buttonDisabled: { opacity: 0.45 },
  dangerButton: {
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "center",
    gap: 8,
    borderRadius: 999,
    borderColor: colors.danger,
    borderWidth: 1,
    paddingVertical: 12
  },
  dangerText: { color: colors.danger, fontSize: 14, fontWeight: "800" }
});

export default PrivateOfficeSecurityScreen;
