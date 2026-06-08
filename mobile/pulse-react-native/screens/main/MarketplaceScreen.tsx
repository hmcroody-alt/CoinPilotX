import React from "react";
import { Linking, Text, TouchableOpacity, View } from "react-native";
import { ScreenScaffold } from "../../components/ScreenScaffold";
import { screenStyles } from "../../components/theme";

export function MarketplaceScreen() {
  return (
    <ScreenScaffold title="Marketplace" subtitle="Live PulseSoc marketplace access.">
      <View style={screenStyles.card}>
        <Text style={screenStyles.cardTitle}>Production Marketplace</Text>
        <Text style={screenStyles.muted}>Browse listings, seller tools, checkout, reporting, and saved marketplace items through the live PulseSoc route.</Text>
        <TouchableOpacity style={screenStyles.button} onPress={() => Linking.openURL("https://pulsesoc.com/pulse/marketplace")}>
          <Text style={screenStyles.buttonText}>Open Marketplace</Text>
        </TouchableOpacity>
      </View>
    </ScreenScaffold>
  );
}
