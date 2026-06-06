import React, { useState } from "react";
import { Text, TextInput, TouchableOpacity } from "react-native";
import { NativeStackScreenProps } from "@react-navigation/native-stack";
import { ScreenScaffold } from "../../components/ScreenScaffold";
import { screenStyles } from "../../components/theme";
import { AuthStackParamList } from "../../navigation/types";
import { useAuthStore } from "../../store/authStore";

type Props = NativeStackScreenProps<AuthStackParamList, "Login">;

export function LoginScreen({ navigation }: Props) {
  const login = useAuthStore(state => state.login);
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [message, setMessage] = useState("");

  async function submit() {
    setMessage("Signing in...");
    try {
      await login(email, password);
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "Login failed.");
    }
  }

  return (
    <ScreenScaffold title="Pulse" subtitle="Sign in with your existing CoinPilotX Pulse account.">
      <TextInput style={screenStyles.input} value={email} onChangeText={setEmail} autoCapitalize="none" keyboardType="email-address" placeholder="Email" placeholderTextColor="#7890a8" />
      <TextInput style={screenStyles.input} value={password} onChangeText={setPassword} secureTextEntry placeholder="Password" placeholderTextColor="#7890a8" />
      <TouchableOpacity style={screenStyles.button} onPress={submit}>
        <Text style={screenStyles.buttonText}>Log in</Text>
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
