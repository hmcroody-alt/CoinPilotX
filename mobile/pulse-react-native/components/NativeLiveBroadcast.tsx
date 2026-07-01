import React from "react";
import { StyleSheet, Text, TouchableOpacity, View } from "react-native";
import { colors } from "./theme";

type WebApiRequest = (path: string, options?: { method?: string; body?: unknown }) => Promise<Record<string, unknown>>;

type NativeLiveBroadcastProps = {
  onClose: () => void;
  onOpenWebPath: (path: string) => void;
  apiRequest: WebApiRequest;
};

export function NativeLiveBroadcast({ onClose, onOpenWebPath }: NativeLiveBroadcastProps) {
  const openStudio = () => {
    onOpenWebPath("/pulse/live/studio?context_type=native&title=PulseSoc%20Live&category=PulseSoc%20Mobile");
  };

  return (
    <View style={styles.overlay}>
      <View style={styles.panel}>
        <Text style={styles.kicker}>PulseSoc Live</Text>
        <Text style={styles.title}>Open Live Studio</Text>
        <Text style={styles.copy}>
          Live Studio is the only broadcast cockpit. Camera, microphone, co-host controls, health, chat, and analytics
          all start from Studio.
        </Text>
        <TouchableOpacity style={styles.primaryButton} onPress={openStudio}>
          <Text style={styles.primaryText}>Continue to Studio</Text>
        </TouchableOpacity>
        <TouchableOpacity style={styles.secondaryButton} onPress={onClose}>
          <Text style={styles.secondaryText}>Cancel</Text>
        </TouchableOpacity>
      </View>
    </View>
  );
}

const styles = StyleSheet.create({
  overlay: {
    ...StyleSheet.absoluteFillObject,
    zIndex: 100,
    justifyContent: "center",
    padding: 20,
    backgroundColor: "rgba(2, 7, 14, 0.92)"
  },
  panel: {
    borderRadius: 24,
    borderWidth: 1,
    borderColor: "rgba(110, 223, 246, 0.28)",
    padding: 22,
    backgroundColor: colors.surface
  },
  kicker: {
    color: colors.accent,
    fontSize: 12,
    fontWeight: "800",
    letterSpacing: 0,
    textTransform: "uppercase"
  },
  title: {
    marginTop: 8,
    color: colors.text,
    fontSize: 28,
    fontWeight: "900"
  },
  copy: {
    marginTop: 10,
    color: colors.muted,
    fontSize: 15,
    lineHeight: 22
  },
  primaryButton: {
    minHeight: 54,
    marginTop: 18,
    borderRadius: 18,
    alignItems: "center",
    justifyContent: "center",
    backgroundColor: colors.accent
  },
  primaryText: {
    color: "#06111f",
    fontWeight: "900"
  },
  secondaryButton: {
    minHeight: 50,
    marginTop: 10,
    borderRadius: 18,
    alignItems: "center",
    justifyContent: "center",
    borderWidth: 1,
    borderColor: "rgba(255,255,255,0.16)"
  },
  secondaryText: {
    color: colors.text,
    fontWeight: "800"
  }
});
