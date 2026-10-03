/**
 * The two questions a provider cannot answer.
 *
 * Apple and Google both prove who somebody is. Neither can say whether that
 * person is old enough to use PulseSoc, or whether they agree to the Terms.
 * The server therefore refuses to create an account on the strength of a
 * provider token alone -- it answers `federated_signup_required` with a signed
 * ticket, and this screen is the only place that ticket can be spent.
 *
 * ## Why the answers are not pre-ticked
 *
 * Every control on this screen starts off. A pre-ticked consent box records an
 * agreement the member never made, which is both the thing the server's refusal
 * exists to prevent and worthless as evidence if it is ever challenged. The
 * continue button stays disabled until both are deliberately turned on, so the
 * only way to get past this screen is to have actually answered it.
 *
 * ## Why this is not the Signup screen
 *
 * `SignupScreen` collects an email, a password, and a verification code. None
 * of those exist here: the provider has already proven the email, and a
 * federated account has no password. Reusing it would mean a screen whose
 * every field is conditional on how the member arrived, and the conditional
 * that matters -- "did anyone consent?" -- would be the easiest one to get
 * wrong.
 */

import { useCallback, useState } from "react";
import { useNavigation, useRoute, RouteProp } from "@react-navigation/native";
import { NativeStackNavigationProp } from "@react-navigation/native-stack";
import { Keyboard, KeyboardAvoidingView, Platform, Pressable, ScrollView, StyleSheet, Switch, Text, TextInput, View } from "react-native";
import { useSafeAreaInsets } from "react-native-safe-area-context";
import { completeFederatedSignup, useAuth } from "../session/auth";
import { PulseApiError } from "../api/pulseApi";
import { translate, useTranslation } from "../i18n";
import { colors } from "../theme/colors";
import { logiNexus } from "../theme/logiNexus";
import { AuthStackParamList } from "../navigation/types";
import { LoginBackground } from "../components/auth/LoginBackground";
import { PulsePrimaryButton } from "../components/auth/signup/PulsePrimaryButton";

const PROVIDER_LABELS: Record<string, string> = { apple: "Apple", google: "Google" };

export function FederatedSignupScreen() {
  const navigation = useNavigation<NativeStackNavigationProp<AuthStackParamList>>();
  const route = useRoute<RouteProp<AuthStackParamList, "FederatedSignup">>();
  const insets = useSafeAreaInsets();
  const { t } = useTranslation();
  const { setAuthState } = useAuth();
  const ticket = route.params.ticket;

  const [ageConfirmed, setAgeConfirmed] = useState(false);
  const [termsAccepted, setTermsAccepted] = useState(false);
  const [emailOptIn, setEmailOptIn] = useState(false);
  const [country, setCountry] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [formError, setFormError] = useState<string | undefined>();

  const providerLabel = PROVIDER_LABELS[ticket.provider] || ticket.provider;
  const canSubmit = ageConfirmed && termsAccepted && !submitting;

  const submit = useCallback(async () => {
    if (!canSubmit) return;
    Keyboard.dismiss();
    setFormError(undefined);
    setSubmitting(true);
    try {
      const state = await completeFederatedSignup({
        signup_ticket: ticket.signup_ticket,
        // Passed through as the member actually answered. The button cannot be
        // reached with either one false, but they are still sent as read rather
        // than as literals -- a hardcoded `true` here would be invisible and
        // would survive any future change to the button's enabled condition.
        age_confirmed: ageConfirmed,
        terms_accepted: termsAccepted,
        email_opt_in: emailOptIn,
        ...(country.trim() ? { country: country.trim() } : {})
      });
      setAuthState(state);
    } catch (error) {
      setFormError(describeSignupError(error));
      // An expired or already-spent ticket cannot be retried on this screen --
      // there is nothing here that would make the next attempt different. Send
      // them back to tap the provider again, which mints a fresh one.
      if (error instanceof PulseApiError && isTicketDead(error)) {
        setTimeout(() => navigation.navigate("Login"), 1800);
      }
    } finally {
      setSubmitting(false);
    }
  }, [canSubmit, ticket, ageConfirmed, termsAccepted, emailOptIn, country, setAuthState, navigation]);

  return (
    <View style={styles.root}>
      <LoginBackground />
      <KeyboardAvoidingView style={styles.flex} behavior={Platform.OS === "ios" ? "padding" : undefined} keyboardVerticalOffset={insets.top}>
        <ScrollView
          contentContainerStyle={[styles.content, { paddingTop: insets.top + logiNexus.spacing.xl, paddingBottom: insets.bottom + logiNexus.spacing.xl }]}
          keyboardShouldPersistTaps="handled"
          testID="federated-signup-screen"
        >
          <Text style={styles.title} maxFontSizeMultiplier={1.5}>
            {t("auth:federatedSignup.title")}
          </Text>
          <Text style={styles.subtitle} maxFontSizeMultiplier={1.5}>
            {t("auth:federatedSignup.subtitle", { provider: providerLabel })}
          </Text>

          {/* The verified address, shown so the member can see which identity
              they are about to create an account with -- Apple's Hide My Email
              means this may be a relay address they have never seen before, and
              a signup that never showed it would be a surprise later. */}
          {ticket.email ? (
            <View style={styles.identityCard}>
              <Text style={styles.identityLabel} maxFontSizeMultiplier={1.4}>
                {t("auth:federatedSignup.verifiedEmail", { provider: providerLabel })}
              </Text>
              <Text style={styles.identityValue} testID="federated-signup-email" maxFontSizeMultiplier={1.4}>
                {ticket.email}
              </Text>
            </View>
          ) : null}

          <ConsentRow
            testID="federated-age-switch"
            label={t("auth:federatedSignup.ageConfirm")}
            value={ageConfirmed}
            onValueChange={setAgeConfirmed}
          />
          <ConsentRow
            testID="federated-terms-switch"
            label={t("auth:federatedSignup.termsAccept")}
            value={termsAccepted}
            onValueChange={setTermsAccepted}
          />
          <ConsentRow
            testID="federated-marketing-switch"
            label={t("auth:federatedSignup.emailOptIn")}
            value={emailOptIn}
            onValueChange={setEmailOptIn}
            optional
          />

          <View style={styles.field}>
            <Text style={styles.fieldLabel} maxFontSizeMultiplier={1.4}>
              {t("auth:federatedSignup.countryLabel")}
            </Text>
            <TextInput
              testID="federated-country-input"
              style={styles.input}
              value={country}
              onChangeText={setCountry}
              autoComplete="country"
              autoCorrect={false}
              placeholder={t("auth:federatedSignup.countryPlaceholder")}
              placeholderTextColor={colors.muted}
              accessibilityLabel={t("auth:federatedSignup.countryLabel")}
            />
          </View>

          {formError ? (
            <Text style={styles.error} testID="federated-signup-error" accessibilityLiveRegion="polite" maxFontSizeMultiplier={1.5}>
              {formError}
            </Text>
          ) : null}

          <PulsePrimaryButton
            testID="federated-signup-submit"
            label={t("auth:federatedSignup.submit")}
            onPress={submit}
            disabled={!canSubmit}
            busy={submitting}
          />

          <Pressable
            accessibilityRole="button"
            testID="federated-signup-cancel"
            onPress={() => navigation.navigate("Login")}
            style={styles.cancel}
            hitSlop={10}
          >
            <Text style={styles.cancelText} maxFontSizeMultiplier={1.4}>
              {t("common:actions.cancel")}
            </Text>
          </Pressable>
        </ScrollView>
      </KeyboardAvoidingView>
    </View>
  );
}

function ConsentRow({
  label,
  value,
  onValueChange,
  testID,
  optional
}: {
  label: string;
  value: boolean;
  onValueChange: (next: boolean) => void;
  testID: string;
  optional?: boolean;
}) {
  return (
    <View style={styles.consentRow}>
      <Text style={styles.consentLabel} maxFontSizeMultiplier={1.6}>
        {label}
        {optional ? <Text style={styles.optional}> {translate("auth:federatedSignup.optional")}</Text> : null}
      </Text>
      <Switch
        testID={testID}
        value={value}
        onValueChange={onValueChange}
        accessibilityLabel={label}
        trackColor={{ false: colors.border, true: colors.accentStrong }}
      />
    </View>
  );
}

/**
 * Whether the ticket itself is the problem, rather than the answers.
 *
 * Both of these mean the proof has run out: a ticket is short-lived and
 * single-use, so the member has either taken too long or tapped twice. Neither
 * is fixable by changing anything on this screen.
 */
function isTicketDead(error: PulseApiError): boolean {
  return error.code === "signup_ticket_expired" || error.code === "signup_ticket_invalid";
}

function describeSignupError(error: unknown): string {
  if (error instanceof PulseApiError) {
    if (error.code === "request_unreachable" || error.status === 503) {
      return translate("errors:auth.unreachable");
    }
    if (isTicketDead(error)) return translate("errors:auth.federatedTicketExpired");
    if (error.code === "account_restricted") return translate("errors:auth.accountRestricted");
    if (error.status === 429) return translate("errors:auth.tooManyAttempts");
    if (error.status >= 500) return translate("errors:auth.serverTrouble");
    return error.message || translate("errors:auth.registerFailed");
  }
  return translate("errors:auth.registerFailed");
}

const styles = StyleSheet.create({
  root: { backgroundColor: "transparent", flex: 1 },
  flex: { flex: 1 },
  content: {
    flexGrow: 1,
    gap: 16,
    justifyContent: "center",
    paddingHorizontal: logiNexus.spacing.xl
  },
  title: {
    color: colors.text,
    fontSize: 26,
    fontWeight: "800",
    letterSpacing: -0.5,
    textAlign: "center"
  },
  subtitle: {
    color: colors.muted,
    fontSize: 14,
    fontWeight: "600",
    textAlign: "center"
  },
  identityCard: {
    backgroundColor: "rgba(255,255,255,0.06)",
    borderColor: colors.border,
    borderRadius: 14,
    borderWidth: StyleSheet.hairlineWidth,
    gap: 4,
    padding: 14
  },
  identityLabel: { color: colors.muted, fontSize: 12, fontWeight: "700" },
  identityValue: { color: colors.text, fontSize: 15, fontWeight: "700" },
  consentRow: {
    alignItems: "center",
    flexDirection: "row",
    gap: 12,
    justifyContent: "space-between",
    minHeight: 44
  },
  consentLabel: { color: colors.text, flex: 1, fontSize: 14, fontWeight: "600" },
  optional: { color: colors.muted, fontWeight: "600" },
  field: { gap: 6 },
  fieldLabel: { color: colors.muted, fontSize: 12, fontWeight: "700" },
  input: {
    backgroundColor: "rgba(255,255,255,0.06)",
    borderColor: colors.border,
    borderRadius: 12,
    borderWidth: StyleSheet.hairlineWidth,
    color: colors.text,
    fontSize: 15,
    minHeight: 48,
    paddingHorizontal: 14
  },
  error: { color: colors.danger, fontSize: 13, fontWeight: "700", textAlign: "center" },
  cancel: { alignSelf: "center", minHeight: 44, justifyContent: "center", paddingHorizontal: 16 },
  cancelText: { color: colors.muted, fontSize: 13, fontWeight: "700" }
});
