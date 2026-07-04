import { useEffect, useState } from "react";
import { StyleSheet, Text } from "react-native";
import { getMissionControl } from "../api/pulse";
import { Panel } from "../components/Panel";
import { Screen } from "../components/Screen";
import { colors } from "../theme/colors";

export function HomeScreen() {
  const [data, setData] = useState<Record<string, unknown> | null>(null);
  const [refreshing, setRefreshing] = useState(false);

  async function load() {
    setRefreshing(true);
    try {
      setData(await getMissionControl());
    } finally {
      setRefreshing(false);
    }
  }

  useEffect(() => {
    load().catch(() => undefined);
  }, []);

  return (
    <Screen title="Mission Control" subtitle="Native shell connected to the existing PulseSoc backend.">
      <Panel>
        <Text style={styles.label}>Backend</Text>
        <Text style={styles.value}>{data ? "Connected" : refreshing ? "Checking" : "Awaiting session data"}</Text>
      </Panel>
      <Panel>
        <Text style={styles.label}>Phase status</Text>
        <Text style={styles.value}>Phase 1 active: auth, push, Mission Control, Messenger, Pulse AI, Profile, Settings.</Text>
      </Panel>
      <Panel>
        <Text style={styles.label}>Native roadmap</Text>
        <Text style={styles.value}>Reels, Status, media capture, and LiveKit calls stay behind QA gates before release.</Text>
      </Panel>
    </Screen>
  );
}

const styles = StyleSheet.create({
  label: {
    color: colors.muted,
    fontSize: 12,
    fontWeight: "800",
    textTransform: "uppercase"
  },
  value: {
    color: colors.text,
    fontSize: 16,
    lineHeight: 23
  }
});
