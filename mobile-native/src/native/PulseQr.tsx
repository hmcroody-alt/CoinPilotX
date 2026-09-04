/**
 * Shared PulseSoc QR surface (Phase 4) — the one QR rendering owner.
 *
 * Always renders a white surface for scanner contrast and only ever encodes
 * canonical PulseSoc links (callers should build values via `qrLink`).
 */
import { StyleSheet, View } from "react-native";
import QRCode from "react-native-qrcode-svg";

type Props = {
  /** Canonical PulseSoc link — build with `qrLink` from src/native. */
  value: string;
  size?: number;
  testID?: string;
};

export function PulseQr({ value, size = 220, testID = "pulse-qr" }: Props) {
  return (
    <View testID={testID} style={styles.surface}>
      <QRCode value={value} size={size} backgroundColor="#FFFFFF" color="#06101B" />
    </View>
  );
}

const styles = StyleSheet.create({
  surface: { backgroundColor: "#FFFFFF", borderRadius: 16, padding: 14 }
});
