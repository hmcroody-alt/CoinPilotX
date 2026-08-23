import { NativeStackScreenProps } from "@react-navigation/native-stack";
import { useEffect } from "react";
import { ActivityIndicator, StyleSheet, Text, View } from "react-native";
import { DashboardNavigation, openDashboardRoute } from "../navigation/dashboardRouting";
import { RootStackParamList } from "../navigation/types";
import { colors } from "../theme/colors";
import { createThemedStyles } from "../theme/themedStyles";

type Props =
  | NativeStackScreenProps<RootStackParamList, "DashboardComposeAlias">
  | NativeStackScreenProps<RootStackParamList, "DashboardMusicAlias">;

export function DashboardActionAliasScreen({ navigation, route }: Props) {
  const targetRoute = route.name === "DashboardComposeAlias" ? "/pulse/compose" : "/pulse/music";

  useEffect(() => {
    if (route.name === "DashboardMusicAlias") {
      navigation.replace("Music", { title: "PulseSoc Music" });
      return;
    }
    openDashboardRoute(navigation as unknown as DashboardNavigation, targetRoute);
  }, [navigation, route.name, targetRoute]);

  return (
    <View style={styles.center}>
      <ActivityIndicator color={colors.accent} />
      <Text style={styles.kicker}>Dashboard action mapped</Text>
      <Text style={styles.body}>Opening {targetRoute}.</Text>
    </View>
  );
}

const styles = createThemedStyles(() => ({
  body: {
    color: colors.muted,
    fontSize: 14,
    lineHeight: 20,
    marginTop: 8,
    textAlign: "center"
  },
  center: {
    alignItems: "center",
    backgroundColor: "transparent",
    flex: 1,
    justifyContent: "center",
    padding: 24
  },
  kicker: {
    color: colors.accent,
    fontSize: 12,
    fontWeight: "900",
    letterSpacing: 0,
    marginTop: 14,
    textTransform: "uppercase"
  }
}));
