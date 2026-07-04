import { ReactNode } from "react";
import { ScrollView, StyleSheet, Text, View } from "react-native";
import { colors } from "../theme/colors";

type Props = {
  title: string;
  subtitle?: string;
  children: ReactNode;
};

export function Screen({ title, subtitle, children }: Props) {
  return (
    <ScrollView style={styles.root} contentContainerStyle={styles.content}>
      <View style={styles.header}>
        <Text style={styles.title}>{title}</Text>
        {subtitle ? <Text style={styles.subtitle}>{subtitle}</Text> : null}
      </View>
      {children}
    </ScrollView>
  );
}

const styles = StyleSheet.create({
  root: {
    flex: 1,
    backgroundColor: colors.background
  },
  content: {
    padding: 18,
    gap: 14
  },
  header: {
    gap: 6,
    paddingBottom: 4
  },
  title: {
    color: colors.text,
    fontSize: 28,
    fontWeight: "800"
  },
  subtitle: {
    color: colors.muted,
    fontSize: 15,
    lineHeight: 21
  }
});
