import { useState } from "react";
import { Alert, Pressable, StyleSheet, Text, TextInput, View } from "react-native";
import { askPulseAi } from "../api/pulse";
import { PulseCommandHeader, PulseCommandPanel } from "../components/PulseCommand";
import { LogiNexusScrollContainer } from "../components/Screen";
import { colors } from "../theme/colors";
import { logiNexus } from "../theme/logiNexus";

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
      Alert.alert("UNDX unavailable", error instanceof Error ? error.message : "Unable to reach UNDX.");
    } finally {
      setLoading(false);
    }
  }

  return (
    <LogiNexusScrollContainer>
      <PulseCommandHeader
        title="UNDX"
        subtitle="Digital Intelligence Companion. Powered by LogiNexus Intelligence."
        status={loading ? "Thinking" : "Ready"}
        tone="intelligence"
      />
      <PulseCommandPanel tone="intelligence">
        <Text style={styles.reply}>{reply || "Ask UNDX for a mission summary, content idea, safety explanation, or account action."}</Text>
      </PulseCommandPanel>
      <View style={styles.composer}>
        <TextInput accessibilityLabel="Ask UNDX" placeholder="Ask UNDX" placeholderTextColor={colors.muted} style={styles.input} value={draft} onChangeText={setDraft} />
        <Pressable style={styles.button} onPress={submit} disabled={loading}>
          <Text style={styles.buttonText}>{loading ? "..." : "Ask"}</Text>
        </Pressable>
      </View>
    </LogiNexusScrollContainer>
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
    gap: logiNexus.spacing.sm
  },
  input: {
    backgroundColor: colors.glass,
    borderColor: colors.border,
    borderRadius: logiNexus.radius.large,
    borderWidth: StyleSheet.hairlineWidth,
    color: colors.text,
    flex: 1,
    minHeight: 48,
    paddingHorizontal: 12
  },
  button: {
    alignItems: "center",
    backgroundColor: colors.accent,
    borderRadius: logiNexus.radius.large,
    justifyContent: "center",
    paddingHorizontal: 18
  },
  buttonText: {
    color: "#08110f",
    fontWeight: "900"
  }
});
