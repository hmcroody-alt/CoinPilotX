import { NativeStackScreenProps } from "@react-navigation/native-stack";
import { useEffect, useMemo } from "react";
import { Pressable, StyleSheet, Text, View } from "react-native";
import { dashboardModuleParamsForRoute } from "../navigation/dashboardRouting";
import { RootStackParamList } from "../navigation/types";
import { colors } from "../theme/colors";
import { createThemedStyles } from "../theme/themedStyles";

type Props = NativeStackScreenProps<RootStackParamList, "DashboardLegacyModule">;

export function DashboardLegacyModuleScreen({ navigation, route }: Props) {
  const legacyPath = useMemo(() => {
    const legacyGroup = route.params?.legacyGroup;
    const legacyModule = route.params?.legacyModule;
    const legacySubmodule = route.params?.legacySubmodule;
    return `/dashboard/${[legacyGroup, legacyModule, legacySubmodule].filter(Boolean).join("/")}`;
  }, [route.params?.legacyGroup, route.params?.legacyModule, route.params?.legacySubmodule]);

  const moduleParams = useMemo(() => dashboardModuleParamsForRoute(legacyPath), [legacyPath]);

  useEffect(() => {
    if (moduleParams) {
      navigation.replace("DashboardModuleDetail", moduleParams);
    }
  }, [moduleParams, navigation]);

  if (moduleParams) {
    return (
      <View style={styles.center}>
        <Text style={styles.kicker}>Dashboard route mapped</Text>
        <Text style={styles.title}>{moduleParams.title || "Dashboard Module"}</Text>
        <Text style={styles.body}>Opening the native module shell for {legacyPath}.</Text>
      </View>
    );
  }

  return (
    <View style={styles.center}>
      <Text style={styles.kicker}>Dashboard route unavailable</Text>
      <Text style={styles.title}>Module not found</Text>
      <Text style={styles.body}>
        {legacyPath} is not represented in the native dashboard module registry yet. The dashboard foundation can still route from the main module map.
      </Text>
      <Pressable style={styles.button} onPress={() => navigation.replace("UserDashboard", { title: "Dashboard" })}>
        <Text style={styles.buttonText}>Open Dashboard</Text>
      </Pressable>
    </View>
  );
}

const styles = createThemedStyles(() => ({
  body: {
    color: colors.muted,
    fontSize: 14,
    lineHeight: 20,
    marginTop: 10,
    maxWidth: 520,
    textAlign: "center"
  },
  button: {
    backgroundColor: "rgba(37,208,167,0.14)",
    borderColor: "rgba(37,208,167,0.48)",
    borderRadius: 8,
    borderWidth: 1,
    marginTop: 20,
    paddingHorizontal: 18,
    paddingVertical: 12
  },
  buttonText: {
    color: colors.text,
    fontSize: 13,
    fontWeight: "900"
  },
  center: {
    alignItems: "center",
    backgroundColor: colors.background,
    flex: 1,
    justifyContent: "center",
    padding: 24
  },
  kicker: {
    color: colors.accent,
    fontSize: 12,
    fontWeight: "900",
    letterSpacing: 0,
    textTransform: "uppercase"
  },
  title: {
    color: colors.text,
    fontSize: 30,
    fontWeight: "900",
    marginTop: 8,
    textAlign: "center"
  }
}));
