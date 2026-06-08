import React from "react";
import { Linking, Text, TouchableOpacity, View } from "react-native";
import { ScreenScaffold } from "../../components/ScreenScaffold";
import { screenStyles } from "../../components/theme";

export function UNDXScreen() {
  return (
    <ScreenScaffold title="UNDX" subtitle="Open the live Premium intelligence experience from PulseSoc production.">
      <View style={screenStyles.card}>
        <Text style={screenStyles.cardTitle}>Premium Intelligence</Text>
        <Text style={screenStyles.muted}>UNDX is connected to the live PulseSoc Premium intelligence route. Use this entry point for the production web surface while native UNDX tools continue to mature.</Text>
        <TouchableOpacity style={screenStyles.button} onPress={() => Linking.openURL("https://pulsesoc.com/pulse/premium/undx")}>
          <Text style={screenStyles.buttonText}>Open UNDX</Text>
        </TouchableOpacity>
      </View>
    </ScreenScaffold>
  );
}
