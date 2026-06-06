import React from "react";
import { createBottomTabNavigator } from "@react-navigation/bottom-tabs";
import { MainTabParamList } from "./types";
import { HomeFeedScreen } from "../screens/main/HomeFeedScreen";
import { MarketplaceScreen } from "../screens/main/MarketplaceScreen";
import { MessagesScreen } from "../screens/main/MessagesScreen";
import { NotificationsScreen } from "../screens/main/NotificationsScreen";
import { ReelsScreen } from "../screens/main/ReelsScreen";
import { ProfileNavigator } from "./ProfileNavigator";
import { colors } from "../components/theme";

const Tab = createBottomTabNavigator<MainTabParamList>();

export function MainTabs() {
  return (
    <Tab.Navigator
      screenOptions={{
        headerShown: false,
        tabBarStyle: { backgroundColor: colors.surface, borderTopColor: colors.border },
        tabBarActiveTintColor: colors.accent,
        tabBarInactiveTintColor: colors.muted
      }}
    >
      <Tab.Screen name="HomeFeed" component={HomeFeedScreen} options={{ title: "Feed" }} />
      <Tab.Screen name="Reels" component={ReelsScreen} />
      <Tab.Screen name="Messages" component={MessagesScreen} />
      <Tab.Screen name="Notifications" component={NotificationsScreen} />
      <Tab.Screen name="Marketplace" component={MarketplaceScreen} />
      <Tab.Screen name="ProfileStack" component={ProfileNavigator} options={{ title: "Profile" }} />
    </Tab.Navigator>
  );
}
