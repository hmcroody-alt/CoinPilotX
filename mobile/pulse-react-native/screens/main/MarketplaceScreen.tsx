import React from "react";
import { Text, View } from "react-native";
import { ScreenScaffold } from "../../components/ScreenScaffold";
import { screenStyles } from "../../components/theme";

export function MarketplaceScreen() {
  return (
    <ScreenScaffold title="Marketplace" subtitle="Foundation for seller, listing, media upload, checkout, and save/report APIs.">
      <View style={screenStyles.card}>
        <Text style={screenStyles.cardTitle}>Integrated endpoint family</Text>
        <Text style={screenStyles.muted}>/api/pulse/marketplace/listings/create, /api/pulse/marketplace/media/upload, /api/pulse/payments/checkout.</Text>
      </View>
    </ScreenScaffold>
  );
}
