import { Alert, Pressable, StyleSheet, Text } from "react-native";
import { registerPushDevice } from "../api/push";
import { Panel } from "../components/Panel";
import { Screen } from "../components/Screen";
import { signOut, useAuth } from "../session/auth";
import { colors } from "../theme/colors";

export function SettingsScreen() {
  const { setAuthState } = useAuth();

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
        <Pressable style={styles.button} onPress={enablePush}>
          <Text style={styles.buttonText}>Enable push notifications</Text>
        </Pressable>
        <Text style={styles.muted}>Camera, microphone, media compression, and LiveKit call controls are Phase 2/3 QA-gated.</Text>
      </Panel>
      <Panel>
        <Text style={styles.title}>Session</Text>
        <Pressable style={styles.secondaryButton} onPress={logout}>
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
