import { NativeStackNavigationProp } from "@react-navigation/native-stack";
import { useNavigation } from "@react-navigation/native";
import { useEffect, useState } from "react";
import { Pressable, StyleSheet, Text } from "react-native";
import { Conversation, getConversations } from "../api/pulse";
import { Panel } from "../components/Panel";
import { Screen } from "../components/Screen";
import { RootStackParamList } from "../navigation/types";
import { colors } from "../theme/colors";

export function MessengerScreen() {
  const navigation = useNavigation<NativeStackNavigationProp<RootStackParamList>>();
  const [conversations, setConversations] = useState<Conversation[]>([]);

  useEffect(() => {
    getConversations()
      .then((data) => setConversations(data.conversations || data.items || []))
      .catch(() => setConversations([]));
  }, []);

  return (
    <Screen title="Messenger" subtitle="Native conversation list backed by PulseSoc message APIs.">
      {conversations.length === 0 ? (
        <Panel>
          <Text style={styles.muted}>No conversations loaded yet.</Text>
        </Panel>
      ) : (
        conversations.map((conversation) => (
          <Pressable
            key={conversation.id}
            onPress={() => navigation.navigate("Chat", { conversationId: conversation.id, title: conversation.title || conversation.name })}
          >
            <Panel>
              <Text style={styles.title}>{conversation.title || conversation.name || `Conversation ${conversation.id}`}</Text>
              <Text style={styles.muted}>{conversation.latest_message || conversation.updated_at || "Open chat"}</Text>
            </Panel>
          </Pressable>
        ))
      )}
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
  }
});
