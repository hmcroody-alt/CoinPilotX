import React, { useState } from "react";
import { Text, TextInput, TouchableOpacity } from "react-native";
import { NativeStackScreenProps } from "@react-navigation/native-stack";
import { ScreenScaffold } from "../../components/ScreenScaffold";
import { screenStyles } from "../../components/theme";
import { AuthStackParamList } from "../../navigation/types";
import { useAuthStore } from "../../store/authStore";
import { isOfflineError } from "../../services/apiClient";
import { validateSignup } from "../../services/validation";

type Props = NativeStackScreenProps<AuthStackParamList, "Signup">;

export function SignupScreen({ navigation }: Props) {
  const signup = useAuthStore(state => state.signup);
  const [fullName, setFullName] = useState("");
  const [username, setUsername] = useState("");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [confirmPassword, setConfirmPassword] = useState("");
  const [loading, setLoading] = useState(false);
  const [message, setMessage] = useState("");

  async function submit() {
    const validation = validateSignup({ fullName, username, email, password, confirmPassword });
    if (!validation.valid) {
      setMessage(validation.message);
      return;
    }
    setMessage("Creating your Pulse account...");
    setLoading(true);
    try {
      await signup({ full_name: fullName, username, email, password });
      navigation.replace("EmailConfirmationPending", { email });
    } catch (error) {
      setMessage(isOfflineError(error) ? "You appear to be offline. Check your connection and try again." : error instanceof Error ? error.message : "Signup failed.");
    } finally {
      setLoading(false);
    }
  }

  return (
    <ScreenScaffold title="Signup" subtitle="Create a Pulse account through the existing CoinPilotX auth API.">
      <TextInput style={screenStyles.input} value={fullName} onChangeText={setFullName} placeholder="Full name" placeholderTextColor="#7890a8" />
      <TextInput style={screenStyles.input} value={username} onChangeText={setUsername} autoCapitalize="none" placeholder="Username" placeholderTextColor="#7890a8" />
      <TextInput style={screenStyles.input} value={email} onChangeText={setEmail} autoCapitalize="none" keyboardType="email-address" placeholder="Email" placeholderTextColor="#7890a8" />
      <TextInput style={screenStyles.input} value={password} onChangeText={setPassword} secureTextEntry placeholder="Password" placeholderTextColor="#7890a8" />
      <TextInput style={screenStyles.input} value={confirmPassword} onChangeText={setConfirmPassword} secureTextEntry placeholder="Confirm password" placeholderTextColor="#7890a8" />
      <TouchableOpacity style={[screenStyles.button, loading && { opacity: 0.7 }]} onPress={submit} disabled={loading}>
        <Text style={screenStyles.buttonText}>{loading ? "Creating..." : "Create account"}</Text>
      </TouchableOpacity>
      <TouchableOpacity style={screenStyles.secondaryButton} onPress={() => navigation.goBack()}>
        <Text style={screenStyles.secondaryButtonText}>Back to login</Text>
      </TouchableOpacity>
      {message ? <Text style={[screenStyles.muted, { marginTop: 12 }]}>{message}</Text> : null}
    </ScreenScaffold>
  );
}
