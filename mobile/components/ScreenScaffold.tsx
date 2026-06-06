import React from "react";
import { ScrollView, Text, View } from "react-native";
import { screenStyles } from "./theme";

type ScreenScaffoldProps = {
  title: string;
  subtitle?: string;
  children?: React.ReactNode;
};

export function ScreenScaffold({ title, subtitle, children }: ScreenScaffoldProps) {
  return (
    <ScrollView style={screenStyles.screen} contentInsetAdjustmentBehavior="automatic">
      <Text style={screenStyles.title}>{title}</Text>
      {subtitle ? <Text style={screenStyles.subtitle}>{subtitle}</Text> : null}
      <View>{children}</View>
    </ScrollView>
  );
}
