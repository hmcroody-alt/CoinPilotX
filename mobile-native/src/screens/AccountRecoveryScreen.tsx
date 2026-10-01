import { NativeStackScreenProps } from "@react-navigation/native-stack";
import { useState } from "react";
import { Alert, Pressable, StyleSheet, Text, TextInput, View } from "react-native";
import { requestPasswordRecovery, resendEmailConfirmation } from "../api/auth";
import { AuthStackParamList } from "../navigation/types";
import { colors } from "../theme/colors";
import { createThemedStyles } from "../theme/themedStyles";

type Props = NativeStackScreenProps<AuthStackParamList, "AccountRecovery">;

export function AccountRecoveryScreen({ navigation }: Props) {
  const [email, setEmail] = useState("");
  const [busy, setBusy] = useState<"password" | "verification" | "">("");

  async function recoverPassword() {
    const value = email.trim();
    if (!value) return Alert.alert("Email required", "Enter the email address on your existing PulseSoc account.");
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
    const value = email.trim();
    if (!value) return Alert.alert("Email required", "Enter the email address on your existing PulseSoc account.");
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
      <Text style={styles.title}>Recover access</Text>
      <Text style={styles.copy}>Use the same email connected to your current PulseSoc account. Recovery never creates a new user or profile.</Text>
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
      <Pressable accessibilityRole="button" style={styles.primary} disabled={Boolean(busy)} onPress={recoverPassword}>
        <Text style={styles.primaryText}>{busy === "password" ? "Sending recovery…" : "Reset password"}</Text>
      </Pressable>
      <Pressable accessibilityRole="button" style={styles.secondary} disabled={Boolean(busy)} onPress={resendVerification}>
        <Text style={styles.secondaryText}>{busy === "verification" ? "Sending verification…" : "Resend email verification"}</Text>
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
