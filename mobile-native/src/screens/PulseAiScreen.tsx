import { useState } from "react";
import { Alert, Pressable, StyleSheet, Text, TextInput, View } from "react-native";
import { askPulseAi } from "../api/pulse";
import { Panel } from "../components/Panel";
import { Screen } from "../components/Screen";
import { colors } from "../theme/colors";

export function PulseAiScreen() {
  const [draft, setDraft] = useState("");
  const [reply, setReply] = useState("");
  const [loading, setLoading] = useState(false);

  async function submit() {
    const message = draft.trim();
    if (!message) return;
    setLoading(true);
    try {
      const data = await askPulseAi(message);
      setReply(data.response || data.reply || data.message || "");
    } catch (error) {
      Alert.alert("Pulse AI unavailable", error instanceof Error ? error.message : "Unable to reach Pulse AI.");
    } finally {
      setLoading(false);
    }
  }

  return (
    <Screen title="Pulse AI" subtitle="Native chat surface backed by the existing Pulse AI endpoint.">
      <Panel>
        <Text style={styles.reply}>{reply || "Ask Pulse AI for a mission summary, content idea, or account action."}</Text>
      </Panel>
      <View style={styles.composer}>
        <TextInput placeholder="Ask Pulse AI" placeholderTextColor={colors.muted} style={styles.input} value={draft} onChangeText={setDraft} />
        <Pressable style={styles.button} onPress={submit} disabled={loading}>
          <Text style={styles.buttonText}>{loading ? "..." : "Ask"}</Text>
        </Pressable>
      </View>
    </Screen>
  );
}

const styles = StyleSheet.create({
  reply: {
    color: colors.text,
    fontSize: 16,
    lineHeight: 23
  },
  composer: {
    flexDirection: "row",
    gap: 8
  },
  input: {
    backgroundColor: colors.surface,
    borderColor: colors.border,
    borderRadius: 8,
    borderWidth: StyleSheet.hairlineWidth,
    color: colors.text,
    flex: 1,
    minHeight: 48,
    paddingHorizontal: 12
  },
  button: {
    alignItems: "center",
    backgroundColor: colors.accent,
    borderRadius: 8,
    justifyContent: "center",
    paddingHorizontal: 18
  },
  buttonText: {
    color: "#08110f",
    fontWeight: "900"
  }
});
