import { Ionicons } from "@expo/vector-icons";
import { NativeStackNavigationProp } from "@react-navigation/native-stack";
import { useNavigation } from "@react-navigation/native";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  Animated,
  Keyboard,
  KeyboardAvoidingView,
  Linking,
  Platform,
  Pressable,
  ScrollView,
  StyleSheet,
  Text,
  TextInput,
  View
} from "react-native";
import * as Haptics from "expo-haptics";
import { useSafeAreaInsets } from "react-native-safe-area-context";
import { PULSE_API_BASE_URL } from "../api/config";
import { registerAccount, useAuth, AuthState } from "../session/auth";
import { PulseApiError } from "../api/pulseApi";
import {
  classifyRegisterError,
  evaluatePassword,
  isCredentialsStepValid,
  isIdentityStepValid,
  normalizeEmail,
  normalizeFullName,
  normalizeUsername,
  validateEmail,
  validateFullName,
  validateUsername
} from "../auth/signupValidation";
import {
  BiometricCapability,
  confirmAndEnableBiometricLogin,
  getBiometricCapability
} from "../session/biometricAuth";
import { colors } from "../theme/colors";
import { logiNexus } from "../theme/logiNexus";
import { AuthStackParamList } from "../navigation/types";
import { LoginBackground } from "../components/auth/LoginBackground";
import { LogiNexusPanel } from "../components/LogiNexus";
import { SecureTextField } from "../components/auth/SecureTextField";
import { AuthStatusFooter } from "../components/auth/AuthStatusFooter";
import { SignupBrandHeader } from "../components/auth/signup/SignupBrandHeader";
import { SignupProgress } from "../components/auth/signup/SignupProgress";
import { PasswordStrengthMeter } from "../components/auth/signup/PasswordStrengthMeter";
import { PulsePrimaryButton } from "../components/auth/signup/PulsePrimaryButton";
import { VerifyEmailStep } from "../components/auth/signup/VerifyEmailStep";
import { useLogiNexusReducedMotion } from "../theme/logiNexusMotion";

type Step = "identity" | "credentials" | "verify" | "completion";
const PROGRESS_STEPS = ["Identity", "Secure", "Verify"];
const STEP_INDEX: Record<Step, number> = { identity: 0, credentials: 1, verify: 2, completion: 2 };

const TERMS_URL = `${PULSE_API_BASE_URL}/terms`;
const PRIVACY_URL = `${PULSE_API_BASE_URL}/privacy`;

export function SignupScreen() {
  const navigation = useNavigation<NativeStackNavigationProp<AuthStackParamList>>();
  const insets = useSafeAreaInsets();
  const reducedMotion = useLogiNexusReducedMotion();
  const { setAuthState } = useAuth();

  const [step, setStep] = useState<Step>("identity");
  const [fullName, setFullName] = useState("");
  const [username, setUsername] = useState("");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [acceptedLegal, setAcceptedLegal] = useState(false);
  const [emailOptIn, setEmailOptIn] = useState(false);

  const [submitting, setSubmitting] = useState(false);
  const [errors, setErrors] = useState<{ fullName?: string; username?: string; email?: string; password?: string; form?: string }>({});
  const [pendingEmail, setPendingEmail] = useState("");
  const [deliveryFailed, setDeliveryFailed] = useState(false);
  const [signedInState, setSignedInState] = useState<AuthState | null>(null);
  const [biometric, setBiometric] = useState<BiometricCapability | null>(null);
  const [enablingBiometric, setEnablingBiometric] = useState(false);

  const usernameRef = useRef<TextInput>(null);
  const emailRef = useRef<TextInput>(null);
  const passwordRef = useRef<TextInput>(null);
  const scrollRef = useRef<ScrollView>(null);
  const submittingRef = useRef(false);
  const fade = useRef(new Animated.Value(1)).current;

  useEffect(() => {
    getBiometricCapability().then(setBiometric).catch(() => undefined);
  }, []);

  // Subtle staggered reveal on step change (skipped under Reduce Motion so the
  // layout never animates for users who opt out).
  useEffect(() => {
    if (reducedMotion) {
      fade.setValue(1);
      return;
    }
    fade.setValue(0);
    Animated.timing(fade, { toValue: 1, duration: logiNexus.motion.reveal, useNativeDriver: true }).start();
  }, [step, fade, reducedMotion]);

  const passwordStrength = useMemo(() => evaluatePassword(password), [password]);
  const identityValid = isIdentityStepValid(fullName, username);
  const credentialsValid = isCredentialsStepValid(email, password, acceptedLegal);

  const goToStep = useCallback((next: Step) => {
    Keyboard.dismiss();
    setStep(next);
    scrollRef.current?.scrollTo({ y: 0, animated: true });
  }, []);

  const handleBack = useCallback(() => {
    if (step === "credentials") {
      goToStep("identity");
      return;
    }
    // From identity (or the post-submit steps) leave registration for Login.
    navigation.goBack();
  }, [step, goToStep, navigation]);

  const handleContinueIdentity = useCallback(() => {
    const nameCheck = validateFullName(fullName);
    const handleCheck = validateUsername(username);
    if (!nameCheck.valid || !handleCheck.valid) {
      setErrors({ fullName: nameCheck.message, username: handleCheck.message });
      Haptics.notificationAsync(Haptics.NotificationFeedbackType.Error).catch(() => undefined);
      return;
    }
    setErrors({});
    Haptics.selectionAsync().catch(() => undefined);
    goToStep("credentials");
  }, [fullName, username, goToStep]);

  const handleRegister = useCallback(async () => {
    if (submittingRef.current) return;
    const emailCheck = validateEmail(email);
    const pwStrength = evaluatePassword(password);
    if (!emailCheck.valid || !pwStrength.meetsMinimum || !acceptedLegal) {
      setErrors({
        email: emailCheck.valid ? undefined : emailCheck.message,
        password: pwStrength.meetsMinimum ? undefined : `Use at least 8 characters for your password.`,
        form: acceptedLegal ? undefined : "Please accept the Terms and Privacy Policy to continue."
      });
      Haptics.notificationAsync(Haptics.NotificationFeedbackType.Error).catch(() => undefined);
      return;
    }

    submittingRef.current = true;
    setSubmitting(true);
    setErrors({});
    Keyboard.dismiss();
    try {
      const outcome = await registerAccount({
        full_name: normalizeFullName(fullName),
        username: normalizeUsername(username),
        email: normalizeEmail(email),
        password,
        age_confirmed: acceptedLegal,
        email_opt_in: emailOptIn
      });
      if (outcome.kind === "signedIn") {
        // Rare phone-less/immediate path: still show the welcome + Face ID step.
        setSignedInState(outcome.state);
        Haptics.notificationAsync(Haptics.NotificationFeedbackType.Success).catch(() => undefined);
        goToStep("completion");
      } else {
        setPendingEmail(outcome.email);
        setDeliveryFailed(outcome.deliveryFailed);
        Haptics.notificationAsync(Haptics.NotificationFeedbackType.Success).catch(() => undefined);
        goToStep("verify");
      }
    } catch (error) {
      const message = error instanceof PulseApiError ? error.message : error instanceof Error ? error.message : "Couldn't create your account. Please try again.";
      const classified = classifyRegisterError(message);
      Haptics.notificationAsync(Haptics.NotificationFeedbackType.Error).catch(() => undefined);
      if (classified.target === "username") {
        setErrors({ username: classified.message });
        goToStep("identity");
        setTimeout(() => usernameRef.current?.focus(), 350);
      } else if (classified.target === "email") {
        setErrors({ email: classified.message });
        setTimeout(() => emailRef.current?.focus(), 150);
      } else if (classified.target === "password") {
        setErrors({ password: classified.message });
        setTimeout(() => passwordRef.current?.focus(), 150);
      } else {
        setErrors({ form: classified.message });
      }
    } finally {
      submittingRef.current = false;
      setSubmitting(false);
    }
  }, [fullName, username, email, password, acceptedLegal, emailOptIn, goToStep]);

  const handleConfirmed = useCallback((state: AuthState) => {
    setSignedInState(state);
    goToStep("completion");
  }, [goToStep]);

  const enterApp = useCallback(() => {
    if (signedInState) setAuthState(signedInState);
  }, [signedInState, setAuthState]);

  const handleEnableBiometric = useCallback(async () => {
    const userId = signedInState?.user?.user_id;
    if (!userId) {
      enterApp();
      return;
    }
    setEnablingBiometric(true);
    const enabled = await confirmAndEnableBiometricLogin(userId).catch(() => false);
    setEnablingBiometric(false);
    if (enabled) Haptics.notificationAsync(Haptics.NotificationFeedbackType.Success).catch(() => undefined);
    enterApp();
  }, [signedInState, enterApp, setAuthState]);

  const openLegal = useCallback((url: string) => {
    // Opens in the system browser; the RN screen stays mounted so all entered
    // registration state is preserved when the user returns.
    Linking.openURL(url).catch(() => undefined);
  }, []);

  const biometricLabel = biometric?.kind === "faceId" ? "Face ID" : biometric?.kind === "touchId" ? "Touch ID" : "biometric unlock";

  return (
    <View style={styles.root}>
      <LoginBackground />
      <KeyboardAvoidingView style={styles.flex} behavior={Platform.OS === "ios" ? "padding" : undefined} keyboardVerticalOffset={insets.top}>
        <ScrollView
          ref={scrollRef}
          contentContainerStyle={[styles.scroll, { paddingTop: insets.top + logiNexus.spacing.xl, paddingBottom: insets.bottom + logiNexus.spacing.xxl }]}
          keyboardShouldPersistTaps="handled"
          keyboardDismissMode="interactive"
        >
          <SignupBrandHeader onBack={handleBack} />

          <LogiNexusPanel style={styles.card}>
            {step !== "completion" ? <SignupProgress steps={PROGRESS_STEPS} currentIndex={STEP_INDEX[step]} /> : null}

            <Animated.View style={{ opacity: fade }}>
              {step === "identity" ? (
                <View style={styles.stepBody}>
                  <Text style={styles.title} maxFontSizeMultiplier={1.5}>Create your identity</Text>
                  <Text style={styles.subtitle} maxFontSizeMultiplier={1.6}>Join the network. Amplify your impact.</Text>

                  <Field label="Full name">
                    <SecureTextField
                      label="Full name"
                      iconName="person-outline"
                      autoCapitalize="words"
                      autoComplete="name"
                      textContentType="name"
                      returnKeyType="next"
                      testID="signup-fullname"
                      value={fullName}
                      errorText={errors.fullName}
                      onChangeText={(value) => { setFullName(value); if (errors.fullName) setErrors((e) => ({ ...e, fullName: undefined })); }}
                      onSubmitEditing={() => usernameRef.current?.focus()}
                    />
                  </Field>

                  <Field label="Username" hint="3–40 characters · letters, numbers, dots, underscores, dashes">
                    <SecureTextField
                      ref={usernameRef}
                      label="Username"
                      iconName="at-outline"
                      autoCapitalize="none"
                      autoComplete="username-new"
                      autoCorrect={false}
                      textContentType="username"
                      returnKeyType="next"
                      testID="signup-username"
                      value={username}
                      errorText={errors.username}
                      onChangeText={(value) => { setUsername(value); if (errors.username) setErrors((e) => ({ ...e, username: undefined })); }}
                      onSubmitEditing={handleContinueIdentity}
                    />
                  </Field>

                  <PulsePrimaryButton
                    label="Continue"
                    onPress={handleContinueIdentity}
                    disabled={!identityValid}
                    testID="signup-identity-continue"
                    accessibilityHint="Advances to securing your account"
                  />
                </View>
              ) : null}

              {step === "credentials" ? (
                <View style={styles.stepBody}>
                  <Text style={styles.title} maxFontSizeMultiplier={1.5}>Secure your account</Text>
                  <Text style={styles.subtitle} maxFontSizeMultiplier={1.6}>We'll email you a link to confirm it's really you.</Text>

                  <Field label="Email address">
                    <SecureTextField
                      ref={emailRef}
                      label="Email address"
                      iconName="mail-outline"
                      autoCapitalize="none"
                      autoComplete="email"
                      autoCorrect={false}
                      keyboardType="email-address"
                      textContentType="emailAddress"
                      returnKeyType="next"
                      testID="signup-email"
                      value={email}
                      errorText={errors.email}
                      onChangeText={(value) => { setEmail(value); if (errors.email) setErrors((e) => ({ ...e, email: undefined })); }}
                      onSubmitEditing={() => passwordRef.current?.focus()}
                    />
                  </Field>

                  <Field label="Password">
                    <SecureTextField
                      ref={passwordRef}
                      label="Password"
                      iconName="lock-closed-outline"
                      autoComplete="password-new"
                      textContentType="newPassword"
                      passwordRules="minlength: 8; required: lower; required: upper; required: digit;"
                      secureToggle
                      returnKeyType="done"
                      testID="signup-password"
                      value={password}
                      errorText={errors.password}
                      onChangeText={(value) => { setPassword(value); if (errors.password) setErrors((e) => ({ ...e, password: undefined })); }}
                    />
                  </Field>
                  <PasswordStrengthMeter strength={passwordStrength} />

                  <CheckRow
                    checked={acceptedLegal}
                    onToggle={() => { setAcceptedLegal((v) => !v); if (errors.form) setErrors((e) => ({ ...e, form: undefined })); }}
                    testID="signup-accept-legal"
                    accessibilityLabel="I am 16 or older and accept the Terms of Service and Privacy Policy"
                  >
                    <Text style={styles.consentText} maxFontSizeMultiplier={1.6}>
                      I'm 16+ and agree to the{" "}
                      <Text style={styles.consentLink} onPress={() => openLegal(TERMS_URL)}>Terms of Service</Text>
                      {" "}and{" "}
                      <Text style={styles.consentLink} onPress={() => openLegal(PRIVACY_URL)}>Privacy Policy</Text>.
                    </Text>
                  </CheckRow>

                  <CheckRow
                    checked={emailOptIn}
                    onToggle={() => setEmailOptIn((v) => !v)}
                    testID="signup-email-optin"
                    accessibilityLabel="Send me occasional PulseSoc product updates. Optional."
                  >
                    <Text style={styles.consentText} maxFontSizeMultiplier={1.6}>
                      Send me occasional PulseSoc product updates. <Text style={styles.optional}>(optional)</Text>
                    </Text>
                  </CheckRow>

                  {errors.form ? (
                    <Text style={styles.formError} accessibilityLiveRegion="assertive">{errors.form}</Text>
                  ) : null}

                  <PulsePrimaryButton
                    label="Create account"
                    onPress={() => void handleRegister()}
                    disabled={!credentialsValid}
                    busy={submitting}
                    testID="signup-create"
                    iconName="arrow-forward"
                    accessibilityHint="Creates your account and sends a confirmation email"
                  />
                </View>
              ) : null}

              {step === "verify" ? (
                <VerifyEmailStep
                  email={pendingEmail}
                  password={password}
                  deliveryFailed={deliveryFailed}
                  onConfirmed={handleConfirmed}
                  onEmailChanged={setPendingEmail}
                />
              ) : null}

              {step === "completion" ? (
                <View style={styles.stepBody}>
                  <View style={styles.successMark}>
                    <Ionicons name="checkmark-circle" size={44} color={colors.accent} />
                  </View>
                  <Text style={styles.title} maxFontSizeMultiplier={1.5}>Welcome to PulseSoc</Text>
                  <Text style={styles.subtitle} maxFontSizeMultiplier={1.6}>
                    Your account is ready{signedInState?.user?.username ? `, @${signedInState.user.username}` : ""}.
                  </Text>

                  {biometric?.available ? (
                    <>
                      <Text style={styles.biometricBlurb} maxFontSizeMultiplier={1.6}>
                        Turn on {biometricLabel} to unlock PulseSoc without typing your password next time. PulseSoc never receives or stores your face or fingerprint.
                      </Text>
                      <PulsePrimaryButton
                        label={`Enable ${biometricLabel}`}
                        onPress={() => void handleEnableBiometric()}
                        busy={enablingBiometric}
                        testID="signup-enable-biometric"
                        iconName="finger-print"
                      />
                      <Pressable accessibilityRole="button" hitSlop={8} onPress={enterApp} testID="signup-skip-biometric">
                        <Text style={styles.skip}>Not now — continue to PulseSoc</Text>
                      </Pressable>
                    </>
                  ) : (
                    <PulsePrimaryButton
                      label="Continue to PulseSoc"
                      onPress={enterApp}
                      testID="signup-enter-app"
                      iconName="arrow-forward"
                    />
                  )}
                </View>
              ) : null}
            </Animated.View>
          </LogiNexusPanel>

          {step === "identity" ? (
            <Pressable accessibilityRole="button" testID="signup-goto-login" hitSlop={8} onPress={() => navigation.navigate("Login")}>
              <Text style={styles.signinRow}>
                Already have an account? <Text style={styles.signinLink}>Sign in</Text>
              </Text>
            </Pressable>
          ) : null}

          <AuthStatusFooter biometricProtected={false} />
        </ScrollView>
      </KeyboardAvoidingView>
    </View>
  );
}

function Field({ label, hint, children }: { label: string; hint?: string; children: React.ReactNode }) {
  return (
    <View style={styles.fieldGroup}>
      <Text style={styles.fieldLabel} maxFontSizeMultiplier={1.4}>{label}</Text>
      {children}
      {hint ? <Text style={styles.fieldHint} maxFontSizeMultiplier={1.4}>{hint}</Text> : null}
    </View>
  );
}

function CheckRow({
  checked,
  onToggle,
  children,
  testID,
  accessibilityLabel
}: {
  checked: boolean;
  onToggle: () => void;
  children: React.ReactNode;
  testID?: string;
  accessibilityLabel?: string;
}) {
  return (
    <Pressable
      accessibilityRole="checkbox"
      accessibilityState={{ checked }}
      accessibilityLabel={accessibilityLabel}
      testID={testID}
      onPress={onToggle}
      style={styles.checkRow}
      hitSlop={6}
    >
      <View style={[styles.checkbox, checked && styles.checkboxOn]}>
        {checked ? <Ionicons name="checkmark" size={15} color={colors.background} /> : null}
      </View>
      <View style={styles.checkChild}>{children}</View>
    </Pressable>
  );
}

const styles = StyleSheet.create({
  root: { flex: 1, backgroundColor: colors.background },
  flex: { flex: 1 },
  scroll: {
    flexGrow: 1,
    gap: logiNexus.spacing.xl,
    justifyContent: "center",
    paddingHorizontal: logiNexus.spacing.xl
  },
  card: { gap: logiNexus.spacing.lg },
  stepBody: { gap: logiNexus.spacing.lg },
  title: { ...logiNexus.typography.title, color: colors.text, textAlign: "center" },
  subtitle: { color: colors.muted, fontSize: 14, fontWeight: "600", textAlign: "center", marginTop: -logiNexus.spacing.sm },
  fieldGroup: { gap: 6 },
  fieldLabel: { color: colors.text, fontSize: 13, fontWeight: "800", letterSpacing: 0.3 },
  fieldHint: { color: colors.muted, fontSize: 12, fontWeight: "600", paddingHorizontal: 2 },
  formError: { color: colors.danger, fontSize: 13, fontWeight: "700", textAlign: "center" },
  consentText: { color: colors.muted, fontSize: 13, fontWeight: "600", lineHeight: 19 },
  consentLink: { color: colors.accentStrong, fontWeight: "800", textDecorationLine: "underline" },
  optional: { color: colors.muted, fontWeight: "700", fontStyle: "italic" },
  checkRow: { alignItems: "flex-start", flexDirection: "row", gap: 10 },
  checkbox: {
    alignItems: "center",
    borderColor: colors.border,
    borderRadius: 6,
    borderWidth: 1.5,
    height: 24,
    justifyContent: "center",
    marginTop: 1,
    width: 24
  },
  checkboxOn: { backgroundColor: colors.accent, borderColor: colors.accent },
  checkChild: { flex: 1 },
  successMark: { alignItems: "center" },
  biometricBlurb: { color: colors.muted, fontSize: 13, fontWeight: "600", lineHeight: 19, textAlign: "center" },
  skip: { color: colors.muted, fontSize: 13, fontWeight: "800", textAlign: "center", textDecorationLine: "underline" },
  signinRow: { color: colors.muted, fontSize: 14, fontWeight: "600", textAlign: "center" },
  signinLink: { color: colors.accentStrong, fontWeight: "900", textDecorationLine: "underline" }
});
