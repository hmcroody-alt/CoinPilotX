import React from "react";
import { createNativeStackNavigator } from "@react-navigation/native-stack";
import { colors } from "../components/theme";
import { CreatePulseScreen } from "../screens/main/feed/CreatePulseScreen";
import { HomeFeedScreen } from "../screens/main/HomeFeedScreen";
import { PostDetailScreen } from "../screens/main/feed/PostDetailScreen";
import { ProfileDetailScreen } from "../screens/main/feed/ProfileDetailScreen";
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
      <Stack.Screen name="CreatePulse" component={CreatePulseScreen} />
      <Stack.Screen name="PostDetail" component={PostDetailScreen} />
      <Stack.Screen name="ProfileDetail" component={ProfileDetailScreen} />
    </Stack.Navigator>
  );
}
