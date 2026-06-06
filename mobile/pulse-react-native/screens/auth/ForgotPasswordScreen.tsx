import React, { useState } from "react";
import { Text, TextInput, TouchableOpacity } from "react-native";
import { NativeStackScreenProps } from "@react-navigation/native-stack";
import { ScreenScaffold } from "../../components/ScreenScaffold";
import { screenStyles } from "../../components/theme";
import { AuthStackParamList } from "../../navigation/types";
import { useAuthStore } from "../../store/authStore";
import { isOfflineError } from "../../services/apiClient";
import { validateEmail } from "../../services/validation";

type Props = NativeStackScreenProps<AuthStackParamList, "ForgotPassword">;

export function ForgotPasswordScreen({ navigation }: Props) {
  const recover = useAuthStore(state => state.recover);
  const [email, setEmail] = useState("");
  const [message, setMessage] = useState("");
  const [sent, setSent] = useState(false);
  const [loading, setLoading] = useState(false);

  async function submit() {
    const validation = validateEmail(email);
    if (!validation.valid) {
      setMessage(validation.message);
      return;
    }
    setMessage("Sending recovery instructions...");
    setLoading(true);
    try {
      await recover(email);
      setSent(true);
      setMessage("If an account exists, password recovery has been sent.");
    } catch (error) {
      setMessage(isOfflineError(error) ? "You appear to be offline. Check your connection and try again." : error instanceof Error ? error.message : "Recovery failed.");
    } finally {
      setLoading(false);
    }
  }

  return (
    <ScreenScaffold title="Forgot Password" subtitle="Use the existing CoinPilotX recovery flow.">
      {!sent ? (
        <>
          <TextInput style={screenStyles.input} value={email} onChangeText={setEmail} autoCapitalize="none" keyboardType="email-address" placeholder="Email" placeholderTextColor="#7890a8" />
          <TouchableOpacity style={[screenStyles.button, loading && { opacity: 0.7 }]} onPress={submit} disabled={loading}>
            <Text style={screenStyles.buttonText}>{loading ? "Sending..." : "Send recovery"}</Text>
          </TouchableOpacity>
        </>
      ) : null}
      <TouchableOpacity style={screenStyles.secondaryButton} onPress={() => navigation.goBack()}>
        <Text style={screenStyles.secondaryButtonText}>Back to login</Text>
      </TouchableOpacity>
      {message ? <Text style={[screenStyles.muted, { marginTop: 12 }]}>{message}</Text> : null}
    </ScreenScaffold>
  );
}
