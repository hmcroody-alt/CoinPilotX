import { useEffect, useState } from "react";
import { StyleSheet, Text } from "react-native";
import { getProfile } from "../api/pulse";
import { Panel } from "../components/Panel";
import { Screen } from "../components/Screen";
import { useAuth } from "../session/auth";
import { colors } from "../theme/colors";

export function ProfileScreen() {
  const { authState } = useAuth();
  const [profile, setProfile] = useState<Record<string, unknown> | null>(null);

  useEffect(() => {
    getProfile().then(setProfile).catch(() => setProfile(null));
  }, []);

  return (
    <Screen title="Profile" subtitle="Native profile summary using existing account data.">
      <Panel>
        <Text style={styles.name}>{authState.user?.display_name || authState.user?.full_name || authState.user?.username || "PulseSoc user"}</Text>
        <Text style={styles.muted}>{authState.user?.email || "Signed in"}</Text>
      </Panel>
      <Panel>
        <Text style={styles.muted}>Profile API</Text>
        <Text style={styles.value}>{profile ? "Loaded" : "Available after backend session validation"}</Text>
      </Panel>
    </Screen>
  );
}

const styles = StyleSheet.create({
  name: {
    color: colors.text,
    fontSize: 22,
    fontWeight: "900"
  },
  muted: {
    color: colors.muted,
    fontSize: 14
  },
  value: {
    color: colors.text,
    fontSize: 16
  }
});
