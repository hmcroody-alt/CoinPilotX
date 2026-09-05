import { NativeStackNavigationProp } from "@react-navigation/native-stack";
import { useNavigation } from "@react-navigation/native";
import { useCallback, useEffect, useRef, useState } from "react";
import { Alert, Animated, Easing, Keyboard, Linking, KeyboardAvoidingView, Platform, Pressable, ScrollView, StyleSheet, Text, View } from "react-native";
import { Ionicons } from "@expo/vector-icons";
import * as Haptics from "expo-haptics";
import { LinearGradient } from "expo-linear-gradient";
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
import { translate, useTranslation } from "../i18n";
import { colors } from "../theme/colors";
import { logiNexus } from "../theme/logiNexus";
import { AuthStackParamList } from "../navigation/types";
import { LoginBackground } from "../components/auth/LoginBackground";
import { PulseSocBrandHeader } from "../components/auth/PulseSocBrandHeader";
import { BiometricLoginButton, BiometricButtonState } from "../components/auth/BiometricLoginButton";
import { ManualLoginForm, ManualLoginFormHandle } from "../components/auth/ManualLoginForm";
import { AuthStatusFooter } from "../components/auth/AuthStatusFooter";
import { useLogiNexusReducedMotion } from "../theme/logiNexusMotion";
import { createThemedStyles } from "../theme/themedStyles";

const LIVING_MESSAGES = [
  "Your network is alive.",
  "Millions of conversations are waiting.",
  "Your community is already awake.",
  "Ideas are traveling across PulseSoc.",
  "People are connecting right now.",
  "Welcome back to the signal.",
  "The galaxy is waiting.",
  "Where creators become movements.",
  "Every signal begins somewhere.",
  "Today's conversation is waiting."
];

export function LoginScreen() {
  const navigation = useNavigation<NativeStackNavigationProp<AuthStackParamList>>();
  const { t } = useTranslation();
  const insets = useSafeAreaInsets();
  const { setAuthState } = useAuth();
  const formRef = useRef<ManualLoginFormHandle>(null);
  const qaBootstrapStarted = useRef(false);
  const autoPromptAttempted = useRef(false);
  const reducedMotion = useLogiNexusReducedMotion();
  const gateProgress = useRef(new Animated.Value(0)).current;
  const messageOpacity = useRef(new Animated.Value(1)).current;

  const [gateOpen, setGateOpen] = useState(false);
  const [messageIndex, setMessageIndex] = useState(0);
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
      Alert.alert(t("auth:signIn.biometricEnabledTitle", { method: label }), t("auth:signIn.biometricEnabledBody", { method: label }));
      return;
    }
    Alert.alert(
      t("auth:signIn.biometricNotEnabledTitle", { method: label }),
      t("auth:signIn.biometricNotEnabledBody"),
      [{ text: t("common:actions.ok"), style: "cancel" }]
    );
  }, [t]);

  const submitManualSignIn = useCallback(async () => {
    if (submitting) return;
    Keyboard.dismiss();
    setFormError(undefined);
    setSubmitting(true);
    try {
      const trimmedIdentifier = identifier.trim();
      const authState = await signIn(trimmedIdentifier, password);
      if (authState.status !== "signedIn" || !authState.user) {
        setFormError(t("errors:auth.identifierMismatch"));
        return;
      }
      setAuthState(authState);
      if (biometricCapability?.available && !biometricEnabled) {
        const userId = authState.user.user_id;
        const kindLabel = biometricCapability.kind === "faceId" ? t("auth:signIn.faceId") : t("auth:signIn.biometricSignInLower");
        setTimeout(() => {
          Alert.alert(
            biometricCapability.kind === "faceId" ? t("auth:signIn.enableFaceIdTitle") : t("auth:signIn.enableBiometricTitle"),
            t("auth:signIn.enableBiometricBody", { method: kindLabel }),
            [
              { text: t("common:actions.notNow"), style: "cancel" },
              {
                text: t("common:actions.enable"),
                onPress: () => {
                  void enableBiometricsForUser(userId, biometricCapability.kind === "faceId" ? t("auth:signIn.faceId") : t("auth:signIn.biometricSignIn"));
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
  }, [identifier, password, submitting, setAuthState, biometricCapability, biometricEnabled, enableBiometricsForUser, t]);

  const handleBiometricPress = useCallback(async () => {
    if (biometricState === "loading") return;

    // Hardware exists but no face/finger is enrolled in iOS yet — guide to setup.
    if (biometricCapability && !biometricCapability.available && biometricCapability.reason === "not_enrolled") {
      const word = biometricCapability.kind === "touchId" ? t("auth:signIn.touchId") : t("auth:signIn.faceId");
      Alert.alert(
        t("auth:signIn.setUpBiometricTitle", { method: word }),
        t("auth:signIn.notEnrolledBody", { method: word }),
        [
          { text: t("common:actions.notNow"), style: "cancel" },
          { text: t("auth:signIn.openSettings"), onPress: () => Linking.openSettings().catch(() => undefined) }
        ]
      );
      return;
    }

    // Enrolled in iOS, but this account hasn't turned on biometric sign-in yet.
    if (!biometricEnabled) {
      const word = biometricCapability?.kind === "touchId" ? t("auth:signIn.touchId") : t("auth:signIn.faceId");
      Alert.alert(
        t("auth:signIn.turnOnBiometricTitle", { method: word }),
        t("auth:signIn.turnOnBiometricBody", { method: word }),
        [{ text: t("common:actions.ok"), onPress: () => formRef.current?.focusIdentifier() }]
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
      Alert.alert(t("auth:signIn.biometricLockedTitle"), t("auth:signIn.biometricLockedBody"));
      return;
    }
    if (result.outcome === "session_invalid" || result.outcome === "no_enrolled_account") {
      setBiometricEnabled(false);
      Alert.alert(t("auth:signIn.signInRequiredTitle"), t("auth:signIn.signInRequiredBody"));
      formRef.current?.focusIdentifier();
      return;
    }
    if (result.outcome === "not_available") return;
    Alert.alert(t("auth:signIn.biometricFailedTitle"), t("auth:signIn.biometricFailedBody"));
  }, [biometricState, biometricCapability, biometricEnabled, setAuthState, t]);

  const kindWord =
    biometricCapability?.kind === "touchId"
      ? t("auth:signIn.touchId")
      : biometricCapability?.kind === "iris"
        ? t("auth:signIn.biometrics")
        : t("auth:signIn.faceId");
  // Show the affordance whenever the device physically supports biometrics; the
  // label + tap behavior adapt to whether it still needs OS setup, PulseSoc
  // enrollment, or is ready to unlock.
  const showBiometricButton = Boolean(biometricCapability?.hasHardware);
  const biometricButtonLabel =
    biometricCapability && !biometricCapability.available && biometricCapability.reason === "not_enrolled"
      ? t("auth:signIn.setUpBiometricTitle", { method: kindWord })
      : !biometricEnabled
        ? t("auth:signIn.enableBiometricLabel", { method: kindWord })
        : t("auth:signIn.signInWithBiometric", { method: kindWord });
  const welcomeName = cachedUser?.display_name || cachedUser?.full_name || cachedUser?.username;

  const openGate = useCallback(() => {
    if (gateOpen) return;
    setGateOpen(true);
    Haptics.impactAsync(Haptics.ImpactFeedbackStyle.Light).catch(() => undefined);
    if (reducedMotion) {
      gateProgress.setValue(1);
      return;
    }
    Animated.timing(gateProgress, {
      toValue: 1,
      duration: 620,
      easing: Easing.out(Easing.cubic),
      useNativeDriver: true
    }).start();
  }, [gateOpen, gateProgress, reducedMotion]);

  useEffect(() => {
    if (reducedMotion) return undefined;
    let stopped = false;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const schedule = () => {
      timer = setTimeout(() => {
        Animated.timing(messageOpacity, { toValue: 0, duration: 420, useNativeDriver: true }).start(({ finished }) => {
          if (!finished || stopped) return;
          setMessageIndex((current) => (current + 1) % LIVING_MESSAGES.length);
          Animated.timing(messageOpacity, { toValue: 1, duration: 520, useNativeDriver: true }).start(({ finished: appeared }) => {
            if (appeared && !stopped) schedule();
          });
        });
      }, 4300);
    };
    schedule();
    return () => {
      stopped = true;
      if (timer) clearTimeout(timer);
      messageOpacity.stopAnimation();
    };
  }, [messageOpacity, reducedMotion]);

  // Auto-initiate Face ID exactly once per screen mount when a single valid
  // biometric account exists. The ref guard prevents a re-prompt loop after the
  // user cancels or fails — they can still tap the visible button manually.
  useEffect(() => {
    if (autoPromptAttempted.current) return;
    if (!gateOpen || !biometricEnabled || !biometricCapability?.available || !cachedUser) return;
    if (submitting || isQaSimulatorAutoLoginEnabled()) return;
    autoPromptAttempted.current = true;
    const timer = setTimeout(() => {
      handleBiometricPress().catch(() => undefined);
    }, 350);
    return () => clearTimeout(timer);
  }, [gateOpen, biometricEnabled, biometricCapability, cachedUser, submitting, handleBiometricPress]);

  const brandTranslateY = gateProgress.interpolate({ inputRange: [0, 1], outputRange: [0, -34] });
  const brandScale = gateProgress.interpolate({ inputRange: [0, 1], outputRange: [1, 0.86] });
  const formTranslateY = gateProgress.interpolate({ inputRange: [0, 1], outputRange: [30, 0] });

  return (
    <View style={styles.root}>
      <LoginBackground />
      <KeyboardAvoidingView
        style={styles.flex}
        behavior={Platform.OS === "ios" ? "padding" : undefined}
        keyboardVerticalOffset={insets.top}
      >
        <ScrollView
          contentContainerStyle={[
            styles.scrollContent,
            gateOpen ? styles.scrollContentOpen : styles.scrollContentArrival,
            { paddingTop: insets.top + logiNexus.spacing.xl, paddingBottom: insets.bottom + logiNexus.spacing.xl }
          ]}
          keyboardShouldPersistTaps="handled"
        >
          <Animated.View style={[styles.brandStage, { transform: [{ translateY: brandTranslateY }, { scale: brandScale }] }]}>
            <PulseSocBrandHeader compact={gateOpen} />
            <Animated.Text testID="pulse-gate-message" accessibilityLiveRegion="polite" style={[styles.livingMessage, { opacity: reducedMotion ? 1 : messageOpacity }]} maxFontSizeMultiplier={1.5}>
              {LIVING_MESSAGES[messageIndex]}
            </Animated.Text>
            <Text style={styles.signalLine} maxFontSizeMultiplier={1.5}>People. Signal. Purpose.</Text>
          </Animated.View>

          {!gateOpen ? (
            <Pressable
              accessibilityRole="button"
              accessibilityLabel="Tap to sign in or join the network"
              testID="pulse-gate-primary"
              onPress={openGate}
              style={({ pressed }) => [styles.gateButton, { opacity: pressed ? 0.78 : 1 }]}
            >
              <LinearGradient colors={["rgba(46,145,244,0.18)", "rgba(67,215,197,0.13)", "rgba(109,230,91,0.15)"]} start={{ x: 0, y: 0.5 }} end={{ x: 1, y: 0.5 }} style={styles.gateButtonFill}>
                <Text style={styles.gateButtonText}>Tap to sign in or join the network</Text>
                <Ionicons name="chevron-down" size={20} color={colors.accentStrong} />
              </LinearGradient>
            </Pressable>
          ) : (
            <Animated.View testID="pulse-gate-form" style={[styles.formStage, { opacity: gateProgress, transform: [{ translateY: formTranslateY }] }]}>
              {welcomeName ? <Text style={styles.returning}>Welcome back, {welcomeName}</Text> : null}
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

              <Pressable accessibilityRole="button" accessibilityLabel="Join the network" testID="create-account-button" onPress={() => navigation.navigate("Signup")} style={styles.joinAction}>
                <Text style={styles.joinMuted}>New to PulseSoc? <Text style={styles.joinLink}>Join the network</Text></Text>
                <Ionicons name="chevron-forward" size={16} color={colors.accentStrong} />
              </Pressable>

              {showBiometricButton ? (
                <BiometricLoginButton
                  kind={biometricCapability!.kind}
                  state={biometricState}
                  onPress={handleBiometricPress}
                  label={biometricButtonLabel}
                />
              ) : null}

              <View style={styles.secondaryActions}>
                <Pressable accessibilityRole="button" testID="forgot-password-link" onPress={() => navigation.navigate("AccountRecovery")} hitSlop={10}>
                  <Text style={styles.secondaryLink}>Forgot password?</Text>
                </Pressable>
                {hasRememberedAccounts ? (
                  <>
                    <Text style={styles.secondaryDot}>•</Text>
                    <Pressable
                      accessibilityRole="button"
                      testID="use-another-account-link"
                      onPress={() => {
                        setIdentifier("");
                        setPassword("");
                        setFormError(undefined);
                        formRef.current?.focusIdentifier();
                      }}
                      hitSlop={10}
                    >
                      <Text style={styles.secondaryLink}>Use another account</Text>
                    </Pressable>
                  </>
                ) : null}
              </View>

              <AuthStatusFooter biometricProtected={biometricEnabled} />
            </Animated.View>
          )}
        </ScrollView>
      </KeyboardAvoidingView>
    </View>
  );
}

function describeLoginError(error: unknown): string {
  if (error instanceof PulseApiError) {
    if (error.code === "request_unreachable" || error.status === 503) {
      return translate("errors:auth.unreachable");
    }
    if (error.status === 429) return translate("errors:auth.tooManyAttempts");
    if (error.status === 401 || error.status === 403) return translate("errors:auth.identifierMismatch");
    if (error.status >= 500) return translate("errors:auth.serverTrouble");
    return error.message || translate("errors:auth.unableToSignIn");
  }
  return error instanceof Error ? error.message : translate("errors:auth.unableToSignIn");
}

const styles = createThemedStyles(() => ({
  root: {
    flex: 1,
    backgroundColor: "transparent"
  },
  flex: {
    flex: 1
  },
  scrollContent: {
    flexGrow: 1,
    paddingHorizontal: logiNexus.spacing.xl
  },
  scrollContentArrival: {
    gap: 34,
    justifyContent: "center"
  },
  scrollContentOpen: {
    gap: 2,
    justifyContent: "flex-start"
  },
  brandStage: {
    alignItems: "center",
    gap: 8
  },
  livingMessage: {
    color: colors.text,
    fontSize: 23,
    fontWeight: "800",
    letterSpacing: -0.4,
    marginTop: 10,
    textAlign: "center"
  },
  signalLine: {
    color: colors.muted,
    fontSize: 14,
    fontWeight: "600",
    letterSpacing: 0.5,
    textAlign: "center"
  },
  gateButton: {
    borderColor: colors.accentStrong,
    borderRadius: 25,
    borderWidth: 1,
    overflow: "hidden"
  },
  gateButtonFill: {
    alignItems: "center",
    gap: 8,
    justifyContent: "center",
    minHeight: 82,
    paddingHorizontal: 20
  },
  gateButtonText: {
    color: colors.accentStrong,
    fontSize: 17,
    fontWeight: "800",
    textAlign: "center"
  },
  formStage: {
    gap: 17,
    marginTop: -24,
    paddingBottom: 8,
    width: "100%"
  },
  returning: {
    color: colors.accent,
    fontSize: 13,
    fontWeight: "700",
    textAlign: "center"
  },
  joinAction: {
    alignItems: "center",
    alignSelf: "center",
    flexDirection: "row",
    gap: 4,
    minHeight: 44,
    paddingHorizontal: 12
  },
  joinMuted: {
    color: colors.muted,
    fontSize: 13,
    fontWeight: "600"
  },
  joinLink: {
    color: colors.accentStrong,
    fontWeight: "800"
  },
  secondaryActions: {
    alignItems: "center",
    flexDirection: "row",
    gap: 10,
    justifyContent: "center",
    minHeight: 32
  },
  secondaryLink: {
    color: colors.muted,
    fontSize: 12,
    fontWeight: "700"
  },
  secondaryDot: {
    color: colors.border,
    fontSize: 12
  }
}));
