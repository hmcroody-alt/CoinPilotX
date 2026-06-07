import React, { useEffect } from "react";
import { Text, View } from "react-native";
import { NativeStackScreenProps } from "@react-navigation/native-stack";
import { AuthStackParamList } from "../../navigation/types";
import { layout, screenStyles } from "../../components/theme";

type Props = NativeStackScreenProps<AuthStackParamList, "Splash">;

export function SplashScreen({ navigation }: Props) {
  useEffect(() => {
    const timer = setTimeout(() => navigation.replace("Login"), 650);
    return () => clearTimeout(timer);
  }, [navigation]);

  return (
    <View style={layout.centeredScreen}>
      <Text style={[screenStyles.title, { fontSize: 42 }]}>PulseSoc</Text>
      <Text style={screenStyles.subtitle}>CoinPilotX social, ready for mobile.</Text>
    </View>
  );
}
