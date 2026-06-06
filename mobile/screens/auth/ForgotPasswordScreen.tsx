import React, { useState } from "react";
import { Text, TextInput, TouchableOpacity } from "react-native";
import { NativeStackScreenProps } from "@react-navigation/native-stack";
import { ScreenScaffold } from "../../components/ScreenScaffold";
import { screenStyles } from "../../components/theme";
import { AuthStackParamList } from "../../navigation/types";
import { useAuthStore } from "../../store/authStore";

type Props = NativeStackScreenProps<AuthStackParamList, "ForgotPassword">;

export function ForgotPasswordScreen({ navigation }: Props) {
  const recover = useAuthStore(state => state.recover);
  const [email, setEmail] = useState("");
  const [message, setMessage] = useState("");

  async function submit() {
    setMessage("Sending recovery instructions...");
    try {
      await recover(email);
      setMessage("If an account exists, password recovery has been sent.");
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "Recovery failed.");
    }
  }

  return (
    <ScreenScaffold title="Forgot Password" subtitle="Use the existing CoinPilotX recovery flow.">
      <TextInput style={screenStyles.input} value={email} onChangeText={setEmail} autoCapitalize="none" keyboardType="email-address" placeholder="Email" placeholderTextColor="#7890a8" />
      <TouchableOpacity style={screenStyles.button} onPress={submit}>
        <Text style={screenStyles.buttonText}>Send recovery</Text>
      </TouchableOpacity>
      <TouchableOpacity style={screenStyles.secondaryButton} onPress={() => navigation.goBack()}>
        <Text style={screenStyles.secondaryButtonText}>Back to login</Text>
      </TouchableOpacity>
      {message ? <Text style={[screenStyles.muted, { marginTop: 12 }]}>{message}</Text> : null}
    </ScreenScaffold>
  );
}
