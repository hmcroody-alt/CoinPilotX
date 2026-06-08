import React from "react";
import { createBottomTabNavigator } from "@react-navigation/bottom-tabs";
import { MaterialCommunityIcons } from "@expo/vector-icons";
import { MainTabParamList } from "./types";
import { HomeFeedScreen } from "../screens/main/HomeFeedScreen";
import { MessagesScreen } from "../screens/main/MessagesScreen";
import { NotificationsScreen } from "../screens/main/NotificationsScreen";
import { ReelsScreen } from "../screens/main/ReelsScreen";
import { VideosScreen } from "../screens/main/VideosScreen";
import { ProfileNavigator } from "./ProfileNavigator";
import { colors } from "../components/theme";

const Tab = createBottomTabNavigator<MainTabParamList>();

export function MainTabs() {
  return (
    <Tab.Navigator
      screenOptions={{
        headerShown: false,
        tabBarStyle: { backgroundColor: colors.surface, borderTopColor: colors.border, minHeight: 76, paddingTop: 7, paddingBottom: 9 },
        tabBarActiveTintColor: colors.accent,
        tabBarInactiveTintColor: colors.muted,
        tabBarLabelStyle: { fontSize: 11, fontWeight: "800" }
      }}
    >
      <Tab.Screen name="HomeFeed" component={HomeFeedScreen} options={{ title: "Home", tabBarIcon: props => <TabIcon name="home-variant" {...props} /> }} />
      <Tab.Screen name="Reels" component={ReelsScreen} options={{ tabBarIcon: props => <TabIcon name="play-box-multiple" {...props} /> }} />
      <Tab.Screen name="Videos" component={VideosScreen} options={{ tabBarIcon: props => <TabIcon name="video" {...props} /> }} />
      <Tab.Screen name="Messages" component={MessagesScreen} options={{ title: "Chats", tabBarIcon: props => <TabIcon name="message-text" {...props} /> }} />
      <Tab.Screen name="Notifications" component={NotificationsScreen} options={{ title: "Alerts", tabBarIcon: props => <TabIcon name="bell" {...props} /> }} />
      <Tab.Screen name="ProfileStack" component={ProfileNavigator} options={{ title: "Profile", tabBarIcon: props => <TabIcon name="account-circle" {...props} /> }} />
    </Tab.Navigator>
  );
}

function TabIcon({ name, color, size }: { name: React.ComponentProps<typeof MaterialCommunityIcons>["name"]; color: string; size: number }) {
  return <MaterialCommunityIcons name={name} size={size} color={color} />;
}
