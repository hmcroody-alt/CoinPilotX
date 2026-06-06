import React, { useState } from "react";
import { Text, TextInput, TouchableOpacity, View } from "react-native";
import { NativeStackScreenProps } from "@react-navigation/native-stack";
import { ScreenScaffold } from "../../components/ScreenScaffold";
import { screenStyles } from "../../components/theme";
import { AuthStackParamList } from "../../navigation/types";
import { useAuthStore } from "../../store/authStore";
import { validatePasswordPair } from "../../services/validation";

type Props = NativeStackScreenProps<AuthStackParamList, "ResetPassword">;

export function ResetPasswordScreen({ navigation, route }: Props) {
  const resetPassword = useAuthStore(state => state.resetPassword);
  const [password, setPassword] = useState("");
  const [confirmPassword, setConfirmPassword] = useState("");
  const [message, setMessage] = useState("");
  const [success, setSuccess] = useState(false);
  const [loading, setLoading] = useState(false);
  const token = route.params?.token || "";

  async function submit() {
    const validation = validatePasswordPair(password, confirmPassword);
    if (!validation.valid) {
      setMessage(validation.message);
      return;
    }
    if (!token) {
      setMessage("This reset link is invalid.");
      return;
    }
    setLoading(true);
    setMessage("Resetting password...");
    try {
      setMessage(await resetPassword(token, password));
      setSuccess(true);
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "Password reset failed.");
    } finally {
      setLoading(false);
    }
  }

  return (
    <ScreenScaffold title="Reset Password" subtitle="Create a new Pulse password from your reset link.">
      {success ? (
        <View style={screenStyles.card}>
          <Text style={screenStyles.cardTitle}>Password reset complete</Text>
          <Text style={screenStyles.muted}>You can log in with your new password.</Text>
        </View>
      ) : (
        <>
          <TextInput style={screenStyles.input} value={password} onChangeText={setPassword} secureTextEntry placeholder="New password" placeholderTextColor="#7890a8" />
          <TextInput style={screenStyles.input} value={confirmPassword} onChangeText={setConfirmPassword} secureTextEntry placeholder="Confirm new password" placeholderTextColor="#7890a8" />
          <TouchableOpacity style={screenStyles.button} onPress={submit} disabled={loading}>
            <Text style={screenStyles.buttonText}>{loading ? "Resetting..." : "Reset password"}</Text>
          </TouchableOpacity>
        </>
      )}
      <TouchableOpacity style={screenStyles.secondaryButton} onPress={() => navigation.navigate("Login")}>
        <Text style={screenStyles.secondaryButtonText}>Back to login</Text>
      </TouchableOpacity>
      {message ? <Text style={[screenStyles.muted, { marginTop: 12 }]}>{message}</Text> : null}
    </ScreenScaffold>
  );
}
