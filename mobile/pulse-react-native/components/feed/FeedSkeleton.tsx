import React from "react";
import { View } from "react-native";
import { colors, screenStyles } from "../theme";

export function FeedSkeleton() {
  return (
    <>
      {[0, 1, 2].map(item => (
        <View key={item} style={screenStyles.card}>
          <View style={{ height: 18, width: "45%", backgroundColor: colors.surfaceSoft, borderRadius: 6, marginBottom: 12 }} />
          <View style={{ height: 14, width: "88%", backgroundColor: colors.surfaceSoft, borderRadius: 6, marginBottom: 8 }} />
          <View style={{ height: 14, width: "66%", backgroundColor: colors.surfaceSoft, borderRadius: 6, marginBottom: 14 }} />
          <View style={{ height: 180, backgroundColor: colors.surfaceSoft, borderRadius: 8 }} />
        </View>
      ))}
    </>
  );
}
