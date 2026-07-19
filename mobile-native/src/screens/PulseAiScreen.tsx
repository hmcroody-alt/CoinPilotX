import { useEffect } from "react";
import { ActivityIndicator, StyleSheet, View } from "react-native";
import { useNavigation } from "@react-navigation/native";
import { NativeStackNavigationProp } from "@react-navigation/native-stack";
import { PULSE_AI_CONVERSATION_ID, PULSE_AI_DISPLAY_NAME } from "../api/messenger";
import { RootStackParamList } from "../navigation/types";
import { colors } from "../theme/colors";

export function PulseAiScreen() {
  const navigation = useNavigation<NativeStackNavigationProp<RootStackParamList>>();

  useEffect(() => {
    const parentNavigation = navigation.getParent<NativeStackNavigationProp<RootStackParamList>>();
    const target = {
      conversationId: PULSE_AI_CONVERSATION_ID,
      title: PULSE_AI_DISPLAY_NAME,
      presence: "available"
    };
    if (parentNavigation) parentNavigation.replace("Chat", target);
    else navigation.replace("Chat", target);
  }, [navigation]);

  return (
    <View style={styles.redirect}>
      <ActivityIndicator color={colors.accent} />
    </View>
  );
}

const styles = StyleSheet.create({
  redirect: {
    alignItems: "center",
    backgroundColor: colors.background,
    flex: 1,
    justifyContent: "center"
  }
});
