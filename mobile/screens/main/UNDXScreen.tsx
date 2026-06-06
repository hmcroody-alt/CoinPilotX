import React from "react";
import { Text, View } from "react-native";
import { ScreenScaffold } from "../../components/ScreenScaffold";
import { screenStyles } from "../../components/theme";

export function UNDXScreen() {
  return (
    <ScreenScaffold title="UNDX" subtitle="Foundation for UNDX chat, council, kernel, and connector APIs.">
      <View style={screenStyles.card}>
        <Text style={screenStyles.cardTitle}>UNDX endpoints</Text>
        <Text style={screenStyles.muted}>/api/undx/chat, /api/undx/agent-council, /api/undx/kernel/scan, /api/undx/kernel/propose.</Text>
      </View>
    </ScreenScaffold>
  );
}
