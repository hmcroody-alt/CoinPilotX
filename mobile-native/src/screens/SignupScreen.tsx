import { useState } from "react";
import { Alert, Pressable, StyleSheet, Text, TextInput, View } from "react-native";
import { createAccount, useAuth } from "../session/auth";
import { colors } from "../theme/colors";

export function SignupScreen() {
  const { setAuthState } = useAuth();
  const [fullName, setFullName] = useState("");
  const [username, setUsername] = useState("");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [loading, setLoading] = useState(false);

  async function submit() {
    setLoading(true);
    try {
      setAuthState(
        await createAccount({
          full_name: fullName.trim(),
          username: username.trim(),
          email: email.trim(),
          password
        })
      );
    } catch (error) {
      Alert.alert("Signup failed", error instanceof Error ? error.message : "Unable to create account.");
    } finally {
      setLoading(false);
    }
  }

  return (
    <View style={styles.root}>
      <Text style={styles.title}>Create PulseSoc account</Text>
      <TextInput placeholder="Full name" placeholderTextColor={colors.muted} style={styles.input} value={fullName} onChangeText={setFullName} />
      <TextInput autoCapitalize="none" placeholder="Username" placeholderTextColor={colors.muted} style={styles.input} value={username} onChangeText={setUsername} />
      <TextInput autoCapitalize="none" keyboardType="email-address" placeholder="Email" placeholderTextColor={colors.muted} style={styles.input} value={email} onChangeText={setEmail} />
      <TextInput placeholder="Password" placeholderTextColor={colors.muted} secureTextEntry style={styles.input} value={password} onChangeText={setPassword} />
      <Pressable style={styles.button} onPress={submit} disabled={loading}>
        <Text style={styles.buttonText}>{loading ? "Creating" : "Create account"}</Text>
      </Pressable>
    </View>
  );
}

const styles = StyleSheet.create({
  root: {
    flex: 1,
    justifyContent: "center",
    gap: 14,
    padding: 22,
    backgroundColor: colors.background
  },
  title: {
    color: colors.text,
    fontSize: 28,
    fontWeight: "900"
  },
  input: {
    backgroundColor: colors.surface,
    borderColor: colors.border,
    borderRadius: 8,
    borderWidth: StyleSheet.hairlineWidth,
    color: colors.text,
    minHeight: 52,
    paddingHorizontal: 14
  },
  button: {
    alignItems: "center",
    backgroundColor: colors.accent,
    borderRadius: 8,
    minHeight: 52,
    justifyContent: "center"
  },
  buttonText: {
    color: "#08110f",
    fontWeight: "800"
  }
});
