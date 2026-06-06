import React from "react";
import { Text, TouchableOpacity, View } from "react-native";
import { ScreenScaffold } from "../../components/ScreenScaffold";
import { screenStyles } from "../../components/theme";
import { useAuthStore } from "../../store/authStore";

export function SettingsScreen() {
  const logout = useAuthStore(state => state.logout);
  return (
    <ScreenScaffold title="Settings" subtitle="Account, notification preferences, trusted devices, and secure logout foundation.">
      <View style={screenStyles.card}>
        <Text style={screenStyles.cardTitle}>Mapped settings APIs</Text>
        <Text style={screenStyles.muted}>/api/account/status, /api/account/security, /api/pulse/notifications/preferences.</Text>
      </View>
      <TouchableOpacity style={screenStyles.button} onPress={logout}>
        <Text style={screenStyles.buttonText}>Log out</Text>
      </TouchableOpacity>
    </ScreenScaffold>
  );
}
