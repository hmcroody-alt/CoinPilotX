import React from "react";
import { createNativeStackNavigator } from "@react-navigation/native-stack";
import { colors } from "../components/theme";
import { CreatePulseScreen } from "../screens/main/feed/CreatePulseScreen";
import { PostDetailScreen } from "../screens/main/feed/PostDetailScreen";
import { ProfileDetailScreen } from "../screens/main/feed/ProfileDetailScreen";
import { MainStackParamList } from "./types";
import { MainTabs } from "./MainTabs";

const Stack = createNativeStackNavigator<MainStackParamList>();

export function MainNavigator() {
  return (
    <Stack.Navigator
      screenOptions={{
        headerShown: false,
        contentStyle: { backgroundColor: colors.background }
      }}
    >
      <Stack.Screen name="MainTabs" component={MainTabs} />
      <Stack.Screen name="CreatePulse" component={CreatePulseScreen} />
      <Stack.Screen name="PostDetail" component={PostDetailScreen} />
      <Stack.Screen name="ProfileDetail" component={ProfileDetailScreen} />
    </Stack.Navigator>
  );
}
