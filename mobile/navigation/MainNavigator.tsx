import React from "react";
import { createNativeStackNavigator } from "@react-navigation/native-stack";
import { colors } from "../components/theme";
import { HomeFeedScreen } from "../screens/main/HomeFeedScreen";
import { MainStackParamList } from "./types";

const Stack = createNativeStackNavigator<MainStackParamList>();

export function MainNavigator() {
  return (
    <Stack.Navigator
      screenOptions={{
        headerShown: false,
        contentStyle: { backgroundColor: colors.background }
      }}
    >
      <Stack.Screen name="HomeFeed" component={HomeFeedScreen} />
    </Stack.Navigator>
  );
}
