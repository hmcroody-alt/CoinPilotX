import { Ionicons } from "@expo/vector-icons";
import { useCallback, useEffect, useRef, useState } from "react";
import { ActivityIndicator, AppState, Pressable, StyleSheet, Text, View } from "react-native";
import * as Haptics from "expo-haptics";
import { changeUnverifiedEmail, getEmailConfirmationStatus, resendEmailConfirmation } from "../../../api/auth";
import { finalizeConfirmedSignup, AuthState } from "../../../session/auth";
import { validateEmail, normalizeEmail } from "../../../auth/signupValidation";
import { useTranslation } from "../../../i18n";
import { colors } from "../../../theme/colors";
import { logiNexus } from "../../../theme/logiNexus";
import { SecureTextField } from "../SecureTextField";
import { PulsePrimaryButton } from "./PulsePrimaryButton";

const POLL_INTERVAL_MS = 4000;
const RESEND_COOLDOWN_S = 30;

/**
 * Email verification step. PulseSoc confirms accounts with an emailed *link*
 * (not an in-app numeric code), so this step honestly reflects that: it tells
 * the user to tap the link, quietly polls `confirmation-status`, and advances
 * the instant the backend reports the address confirmed — then exchanges the
 * held credentials for a real session via the production login path. Resend is
 * cooldown-gated on the client and rate-limited on the server; the user can
 * also correct a mistyped address without losing progress.
 */
export function VerifyEmailStep({
  email,
  password,
  deliveryFailed,
  onConfirmed,
  onEmailChanged
}: {
  email: string;
  password: string;
  deliveryFailed: boolean;
  onConfirmed: (state: AuthState) => void;
  onEmailChanged: (nextEmail: string) => void;
}) {
  const { t } = useTranslation();
  const [status, setStatus] = useState<string | undefined>(
    deliveryFailed ? t("auth:verify.deliveryFailed") : undefined
  );
  const [error, setError] = useState<string | undefined>();
  const [checking, setChecking] = useState(false);
  const [cooldown, setCooldown] = useState(RESEND_COOLDOWN_S);
  const [editingEmail, setEditingEmail] = useState(false);
  const [nextEmail, setNextEmail] = useState(email);
  const [savingEmail, setSavingEmail] = useState(false);
  const finalizingRef = useRef(false);

  // Attempt to confirm-then-login. Guarded so overlapping polls never fire two
  // login requests (idempotency at the client edge).
  const attemptFinalize = useCallback(
    async (source: "poll" | "manual") => {
      if (finalizingRef.current) return;
      finalizingRef.current = true;
      if (source === "manual") setChecking(true);
      try {
        const result = await getEmailConfirmationStatus(email);
        if (!result.confirmed) {
          if (source === "manual") setStatus(t("auth:verify.notConfirmedYet"));
          return;
        }
        const state = await finalizeConfirmedSignup(email, password);
        if (state.status === "signedIn") {
          Haptics.notificationAsync(Haptics.NotificationFeedbackType.Success).catch(() => undefined);
          onConfirmed(state);
        } else if (source === "manual") {
          setError(t("auth:verify.confirmedSignInToFinish"));
        }
      } catch {
        if (source === "manual") setError(t("auth:verify.checkFailed"));
      } finally {
        finalizingRef.current = false;
        if (source === "manual") setChecking(false);
      }
    },
    [email, password, onConfirmed, t]
  );

  // Quiet background polling while this step is foregrounded.
  useEffect(() => {
    if (editingEmail) return;
    let active = true;
    const tick = () => {
      if (active && AppState.currentState === "active") void attemptFinalize("poll");
    };
    const timer = setInterval(tick, POLL_INTERVAL_MS);
    return () => {
      active = false;
      clearInterval(timer);
    };
  }, [attemptFinalize, editingEmail]);

  // Resend cooldown countdown.
  useEffect(() => {
    if (cooldown <= 0) return;
    const timer = setTimeout(() => setCooldown((value) => value - 1), 1000);
    return () => clearTimeout(timer);
  }, [cooldown]);

  const handleResend = useCallback(async () => {
    if (cooldown > 0) return;
    setError(undefined);
    setCooldown(RESEND_COOLDOWN_S);
    try {
      const result = await resendEmailConfirmation(email);
      setStatus(result.message || t("auth:verify.resentLink"));
    } catch {
      setError(t("auth:verify.resendFailed"));
    }
  }, [cooldown, email, t]);

  const handleSaveEmail = useCallback(async () => {
    const check = validateEmail(nextEmail);
    if (!check.valid) {
      setError(check.message);
      return;
    }
    const normalized = normalizeEmail(nextEmail);
    if (normalized === email) {
      setEditingEmail(false);
      return;
    }
    setSavingEmail(true);
    setError(undefined);
    try {
      await changeUnverifiedEmail({ old_email: email, new_email: normalized, password });
      onEmailChanged(normalized);
      setEditingEmail(false);
      setCooldown(RESEND_COOLDOWN_S);
      setStatus(t("auth:verify.linkSentTo", { email: normalized }));
    } catch (err) {
      setError(err instanceof Error ? err.message : t("auth:verify.updateEmailFailed"));
    } finally {
      setSavingEmail(false);
    }
  }, [nextEmail, email, password, onEmailChanged, t]);

  return (
    <View style={styles.root}>
      <View style={styles.iconWrap}>
        <Ionicons name="mail-unread-outline" size={30} color={colors.accent} />
      </View>

      <Text style={styles.lead} maxFontSizeMultiplier={1.6}>
        {t("auth:verify.linkLead")}
      </Text>
      <Text style={styles.email} maxFontSizeMultiplier={1.4}>
        {email}
      </Text>
      <Text style={styles.hint} maxFontSizeMultiplier={1.6}>
        {t("auth:verify.autoUpdateHint")}
      </Text>

      {status ? (
        <Text style={styles.status} accessibilityLiveRegion="polite">
          {status}
        </Text>
      ) : null}
      {error ? (
        <Text style={styles.error} accessibilityLiveRegion="assertive">
          {error}
        </Text>
      ) : null}

      {editingEmail ? (
        <View style={styles.editBlock}>
          <SecureTextField
            label={t("auth:verify.newEmailLabel")}
            iconName="mail-outline"
            autoCapitalize="none"
            autoComplete="email"
            autoCorrect={false}
            keyboardType="email-address"
            textContentType="emailAddress"
            value={nextEmail}
            onChangeText={setNextEmail}
          />
          <PulsePrimaryButton
            label={savingEmail ? t("auth:verify.updatingEmail") : t("auth:verify.updateEmail")}
            busy={savingEmail}
            onPress={() => void handleSaveEmail()}
            testID="signup-save-email"
            iconName="checkmark"
          />
          <Pressable accessibilityRole="button" hitSlop={8} onPress={() => { setEditingEmail(false); setNextEmail(email); }}>
            <Text style={styles.link}>{t("common:actions.cancel")}</Text>
          </Pressable>
        </View>
      ) : (
        <>
          <PulsePrimaryButton
            label={checking ? t("auth:verify.checking") : t("auth:verify.confirmedContinue")}
            busy={checking}
            onPress={() => void attemptFinalize("manual")}
            testID="signup-confirm-continue"
            iconName="arrow-forward"
          />

          <View style={styles.actionsRow}>
            <Pressable
              accessibilityRole="button"
              accessibilityState={{ disabled: cooldown > 0 }}
              disabled={cooldown > 0}
              hitSlop={8}
              onPress={() => void handleResend()}
              testID="signup-resend"
            >
              <Text style={[styles.link, cooldown > 0 && styles.linkDisabled]}>
                {cooldown > 0 ? t("auth:verify.resendLinkIn", { seconds: cooldown }) : t("auth:verify.resendLink")}
              </Text>
            </Pressable>
            <Text style={styles.divider}>|</Text>
            <Pressable accessibilityRole="button" hitSlop={8} onPress={() => setEditingEmail(true)} testID="signup-change-email">
              <Text style={styles.link}>{t("auth:verify.wrongEmail")}</Text>
            </Pressable>
          </View>
        </>
      )}

      {!editingEmail && !checking ? (
        <View style={styles.autopoll}>
          <ActivityIndicator size="small" color={colors.muted} />
          <Text style={styles.autopollText}>{t("auth:verify.waitingForConfirmation")}</Text>
        </View>
      ) : null}
    </View>
  );
}

const styles = StyleSheet.create({
  root: {
    alignItems: "center",
    gap: logiNexus.spacing.sm
  },
  iconWrap: {
    alignItems: "center",
    backgroundColor: colors.signalDim,
    borderRadius: logiNexus.radius.circular,
    height: 60,
    justifyContent: "center",
    marginBottom: logiNexus.spacing.xs,
    width: 60
  },
  lead: {
    color: colors.muted,
    fontSize: 14,
    fontWeight: "700",
    textAlign: "center"
  },
  email: {
    color: colors.text,
    fontSize: 17,
    fontWeight: "900",
    textAlign: "center"
  },
  hint: {
    color: colors.muted,
    fontSize: 13,
    fontWeight: "600",
    lineHeight: 19,
    marginTop: logiNexus.spacing.xs,
    paddingHorizontal: logiNexus.spacing.sm,
    textAlign: "center"
  },
  status: {
    color: colors.accent,
    fontSize: 13,
    fontWeight: "700",
    textAlign: "center"
  },
  error: {
    color: colors.danger,
    fontSize: 13,
    fontWeight: "700",
    textAlign: "center"
  },
  editBlock: {
    gap: logiNexus.spacing.md,
    marginTop: logiNexus.spacing.sm,
    width: "100%"
  },
  actionsRow: {
    alignItems: "center",
    flexDirection: "row",
    gap: 10,
    justifyContent: "center",
    marginTop: logiNexus.spacing.xs
  },
  link: {
    color: colors.accentStrong,
    fontSize: 13,
    fontWeight: "800",
    textDecorationLine: "underline"
  },
  linkDisabled: {
    color: colors.muted,
    textDecorationLine: "none"
  },
  divider: {
    color: colors.border
  },
  autopoll: {
    alignItems: "center",
    flexDirection: "row",
    gap: 8,
    marginTop: logiNexus.spacing.sm
  },
  autopollText: {
    color: colors.muted,
    fontSize: 12,
    fontWeight: "700"
  }
});
