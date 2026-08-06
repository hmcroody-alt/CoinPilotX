import { useEffect } from "react";
import { ActivityIndicator, StyleSheet, View } from "react-native";
import { useNavigation } from "@react-navigation/native";
import { BottomTabScreenProps } from "@react-navigation/bottom-tabs";
import { NativeStackNavigationProp } from "@react-navigation/native-stack";
import { PULSE_AI_CONVERSATION_ID, PULSE_AI_DISPLAY_NAME } from "../api/messenger";
import { AppTabParamList, RootStackParamList } from "../navigation/types";
import { colors } from "../theme/colors";
import { createThemedStyles } from "../theme/themedStyles";

export function PulseAiScreen({ route }: BottomTabScreenProps<AppTabParamList, "PulseAI">) {
  const navigation = useNavigation<NativeStackNavigationProp<RootStackParamList>>();

  useEffect(() => {
    const parentNavigation = navigation.getParent<NativeStackNavigationProp<RootStackParamList>>();
    const target = {
      conversationId: PULSE_AI_CONVERSATION_ID,
      title: PULSE_AI_DISPLAY_NAME,
      presence: "available",
      ...(route.params?.taskId ? { undxTaskId: route.params.taskId } : {})
    };
    if (parentNavigation) parentNavigation.replace("Chat", target);
    else navigation.replace("Chat", target);
  }, [navigation, route.params?.taskId]);

  return (
    <View style={styles.redirect}>
      <ActivityIndicator color={colors.accent} />
    </View>
  );
}

const styles = createThemedStyles(() => ({
  redirect: {
    alignItems: "center",
    backgroundColor: "transparent",
    flex: 1,
    justifyContent: "center"
  }
}));
