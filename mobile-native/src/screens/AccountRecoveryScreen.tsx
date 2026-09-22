import { NativeStackScreenProps } from "@react-navigation/native-stack";
import { useState } from "react";
import { Alert, Pressable, StyleSheet, Text, TextInput, View } from "react-native";
import { requestPasswordRecovery, resendEmailConfirmation } from "../api/auth";
import { AuthStackParamList } from "../navigation/types";
import { colors } from "../theme/colors";
import { createThemedStyles } from "../theme/themedStyles";

type Props = NativeStackScreenProps<AuthStackParamList, "AccountRecovery">;

/**
 * Mirrors the server's `is_valid_email` so the two surfaces agree on what an
 * address is. Checked here as well as there because "Check your email" is a
 * promise the client makes; it must not make it about a string that could never
 * be mailed. Deliberately no stricter than the server -- a local rule the server
 * would have accepted would reject real addresses the account was created with.
 */
const EMAIL_SHAPE = /^[^@\s]+@[^@\s]+\.[^@\s]+$/;

export function AccountRecoveryScreen({ navigation, route }: Props) {
  const [email, setEmail] = useState(route.params?.email ?? "");
  const [busy, setBusy] = useState<"password" | "verification" | "">("");

  // Bounced here by an unconfirmed login, resending is the action they need.
  const verificationFirst = route.params?.intent === "verification";

  function readAddress(): string | null {
    const value = email.trim();
    if (!EMAIL_SHAPE.test(value)) {
      Alert.alert("Enter your email address", "Use the full email address on your PulseSoc account, like you@example.com.");
      return null;
    }
    return value;
  }

  async function recoverPassword() {
    const value = readAddress();
    if (!value) return;
    setBusy("password");
    try {
      const result = await requestPasswordRecovery(value);
      Alert.alert("Check your email", result.message || "If an account exists, password recovery has been sent.");
    } catch (error) {
      Alert.alert("Recovery unavailable", error instanceof Error ? error.message : "Try again in a moment.");
    } finally {
      setBusy("");
    }
  }

  async function resendVerification() {
    const value = readAddress();
    if (!value) return;
    setBusy("verification");
    try {
      const result = await resendEmailConfirmation(value);
      Alert.alert("Verification", result.message || "If the account needs confirmation, a new email has been sent.");
    } catch (error) {
      Alert.alert("Verification unavailable", error instanceof Error ? error.message : "Try again in a moment.");
    } finally {
      setBusy("");
    }
  }

  return (
    <View style={styles.root}>
      <Text style={styles.eyebrow}>EXISTING PULSESOC ACCOUNT</Text>
      <Text style={styles.title}>{verificationFirst ? "Verify your email" : "Recover access"}</Text>
      <Text style={styles.copy}>
        {verificationFirst
          ? "Your account still needs its email confirmed before you can sign in. Send yourself a fresh confirmation link."
          : "Use the same email connected to your current PulseSoc account. Recovery never creates a new user or profile."}
      </Text>
      <TextInput
        accessibilityLabel="Existing account email"
        autoCapitalize="none"
        autoComplete="email"
        keyboardType="email-address"
        placeholder="Email address"
        placeholderTextColor={colors.muted}
        style={styles.input}
        value={email}
        onChangeText={setEmail}
      />
      <Pressable
        accessibilityRole="button"
        testID="resend-verification-button"
        style={verificationFirst ? styles.primary : styles.secondary}
        disabled={Boolean(busy)}
        onPress={resendVerification}
      >
        <Text style={verificationFirst ? styles.primaryText : styles.secondaryText}>
          {busy === "verification" ? "Sending verification…" : "Resend email verification"}
        </Text>
      </Pressable>
      <Pressable
        accessibilityRole="button"
        testID="reset-password-button"
        style={verificationFirst ? styles.secondary : styles.primary}
        disabled={Boolean(busy)}
        onPress={recoverPassword}
      >
        <Text style={verificationFirst ? styles.secondaryText : styles.primaryText}>
          {busy === "password" ? "Sending recovery…" : "Reset password"}
        </Text>
      </Pressable>
      <Pressable accessibilityRole="button" onPress={() => navigation.goBack()}>
        <Text style={styles.link}>Back to sign in</Text>
      </Pressable>
    </View>
  );
}

const styles = createThemedStyles(() => ({
  root: { flex: 1, justifyContent: "center", gap: 14, padding: 22, backgroundColor: "transparent" },
  eyebrow: { color: colors.accentStrong, fontSize: 12, fontWeight: "900", letterSpacing: 1.2 },
  title: { color: colors.text, fontSize: 34, fontWeight: "900" },
  copy: { color: colors.muted, fontSize: 16, lineHeight: 23, marginBottom: 8 },
  input: { backgroundColor: colors.surface, borderColor: colors.border, borderRadius: 8, borderWidth: StyleSheet.hairlineWidth, color: colors.text, minHeight: 52, paddingHorizontal: 14 },
  primary: { alignItems: "center", backgroundColor: colors.accent, borderRadius: 8, minHeight: 52, justifyContent: "center" },
  primaryText: { color: "#08110f", fontWeight: "800" },
  secondary: { alignItems: "center", borderColor: colors.border, borderRadius: 8, borderWidth: 1, minHeight: 52, justifyContent: "center" },
  secondaryText: { color: colors.text, fontWeight: "800" },
  link: { color: colors.accentStrong, fontWeight: "700", textAlign: "center", padding: 8 }
}));
