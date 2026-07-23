import { NativeStackNavigationProp } from "@react-navigation/native-stack";
import { useNavigation } from "@react-navigation/native";
import { useCallback, useEffect, useRef, useState } from "react";
import { Alert, Keyboard, Linking, KeyboardAvoidingView, Platform, ScrollView, StyleSheet, Text, View } from "react-native";
import * as Haptics from "expo-haptics";
import { useSafeAreaInsets } from "react-native-safe-area-context";
import { signIn, useAuth } from "../session/auth";
import { createQaSimulatorLocalSession, isQaSimulatorAutoLoginEnabled, tryHandleQaSimulatorAuthUrl } from "../session/qaSimulatorAuth";
import { getCachedSessionUser } from "../session/sessionStore";
import { PulseUser } from "../api/auth";
import { PulseApiError } from "../api/pulseApi";
import {
  authenticateWithBiometrics,
  confirmAndEnableBiometricLogin,
  getBiometricCapability,
  isBiometricEnabledForCurrentSession,
  BiometricCapability
} from "../session/biometricAuth";
import { listRememberedAccounts } from "../session/rememberedAccounts";
import { colors } from "../theme/colors";
import { logiNexus } from "../theme/logiNexus";
import { AuthStackParamList } from "../navigation/types";
import { LoginBackground } from "../components/auth/LoginBackground";
import { PulseSocBrandHeader } from "../components/auth/PulseSocBrandHeader";
import { BiometricLoginButton, BiometricButtonState } from "../components/auth/BiometricLoginButton";
import { ManualLoginForm, ManualLoginFormHandle } from "../components/auth/ManualLoginForm";
import { AccountActions } from "../components/auth/AccountActions";
import { AuthStatusFooter } from "../components/auth/AuthStatusFooter";
import { LogiNexusPanel } from "../components/LogiNexus";

export function LoginScreen() {
  const navigation = useNavigation<NativeStackNavigationProp<AuthStackParamList>>();
  const insets = useSafeAreaInsets();
  const { setAuthState } = useAuth();
  const formRef = useRef<ManualLoginFormHandle>(null);
  const qaBootstrapStarted = useRef(false);
  const autoPromptAttempted = useRef(false);

  const [identifier, setIdentifier] = useState("");
  const [password, setPassword] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [formError, setFormError] = useState<string | undefined>();

  const [biometricCapability, setBiometricCapability] = useState<BiometricCapability | null>(null);
  const [biometricEnabled, setBiometricEnabled] = useState(false);
  const [biometricState, setBiometricState] = useState<BiometricButtonState>("idle");
  const [cachedUser, setCachedUser] = useState<PulseUser | null>(null);
  const [hasRememberedAccounts, setHasRememberedAccounts] = useState(false);

  useEffect(() => {
    let mounted = true;
    async function loadReturningUserState() {
      const [capability, enabled, cached, remembered] = await Promise.all([
        getBiometricCapability(),
        isBiometricEnabledForCurrentSession(),
        getCachedSessionUser<PulseUser>(),
        listRememberedAccounts()
      ]);
      if (!mounted) return;
      setBiometricCapability(capability);
      setBiometricEnabled(enabled);
      setCachedUser(cached);
      setHasRememberedAccounts(remembered.length > 0);
    }
    loadReturningUserState().catch(() => undefined);
    return () => {
      mounted = false;
    };
  }, []);

  useEffect(() => {
    let mounted = true;

    async function handleQaUrl(url: string | null) {
      if (!url) return;
      const result = await tryHandleQaSimulatorAuthUrl(url).catch(() => null);
      if (!mounted || !result?.handled || !result.authState) return;
      setAuthState(result.authState);
    }

    Linking.getInitialURL().then(handleQaUrl).catch(() => undefined);
    const subscription = Linking.addEventListener("url", (event) => {
      handleQaUrl(event.url).catch(() => undefined);
    });
    return () => {
      mounted = false;
      subscription.remove();
    };
  }, [setAuthState]);

  useEffect(() => {
    if (!isQaSimulatorAutoLoginEnabled() || qaBootstrapStarted.current) return;
    let mounted = true;
    qaBootstrapStarted.current = true;
    setSubmitting(true);
    createQaSimulatorLocalSession()
      .then((state) => {
        if (mounted && state.status === "signedIn") setAuthState(state);
      })
      .catch(() => undefined)
      .finally(() => {
        if (mounted) setSubmitting(false);
      });
    return () => {
      mounted = false;
    };
  }, [setAuthState]);

  const enableBiometricsForUser = useCallback(async (userId: number, label: string) => {
    const enabled = await confirmAndEnableBiometricLogin(userId).catch(() => false);
    if (enabled) {
      setBiometricEnabled(true);
      Haptics.notificationAsync(Haptics.NotificationFeedbackType.Success).catch(() => undefined);
      Alert.alert(`${label} enabled`, `Next time, tap ${label} on the sign-in screen to unlock PulseSoc.`);
      return;
    }
    Alert.alert(
      `${label} not enabled`,
      "We couldn't confirm your biometrics. You can try again from Settings anytime — your password sign-in still works.",
      [{ text: "OK", style: "cancel" }]
    );
  }, []);

  const submitManualSignIn = useCallback(async () => {
    if (submitting) return;
    Keyboard.dismiss();
    setFormError(undefined);
    setSubmitting(true);
    try {
      const trimmedIdentifier = identifier.trim();
      const authState = await signIn(trimmedIdentifier, password);
      if (authState.status !== "signedIn" || !authState.user) {
        setFormError("That email/username or password doesn't match our records.");
        return;
      }
      setAuthState(authState);
      if (biometricCapability?.available && !biometricEnabled) {
        const userId = authState.user.user_id;
        const kindLabel = biometricCapability.kind === "faceId" ? "Face ID" : "biometric sign-in";
        setTimeout(() => {
          Alert.alert(
            biometricCapability.kind === "faceId" ? "Enable Face ID?" : "Enable biometric sign-in?",
            `Unlock PulseSoc faster next time without typing your password. You can turn ${kindLabel} off anytime in Settings. PulseSoc never receives or stores your face.`,
            [
              { text: "Not now", style: "cancel" },
              {
                text: "Enable",
                onPress: () => {
                  void enableBiometricsForUser(userId, biometricCapability.kind === "faceId" ? "Face ID" : "Biometric sign-in");
                }
              }
            ]
          );
        }, 400);
      }
    } catch (error) {
      setFormError(describeLoginError(error));
    } finally {
      setSubmitting(false);
    }
  }, [identifier, password, submitting, setAuthState, biometricCapability, biometricEnabled, enableBiometricsForUser]);

  const handleBiometricPress = useCallback(async () => {
    if (biometricState === "loading") return;

    // Hardware exists but no face/finger is enrolled in iOS yet — guide to setup.
    if (biometricCapability && !biometricCapability.available && biometricCapability.reason === "not_enrolled") {
      const word = biometricCapability.kind === "touchId" ? "Touch ID" : "Face ID";
      Alert.alert(
        `Set up ${word}`,
        `${word} isn't enrolled on this device yet. Open Settings › Face ID & Passcode to set it up, then come back to sign in.`,
        [
          { text: "Not now", style: "cancel" },
          { text: "Open Settings", onPress: () => Linking.openSettings().catch(() => undefined) }
        ]
      );
      return;
    }

    // Enrolled in iOS, but this account hasn't turned on biometric sign-in yet.
    if (!biometricEnabled) {
      const word = biometricCapability?.kind === "touchId" ? "Touch ID" : "Face ID";
      Alert.alert(
        `Turn on ${word}`,
        `Sign in with your password once, then tap Enable to unlock PulseSoc with ${word} next time.`,
        [{ text: "OK", onPress: () => formRef.current?.focusIdentifier() }]
      );
      return;
    }

    setBiometricState("loading");
    const result = await authenticateWithBiometrics();
    if (!result) {
      setBiometricState("idle");
      return;
    }
    if (result.outcome === "success") {
      setBiometricState("success");
      Haptics.notificationAsync(Haptics.NotificationFeedbackType.Success).catch(() => undefined);
      setAuthState(result.authState);
      return;
    }
    setBiometricState("failed");
    setTimeout(() => setBiometricState("idle"), 1200);
    if (result.outcome === "cancelled") return;
    if (result.outcome === "lockout") {
      Alert.alert("Biometric unlock locked", "Too many attempts. Use your device passcode or sign in manually.");
      return;
    }
    if (result.outcome === "session_invalid" || result.outcome === "no_enrolled_account") {
      setBiometricEnabled(false);
      Alert.alert("Sign in required", "Your saved sign-in needs to be refreshed. Please sign in with your password.");
      formRef.current?.focusIdentifier();
      return;
    }
    if (result.outcome === "not_available") return;
    Alert.alert("Biometric sign-in failed", "We couldn't verify you. Please try again or sign in manually.");
  }, [biometricState, biometricCapability, biometricEnabled, setAuthState]);

  const kindWord = biometricCapability?.kind === "touchId" ? "Touch ID" : biometricCapability?.kind === "iris" ? "biometrics" : "Face ID";
  // Show the affordance whenever the device physically supports biometrics; the
  // label + tap behavior adapt to whether it still needs OS setup, PulseSoc
  // enrollment, or is ready to unlock.
  const showBiometricButton = Boolean(biometricCapability?.hasHardware);
  const biometricButtonLabel =
    biometricCapability && !biometricCapability.available && biometricCapability.reason === "not_enrolled"
      ? `Set up ${kindWord}`
      : !biometricEnabled
        ? `Enable ${kindWord}`
        : `Sign in with ${kindWord}`;
  const welcomeName = cachedUser?.display_name || cachedUser?.full_name || cachedUser?.username;

  // Auto-initiate Face ID exactly once per screen mount when a single valid
  // biometric account exists. The ref guard prevents a re-prompt loop after the
  // user cancels or fails — they can still tap the visible button manually.
  useEffect(() => {
    if (autoPromptAttempted.current) return;
    if (!biometricEnabled || !biometricCapability?.available || !cachedUser) return;
    if (submitting || isQaSimulatorAutoLoginEnabled()) return;
    autoPromptAttempted.current = true;
    const timer = setTimeout(() => {
      handleBiometricPress().catch(() => undefined);
    }, 350);
    return () => clearTimeout(timer);
  }, [biometricEnabled, biometricCapability, cachedUser, submitting, handleBiometricPress]);

  return (
    <View style={styles.root}>
      <LoginBackground />
      <KeyboardAvoidingView
        style={styles.flex}
        behavior={Platform.OS === "ios" ? "padding" : undefined}
        keyboardVerticalOffset={insets.top}
      >
        <ScrollView
          contentContainerStyle={[styles.scrollContent, { paddingTop: insets.top + logiNexus.spacing.xxl, paddingBottom: insets.bottom + logiNexus.spacing.xl }]}
          keyboardShouldPersistTaps="handled"
        >
          <PulseSocBrandHeader />

          <LogiNexusPanel style={styles.card}>
            <Text style={styles.cardTitle} maxFontSizeMultiplier={1.5}>
              {welcomeName ? `Welcome back, ${welcomeName}` : "Welcome back"}
            </Text>
            <Text style={styles.cardCopy} maxFontSizeMultiplier={1.5}>
              Sign in with your existing PulseSoc account to keep your posts, messages, media, subscriptions, and settings together.
            </Text>

            {showBiometricButton ? (
              <>
                <BiometricLoginButton
                  kind={biometricCapability!.kind}
                  state={biometricState}
                  onPress={handleBiometricPress}
                  label={biometricButtonLabel}
                />
                <View style={styles.divider}>
                  <View style={styles.dividerLine} />
                  <Text style={styles.dividerLabel}>or sign in manually</Text>
                  <View style={styles.dividerLine} />
                </View>
              </>
            ) : null}

            <ManualLoginForm
              ref={formRef}
              identifier={identifier}
              password={password}
              onChangeIdentifier={setIdentifier}
              onChangePassword={setPassword}
              onSubmit={submitManualSignIn}
              submitting={submitting}
              formError={formError}
            />

            <AccountActions
              onCreateAccount={() => navigation.navigate("Signup")}
              onForgotPassword={() => navigation.navigate("AccountRecovery")}
              onUseAnotherAccount={
                hasRememberedAccounts
                  ? () => {
                      setIdentifier("");
                      setPassword("");
                      setFormError(undefined);
                      formRef.current?.focusIdentifier();
                    }
                  : undefined
              }
            />
          </LogiNexusPanel>

          <AuthStatusFooter biometricProtected={biometricEnabled} />
        </ScrollView>
      </KeyboardAvoidingView>
    </View>
  );
}

function describeLoginError(error: unknown): string {
  if (error instanceof PulseApiError) {
    if (error.code === "request_unreachable" || error.status === 503) {
      return "PulseSoc could not be reached. Check your connection and try again.";
    }
    if (error.status === 429) return "Too many attempts. Please wait a moment and try again.";
    if (error.status === 401 || error.status === 403) return "That email/username or password doesn't match our records.";
    if (error.status >= 500) return "PulseSoc is having trouble right now. Please try again shortly.";
    return error.message || "Unable to sign in.";
  }
  return error instanceof Error ? error.message : "Unable to sign in.";
}

const styles = StyleSheet.create({
  root: {
    flex: 1,
    backgroundColor: colors.background
  },
  flex: {
    flex: 1
  },
  scrollContent: {
    flexGrow: 1,
    gap: logiNexus.spacing.xxl,
    justifyContent: "center",
    paddingHorizontal: logiNexus.spacing.xl
  },
  card: {
    gap: logiNexus.spacing.lg
  },
  cardTitle: {
    ...logiNexus.typography.title,
    color: colors.text,
    textAlign: "center"
  },
  cardCopy: {
    color: colors.muted,
    fontSize: 14,
    fontWeight: "600",
    lineHeight: 20,
    textAlign: "center"
  },
  divider: {
    alignItems: "center",
    flexDirection: "row",
    gap: logiNexus.spacing.sm
  },
  dividerLine: {
    backgroundColor: colors.border,
    flex: 1,
    height: StyleSheet.hairlineWidth
  },
  dividerLabel: {
    color: colors.muted,
    fontSize: 12,
    fontWeight: "700"
  }
});
