import { NativeStackScreenProps } from "@react-navigation/native-stack";
import { useEffect, useState } from "react";
import { Alert, Pressable, StyleSheet, Text, TextInput, View } from "react-native";
import { getConversation, Message, sendMessage } from "../api/pulse";
import { Panel } from "../components/Panel";
import { Screen } from "../components/Screen";
import { RootStackParamList } from "../navigation/types";
import { colors } from "../theme/colors";

export function ChatScreen({ route }: NativeStackScreenProps<RootStackParamList, "Chat">) {
  const [messages, setMessages] = useState<Message[]>([]);
  const [draft, setDraft] = useState("");

  async function load() {
    const data = await getConversation(route.params.conversationId);
    setMessages(data.messages || []);
  }

  async function submit() {
    const body = draft.trim();
    if (!body) return;
    setDraft("");
    try {
      await sendMessage(route.params.conversationId, body);
      await load();
    } catch (error) {
      Alert.alert("Message not sent", error instanceof Error ? error.message : "Unable to send message.");
    }
  }

  useEffect(() => {
    load().catch(() => undefined);
  }, [route.params.conversationId]);

  return (
    <Screen title={route.params.title || "Chat"}>
      {messages.map((message) => (
        <Panel key={message.id}>
          <Text style={styles.message}>{message.body || message.text || ""}</Text>
          <Text style={styles.meta}>{message.created_at || ""}</Text>
        </Panel>
      ))}
      <View style={styles.composer}>
        <TextInput placeholder="Message" placeholderTextColor={colors.muted} style={styles.input} value={draft} onChangeText={setDraft} />
        <Pressable style={styles.button} onPress={submit}>
          <Text style={styles.buttonText}>Send</Text>
        </Pressable>
      </View>
    </Screen>
  );
}

const styles = StyleSheet.create({
  message: {
    color: colors.text,
    fontSize: 16
  },
  meta: {
    color: colors.muted,
    fontSize: 12
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
