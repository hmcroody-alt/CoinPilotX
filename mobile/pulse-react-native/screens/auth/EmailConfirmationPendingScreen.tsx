import React, { useEffect, useState } from "react";
import { Linking, Text, TextInput, TouchableOpacity, View } from "react-native";
import { NativeStackScreenProps } from "@react-navigation/native-stack";
import { ScreenScaffold } from "../../components/ScreenScaffold";
import { screenStyles } from "../../components/theme";
import { AuthStackParamList } from "../../navigation/types";
import { useAuthStore } from "../../store/authStore";
import { validateEmail } from "../../services/validation";

type Props = NativeStackScreenProps<AuthStackParamList, "EmailConfirmationPending">;

export function EmailConfirmationPendingScreen({ navigation, route }: Props) {
  const pendingEmail = useAuthStore(state => state.pendingConfirmationEmail);
  const initialEmail = route.params?.email || pendingEmail;
  const token = route.params?.token;
  const confirmEmail = useAuthStore(state => state.confirmEmail);
  const resendConfirmation = useAuthStore(state => state.resendConfirmation);
  const refreshConfirmationStatus = useAuthStore(state => state.refreshConfirmationStatus);
  const [email, setEmail] = useState(initialEmail || "");
  const [message, setMessage] = useState(token ? "Verifying email..." : "Check your email to confirm your account.");
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    if (!token) return;
    setLoading(true);
    confirmEmail(token)
      .then(text => setMessage(text))
      .catch(error => setMessage(error instanceof Error ? error.message : "Email confirmation failed."))
      .finally(() => setLoading(false));
  }, [confirmEmail, token]);

  async function resend() {
    const validation = validateEmail(email);
    if (!validation.valid) {
      setMessage(validation.message);
      return;
    }
    setLoading(true);
    try {
      setMessage(await resendConfirmation(email));
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "Could not resend confirmation.");
    } finally {
      setLoading(false);
    }
  }

  async function refreshStatus() {
    const validation = validateEmail(email);
    if (!validation.valid) {
      setMessage(validation.message);
      return;
    }
    setLoading(true);
    try {
      const confirmed = await refreshConfirmationStatus(email);
      if (confirmed) {
        setMessage("Email confirmed. You can log in anytime.");
      } else {
        setMessage("Still waiting for confirmation. Check your inbox and spam folder.");
      }
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "Could not refresh confirmation status.");
    } finally {
      setLoading(false);
    }
  }

  function openInbox() {
    Linking.openURL("mailto:").catch(() => setMessage("Open your email app and look for the PulseSoc confirmation email."));
  }

  return (
    <ScreenScaffold title="Confirm Email" subtitle="Check your email to confirm your account.">
      <View style={screenStyles.card}>
        <Text style={screenStyles.cardTitle}>Check your email to confirm your account.</Text>
        <Text style={screenStyles.muted}>Open the confirmation link from PulseSoc, then refresh this screen or return to login.</Text>
      </View>
      <TextInput style={screenStyles.input} value={email} onChangeText={setEmail} autoCapitalize="none" keyboardType="email-address" placeholder="Email" placeholderTextColor="#7890a8" />
      <TouchableOpacity style={screenStyles.button} onPress={resend} disabled={loading}>
        <Text style={screenStyles.buttonText}>{loading ? "Working..." : "Resend confirmation"}</Text>
      </TouchableOpacity>
      <TouchableOpacity style={screenStyles.secondaryButton} onPress={openInbox}>
        <Text style={screenStyles.secondaryButtonText}>Open email app</Text>
      </TouchableOpacity>
      <TouchableOpacity style={screenStyles.secondaryButton} onPress={refreshStatus}>
        <Text style={screenStyles.secondaryButtonText}>Refresh confirmation status</Text>
      </TouchableOpacity>
      <TouchableOpacity style={screenStyles.secondaryButton} onPress={() => navigation.navigate("Login")}>
        <Text style={screenStyles.secondaryButtonText}>Back to login</Text>
      </TouchableOpacity>
      {message ? <Text style={[screenStyles.muted, { marginTop: 12 }]}>{message}</Text> : null}
    </ScreenScaffold>
  );
}
