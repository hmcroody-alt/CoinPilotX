import { useEffect } from "react";
import { ActivityIndicator, StyleSheet, View } from "react-native";
import { useNavigation } from "@react-navigation/native";
import { BottomTabScreenProps } from "@react-navigation/bottom-tabs";
import { NativeStackNavigationProp } from "@react-navigation/native-stack";
import { undxChatTarget } from "../undx/undxChatTarget";
import { AppTabParamList, RootStackParamList } from "../navigation/types";
import { colors } from "../theme/colors";
import { createThemedStyles } from "../theme/themedStyles";

export function PulseAiScreen({ route }: BottomTabScreenProps<AppTabParamList, "PulseAI">) {
  const navigation = useNavigation<NativeStackNavigationProp<RootStackParamList>>();

  useEffect(() => {
    const parentNavigation = navigation.getParent<NativeStackNavigationProp<RootStackParamList>>();
    // No return target: this is the tab entry, not a drill-in from anywhere,
    // so Back falls through to the chat screen's own fallback rather than to
    // an asset the member never opened.
    const target = undxChatTarget({ taskId: route.params?.taskId });
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
