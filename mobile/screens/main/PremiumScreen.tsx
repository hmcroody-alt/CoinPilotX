import React from "react";
import { Text, View } from "react-native";
import { ScreenScaffold } from "../../components/ScreenScaffold";
import { screenStyles } from "../../components/theme";

export function PremiumScreen() {
  return (
    <ScreenScaffold title="Premium" subtitle="Foundation for checkout, identity effects, and profile theme APIs.">
      <View style={screenStyles.card}>
        <Text style={screenStyles.cardTitle}>Premium endpoints</Text>
        <Text style={screenStyles.muted}>/api/premium/checkout, /api/pulse/premium/activate, /api/pulse/premium/identity-effects, /api/pulse/premium/profile-theme.</Text>
      </View>
    </ScreenScaffold>
  );
}
