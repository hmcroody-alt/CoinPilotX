import { forwardRef, useImperativeHandle, useRef } from "react";
import { ActivityIndicator, Pressable, StyleSheet, Text, TextInput, View } from "react-native";
import { LinearGradient } from "expo-linear-gradient";
import { colors } from "../../theme/colors";
import { logiNexus } from "../../theme/logiNexus";
import { SecureTextField } from "./SecureTextField";
import { createThemedStyles } from "../../theme/themedStyles";
import { useTranslation } from "../../i18n";

export type ManualLoginFormHandle = {
  focusIdentifier: () => void;
};

export const ManualLoginForm = forwardRef<
  ManualLoginFormHandle,
  {
    identifier: string;
    password: string;
    onChangeIdentifier: (value: string) => void;
    onChangePassword: (value: string) => void;
    onSubmit: () => void;
    submitting: boolean;
    identifierError?: string;
    passwordError?: string;
    formError?: string;
  }
>(function ManualLoginForm(
  { identifier, password, onChangeIdentifier, onChangePassword, onSubmit, submitting, identifierError, passwordError, formError },
  ref
) {
  const { t } = useTranslation();
  const identifierRef = useRef<TextInput>(null);
  const passwordRef = useRef<TextInput>(null);

  useImperativeHandle(ref, () => ({
    focusIdentifier: () => identifierRef.current?.focus()
  }));

  const canSubmit = identifier.trim().length > 0 && password.length > 0 && !submitting;

  return (
    <View style={styles.root}>
      <SecureTextField
        ref={identifierRef}
        label={t("auth:signIn.emailOrUsernameLabel")}
        iconName="mail-outline"
        autoCapitalize="none"
        autoComplete="username"
        autoCorrect={false}
        keyboardType="email-address"
        returnKeyType="next"
        testID="login-identifier"
        value={identifier}
        errorText={identifierError}
        onChangeText={onChangeIdentifier}
        onSubmitEditing={() => passwordRef.current?.focus()}
      />
      <SecureTextField
        ref={passwordRef}
        label={t("auth:signIn.passwordLabel")}
        iconName="lock-closed-outline"
        autoComplete="current-password"
        returnKeyType="go"
        secureToggle
        testID="login-password"
        value={password}
        errorText={passwordError}
        onChangeText={onChangePassword}
        onSubmitEditing={onSubmit}
      />
      {formError ? (
        <Text style={styles.formError} accessibilityLiveRegion="assertive" testID="login-form-error">
          {formError}
        </Text>
      ) : null}
      <Pressable
        accessibilityRole="button"
        accessibilityLabel={t("auth:signIn.submit")}
        accessibilityState={{ disabled: !canSubmit, busy: submitting }}
        disabled={!canSubmit}
        testID="login-submit"
        style={({ pressed }) => [styles.submit, { opacity: !canSubmit ? 0.42 : pressed ? 0.82 : 1 }]}
        onPress={onSubmit}
      >
        <LinearGradient colors={["#2E91F4", "#43D7C5", "#6DE65B"]} start={{ x: 0, y: 0.5 }} end={{ x: 1, y: 0.5 }} style={styles.submitGradient}>
          {submitting ? <ActivityIndicator color={colors.background} /> : <Text style={styles.submitText}>Sign in</Text>}
        </LinearGradient>
      </Pressable>
    </View>
  );
});

const styles = createThemedStyles(() => ({
  root: {
    gap: logiNexus.spacing.md
  },
  formError: {
    color: colors.danger,
    fontSize: 13,
    fontWeight: "700",
    textAlign: "center"
  },
  submit: {
    borderRadius: logiNexus.radius.medium,
    minHeight: 54,
    overflow: "hidden"
  },
  submitGradient: {
    alignItems: "center",
    flex: 1,
    justifyContent: "center",
    minHeight: 54
  },
  submitText: {
    ...logiNexus.typography.button,
    fontSize: 15,
    color: colors.background
  }
}));
