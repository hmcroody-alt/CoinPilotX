import React from "react";
import { Text, TouchableOpacity, View } from "react-native";
import { NativeStackScreenProps } from "@react-navigation/native-stack";
import { ApiPreview } from "../../components/ApiPreview";
import { ScreenScaffold } from "../../components/ScreenScaffold";
import { screenStyles } from "../../components/theme";
import { ProfileStackParamList } from "../../navigation/types";
import { useAuthStore } from "../../store/authStore";

type Props = NativeStackScreenProps<ProfileStackParamList, "Profile">;

export function ProfileScreen({ navigation }: Props) {
  const user = useAuthStore(state => state.user);
  return (
    <ScreenScaffold title="Profile" subtitle={user?.username ? `@${user.username}` : "Backed by /api/pulse/profile/me."}>
      <View style={screenStyles.card}>
        <Text style={screenStyles.cardTitle}>{user?.full_name || "PulseSoc profile"}</Text>
        <Text style={screenStyles.muted}>{user?.email || "Authenticated mobile session"}</Text>
      </View>
      <TouchableOpacity style={screenStyles.secondaryButton} onPress={() => navigation.navigate("Settings")}>
        <Text style={screenStyles.secondaryButtonText}>Settings</Text>
      </TouchableOpacity>
      <TouchableOpacity style={screenStyles.secondaryButton} onPress={() => navigation.navigate("Premium")}>
        <Text style={screenStyles.secondaryButtonText}>Premium</Text>
      </TouchableOpacity>
      <TouchableOpacity style={screenStyles.secondaryButton} onPress={() => navigation.navigate("UNDX")}>
        <Text style={screenStyles.secondaryButtonText}>UNDX</Text>
      </TouchableOpacity>
      <ApiPreview endpoint="/api/pulse/profile/me" listKeys={["items"]} emptyLabel="Profile API returns object data for detail rendering." />
    </ScreenScaffold>
  );
}
