import React, { useEffect, useState } from "react";
import { ActivityIndicator, Linking, Text, TouchableOpacity, View } from "react-native";
import { ScreenScaffold } from "../../components/ScreenScaffold";
import { colors, screenStyles } from "../../components/theme";
import { useAuthStore } from "../../store/authStore";
import { pulseApi } from "../../services/apiClient";

type SecurityStatus = {
  ok?: boolean;
  security_score?: number;
  trust_level?: string;
  email_verified?: boolean;
  phone_verified?: boolean;
  two_factor_enabled?: boolean;
  recovery_email?: string;
  recovery_phone?: string;
};

export function SettingsScreen() {
  const logout = useAuthStore(state => state.logout);
  const [security, setSecurity] = useState<SecurityStatus | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  useEffect(() => {
    pulseApi<SecurityStatus>("/api/account/security")
      .then(setSecurity)
      .catch(value => setError(value instanceof Error ? value.message : "Security settings could not load."))
      .finally(() => setLoading(false));
  }, []);

  return (
    <ScreenScaffold title="Settings" subtitle="Account security, recovery, trusted devices, and privacy controls.">
      {loading ? <ActivityIndicator color={colors.accent} /> : null}
      {error ? <Text style={screenStyles.error}>{error}</Text> : null}
      <View style={screenStyles.card}>
        <Text style={screenStyles.cardTitle}>Security Score</Text>
        <Text style={screenStyles.title}>{Number(security?.security_score || 0)}</Text>
        <Text style={screenStyles.muted}>Trust: {security?.trust_level || "Basic User"}</Text>
      </View>
      <View style={screenStyles.card}>
        <Text style={screenStyles.cardTitle}>Protection</Text>
        <Text style={screenStyles.muted}>Email: {security?.email_verified ? "verified" : "not verified"}</Text>
        <Text style={screenStyles.muted}>Phone: {security?.phone_verified ? "verified" : "not verified"}</Text>
        <Text style={screenStyles.muted}>2FA: {security?.two_factor_enabled ? "enabled" : "not enabled"}</Text>
        <Text style={screenStyles.muted}>Recovery email: {security?.recovery_email ? "added" : "missing"}</Text>
        <Text style={screenStyles.muted}>Recovery phone: {security?.recovery_phone ? "added" : "missing"}</Text>
        <TouchableOpacity style={screenStyles.secondaryButton} onPress={() => Linking.openURL("https://pulsesoc.com/pulse/settings/security")}>
          <Text style={screenStyles.secondaryButtonText}>Open Security Center</Text>
        </TouchableOpacity>
      </View>
      <TouchableOpacity style={screenStyles.button} onPress={logout}>
        <Text style={screenStyles.buttonText}>Log out</Text>
      </TouchableOpacity>
    </ScreenScaffold>
  );
}
