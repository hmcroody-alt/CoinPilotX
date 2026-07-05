import { Alert, Pressable, StyleSheet, Text } from "react-native";
import { useNavigation } from "@react-navigation/native";
import { NativeStackNavigationProp } from "@react-navigation/native-stack";
import { registerPushDevice } from "../api/push";
import { Panel } from "../components/Panel";
import { Screen } from "../components/Screen";
import { RootStackParamList } from "../navigation/types";
import { signOut, useAuth } from "../session/auth";
import { colors } from "../theme/colors";

export function SettingsScreen() {
  const { setAuthState } = useAuth();
  const navigation = useNavigation<NativeStackNavigationProp<RootStackParamList>>();

  async function enablePush() {
    const result = await registerPushDevice();
    Alert.alert(result.ok === false ? "Push not enabled" : "Push ready", String(result.message || "Device registration sent."));
  }

  async function logout() {
    setAuthState(await signOut());
  }

  return (
    <Screen title="Settings" subtitle="Native permissions and session controls for the parallel app.">
      <Panel>
        <Text style={styles.title}>Native permissions</Text>
        <Pressable accessibilityRole="button" style={styles.button} onPress={enablePush}>
          <Text style={styles.buttonText}>Enable push notifications</Text>
        </Pressable>
        <Pressable accessibilityRole="button" style={styles.secondaryButton} onPress={() => navigation.navigate("NotificationPreferences")}>
          <Text style={styles.secondaryText}>Notification preferences</Text>
        </Pressable>
        <Pressable accessibilityRole="button" style={styles.secondaryButton} onPress={() => navigation.navigate("AccountCenter", { section: "account", title: "Account Center" })}>
          <Text style={styles.secondaryText}>Account Center</Text>
        </Pressable>
        <Pressable accessibilityRole="button" style={styles.secondaryButton} onPress={() => navigation.navigate("AccountCenter", { section: "security", title: "Security Center" })}>
          <Text style={styles.secondaryText}>Security Center</Text>
        </Pressable>
        <Pressable accessibilityRole="button" style={styles.secondaryButton} onPress={() => navigation.navigate("AccountCenter", { section: "privacy", title: "Privacy Center" })}>
          <Text style={styles.secondaryText}>Privacy Center</Text>
        </Pressable>
        <Pressable accessibilityRole="button" style={styles.secondaryButton} onPress={() => navigation.navigate("AccountCenter", { section: "devices", title: "Sessions and Devices" })}>
          <Text style={styles.secondaryText}>Sessions and devices</Text>
        </Pressable>
        <Pressable accessibilityRole="button" style={styles.secondaryButton} onPress={() => navigation.navigate("Premium")}>
          <Text style={styles.secondaryText}>Premium and entitlements</Text>
        </Pressable>
        <Pressable accessibilityRole="button" style={styles.secondaryButton} onPress={() => navigation.navigate("CreatorStudio")}>
          <Text style={styles.secondaryText}>Creator Studio</Text>
        </Pressable>
        <Pressable accessibilityRole="button" style={styles.secondaryButton} onPress={() => navigation.navigate("GrowthCenter")}>
          <Text style={styles.secondaryText}>Growth Center</Text>
        </Pressable>
        <Pressable accessibilityRole="button" style={styles.secondaryButton} onPress={() => navigation.navigate("IntelligenceCenter")}>
          <Text style={styles.secondaryText}>Intelligence and alerts</Text>
        </Pressable>
        <Pressable accessibilityRole="button" style={styles.secondaryButton} onPress={() => navigation.navigate("AlertManagement", { title: "Alerts" })}>
          <Text style={styles.secondaryText}>Alert Management</Text>
        </Pressable>
        <Text style={styles.muted}>Camera, microphone, media compression, and LiveKit call controls are Phase 2/3 QA-gated.</Text>
      </Panel>
      <Panel>
        <Text style={styles.title}>Session</Text>
        <Pressable accessibilityRole="button" style={styles.secondaryButton} onPress={logout}>
          <Text style={styles.secondaryText}>Sign out</Text>
        </Pressable>
      </Panel>
    </Screen>
  );
}

const styles = StyleSheet.create({
  title: {
    color: colors.text,
    fontSize: 17,
    fontWeight: "800"
  },
  muted: {
    color: colors.muted,
    fontSize: 14,
    lineHeight: 20
  },
  button: {
    alignItems: "center",
    backgroundColor: colors.accent,
    borderRadius: 8,
    minHeight: 46,
    justifyContent: "center"
  },
  buttonText: {
    color: "#08110f",
    fontWeight: "900"
  },
  secondaryButton: {
    alignItems: "center",
    borderColor: colors.border,
    borderRadius: 8,
    borderWidth: StyleSheet.hairlineWidth,
    minHeight: 46,
    justifyContent: "center"
  },
  secondaryText: {
    color: colors.text,
    fontWeight: "800"
  }
});
