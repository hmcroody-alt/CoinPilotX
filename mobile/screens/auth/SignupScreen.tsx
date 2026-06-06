import React, { useState } from "react";
import { Text, TextInput, TouchableOpacity } from "react-native";
import { NativeStackScreenProps } from "@react-navigation/native-stack";
import { ScreenScaffold } from "../../components/ScreenScaffold";
import { screenStyles } from "../../components/theme";
import { AuthStackParamList } from "../../navigation/types";
import { useAuthStore } from "../../store/authStore";

type Props = NativeStackScreenProps<AuthStackParamList, "Signup">;

export function SignupScreen({ navigation }: Props) {
  const signup = useAuthStore(state => state.signup);
  const [fullName, setFullName] = useState("");
  const [username, setUsername] = useState("");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [message, setMessage] = useState("");

  async function submit() {
    setMessage("Creating your Pulse account...");
    try {
      await signup({ full_name: fullName, username, email, password });
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "Signup failed.");
    }
  }

  return (
    <ScreenScaffold title="Signup" subtitle="Create a Pulse account through the existing CoinPilotX auth API.">
      <TextInput style={screenStyles.input} value={fullName} onChangeText={setFullName} placeholder="Full name" placeholderTextColor="#7890a8" />
      <TextInput style={screenStyles.input} value={username} onChangeText={setUsername} autoCapitalize="none" placeholder="Username" placeholderTextColor="#7890a8" />
      <TextInput style={screenStyles.input} value={email} onChangeText={setEmail} autoCapitalize="none" keyboardType="email-address" placeholder="Email" placeholderTextColor="#7890a8" />
      <TextInput style={screenStyles.input} value={password} onChangeText={setPassword} secureTextEntry placeholder="Password" placeholderTextColor="#7890a8" />
      <TouchableOpacity style={screenStyles.button} onPress={submit}>
        <Text style={screenStyles.buttonText}>Create account</Text>
      </TouchableOpacity>
      <TouchableOpacity style={screenStyles.secondaryButton} onPress={() => navigation.goBack()}>
        <Text style={screenStyles.secondaryButtonText}>Back to login</Text>
      </TouchableOpacity>
      {message ? <Text style={[screenStyles.muted, { marginTop: 12 }]}>{message}</Text> : null}
    </ScreenScaffold>
  );
}
