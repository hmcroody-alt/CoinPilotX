import { StyleSheet } from "react-native";

export const colors = {
  background: "#06111f",
  surface: "#0d1d30",
  surfaceSoft: "#142840",
  border: "#28435f",
  text: "#f5fbff",
  muted: "#9eb2c8",
  accent: "#5ee1b7",
  accentAlt: "#66d9ff",
  danger: "#ff6b7a"
};

export const layout = StyleSheet.create({
  centeredScreen: {
    flex: 1,
    alignItems: "center",
    justifyContent: "center",
    backgroundColor: colors.background,
    padding: 20
  }
});

export const screenStyles = StyleSheet.create({
  screen: {
    flex: 1,
    backgroundColor: colors.background,
    padding: 16
  },
  title: {
    color: colors.text,
    fontSize: 28,
    fontWeight: "800",
    marginBottom: 6
  },
  subtitle: {
    color: colors.muted,
    fontSize: 15,
    lineHeight: 22,
    marginBottom: 16
  },
  card: {
    borderWidth: 1,
    borderColor: colors.border,
    borderRadius: 8,
    backgroundColor: colors.surface,
    padding: 14,
    marginBottom: 12
  },
  cardTitle: {
    color: colors.text,
    fontSize: 16,
    fontWeight: "700",
    marginBottom: 4
  },
  muted: {
    color: colors.muted,
    fontSize: 13,
    lineHeight: 19
  },
  error: {
    color: colors.danger,
    fontSize: 14,
    lineHeight: 20
  },
  input: {
    minHeight: 48,
    borderWidth: 1,
    borderColor: colors.border,
    borderRadius: 8,
    backgroundColor: colors.surface,
    color: colors.text,
    paddingHorizontal: 12,
    marginBottom: 12
  },
  button: {
    minHeight: 48,
    alignItems: "center",
    justifyContent: "center",
    borderRadius: 8,
    backgroundColor: colors.accent,
    paddingHorizontal: 14,
    marginTop: 4
  },
  buttonText: {
    color: "#06111f",
    fontSize: 15,
    fontWeight: "800"
  },
  secondaryButton: {
    minHeight: 44,
    alignItems: "center",
    justifyContent: "center",
    borderRadius: 8,
    borderWidth: 1,
    borderColor: colors.border,
    paddingHorizontal: 14,
    marginTop: 10
  },
  secondaryButtonText: {
    color: colors.text,
    fontSize: 14,
    fontWeight: "700"
  }
});
