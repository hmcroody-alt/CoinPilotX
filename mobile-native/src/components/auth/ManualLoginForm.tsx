import { forwardRef, useImperativeHandle, useRef } from "react";
import { ActivityIndicator, Pressable, StyleSheet, Text, TextInput, View } from "react-native";
import { colors } from "../../theme/colors";
import { logiNexus } from "../../theme/logiNexus";
import { SecureTextField } from "./SecureTextField";

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
        label="Email or username"
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
        label="Password"
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
        accessibilityLabel="Sign in"
        accessibilityState={{ disabled: !canSubmit, busy: submitting }}
        disabled={!canSubmit}
        testID="login-submit"
        style={({ pressed }) => [styles.submit, { opacity: !canSubmit ? 0.5 : pressed ? 0.85 : 1 }]}
        onPress={onSubmit}
      >
        {submitting ? <ActivityIndicator color={colors.background} /> : <Text style={styles.submitText}>Sign in</Text>}
      </Pressable>
    </View>
  );
});

const styles = StyleSheet.create({
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
    alignItems: "center",
    backgroundColor: colors.accentStrong,
    borderRadius: logiNexus.radius.medium,
    justifyContent: "center",
    minHeight: 54
  },
  submitText: {
    ...logiNexus.typography.button,
    fontSize: 15,
    color: colors.background
  }
});
