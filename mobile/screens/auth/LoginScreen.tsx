import React, { useState } from "react";
import { Switch, Text, TextInput, TouchableOpacity, View } from "react-native";
import { NativeStackScreenProps } from "@react-navigation/native-stack";
import { ScreenScaffold } from "../../components/ScreenScaffold";
import { colors, screenStyles } from "../../components/theme";
import { AuthStackParamList } from "../../navigation/types";
import { useAuthStore } from "../../store/authStore";
import { PulseApiError, isOfflineError } from "../../services/apiClient";
import { validateIdentifier } from "../../services/validation";

type Props = NativeStackScreenProps<AuthStackParamList, "Login">;

export function LoginScreen({ navigation }: Props) {
  const login = useAuthStore(state => state.login);
  const [identifier, setIdentifier] = useState("");
  const [password, setPassword] = useState("");
  const [remember, setRemember] = useState(true);
  const [loading, setLoading] = useState(false);
  const [message, setMessage] = useState("");

  async function submit() {
    const validation = validateIdentifier(identifier);
    if (!validation.valid) {
      setMessage(validation.message);
      return;
    }
    if (!password) {
      setMessage("Password is required.");
      return;
    }
    setMessage("Signing in...");
    setLoading(true);
    try {
      await login(identifier, password, remember);
    } catch (error) {
      const text = error instanceof Error ? error.message : "Login failed.";
      setMessage(isOfflineError(error) ? "You appear to be offline. Check your connection and try again." : text);
      if (error instanceof PulseApiError && error.code === "email_not_confirmed") {
        navigation.navigate("EmailConfirmationPending", { email: identifier.includes("@") ? identifier : undefined });
      }
    } finally {
      setLoading(false);
    }
  }

  return (
    <ScreenScaffold title="Pulse" subtitle="Sign in with your email or username.">
      <TextInput style={screenStyles.input} value={identifier} onChangeText={setIdentifier} autoCapitalize="none" autoCorrect={false} keyboardType="email-address" placeholder="Email or username" placeholderTextColor="#7890a8" />
      <TextInput style={screenStyles.input} value={password} onChangeText={setPassword} secureTextEntry placeholder="Password" placeholderTextColor="#7890a8" />
      <View style={{ ...screenStyles.card, flexDirection: "row", alignItems: "center", justifyContent: "space-between" }}>
        <Text style={screenStyles.cardTitle}>Remember session</Text>
        <Switch value={remember} onValueChange={setRemember} trackColor={{ true: colors.accent, false: colors.border }} />
      </View>
      <TouchableOpacity style={[screenStyles.button, loading && { opacity: 0.7 }]} onPress={submit} disabled={loading}>
        <Text style={screenStyles.buttonText}>{loading ? "Signing in..." : "Log in"}</Text>
      </TouchableOpacity>
      <TouchableOpacity style={screenStyles.secondaryButton} onPress={() => navigation.navigate("Signup")}>
        <Text style={screenStyles.secondaryButtonText}>Create account</Text>
      </TouchableOpacity>
      <TouchableOpacity style={screenStyles.secondaryButton} onPress={() => navigation.navigate("ForgotPassword")}>
        <Text style={screenStyles.secondaryButtonText}>Forgot password</Text>
      </TouchableOpacity>
      {message ? <Text style={[screenStyles.muted, { marginTop: 12 }]}>{message}</Text> : null}
    </ScreenScaffold>
  );
}
