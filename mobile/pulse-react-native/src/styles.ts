import { StyleSheet } from "react-native";

export const styles = StyleSheet.create({
  auth: {
    flex: 1,
    justifyContent: "center",
    gap: 12,
    padding: 20,
    backgroundColor: "#050b14",
  },
  screen: {
    flex: 1,
    padding: 16,
    backgroundColor: "#050b14",
  },
  center: {
    flex: 1,
    justifyContent: "center",
    alignItems: "center",
    backgroundColor: "#050b14",
  },
  logo: {
    color: "#f2fbff",
    fontSize: 42,
    fontWeight: "900",
  },
  title: {
    color: "#f2fbff",
    fontSize: 28,
    fontWeight: "900",
    marginBottom: 14,
  },
  card: {
    padding: 14,
    borderRadius: 10,
    borderWidth: 1,
    borderColor: "rgba(110,223,246,.22)",
    backgroundColor: "#0d1627",
    marginBottom: 10,
  },
  cardTitle: {
    color: "#f2fbff",
    fontWeight: "800",
    fontSize: 16,
  },
  muted: {
    color: "#9fb5c0",
  },
  error: {
    color: "#ffbdc7",
  },
  input: {
    minHeight: 48,
    borderRadius: 10,
    borderWidth: 1,
    borderColor: "rgba(110,223,246,.22)",
    backgroundColor: "#081323",
    color: "#f2fbff",
    paddingHorizontal: 12,
  },
  segmented: {
    flexDirection: "row",
    borderWidth: 1,
    borderColor: "rgba(110,223,246,.22)",
    borderRadius: 10,
    overflow: "hidden",
  },
  segment: {
    flex: 1,
    minHeight: 42,
    alignItems: "center",
    justifyContent: "center",
    backgroundColor: "#081323",
  },
  segmentActive: {
    backgroundColor: "#12314b",
  },
  segmentText: {
    color: "#f2fbff",
    fontWeight: "800",
  },
  primaryButton: {
    minHeight: 48,
    borderRadius: 10,
    alignItems: "center",
    justifyContent: "center",
    backgroundColor: "#6edff6",
  },
  primaryButtonText: {
    color: "#06101b",
    fontWeight: "900",
  },
  secondaryButton: {
    minHeight: 44,
    borderRadius: 10,
    alignItems: "center",
    justifyContent: "center",
    backgroundColor: "#dff7ff",
  },
});
