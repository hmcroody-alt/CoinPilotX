/**
 * QR / barcode scan sheet (Phase 5) — the one scanner owner.
 *
 * Modal component (deliberately NOT a navigation screen). Camera permission
 * is requested only when the sheet opens — i.e. after a user-initiated
 * action. Payloads go through `classifyScannedPayload`; only PulseSoc links
 * auto-route, external URLs require explicit confirmation, rejected
 * payloads are never acted on.
 */
import { CameraView } from "expo-camera";
import { useCallback, useEffect, useRef, useState } from "react";
import { Linking, Modal, Pressable, StyleSheet, Text, View } from "react-native";
import { haptic } from "./haptics";
import { requestPermission, openSystemSettings } from "./permissions";
import { classifyScannedPayload, ScannedPayload } from "./qr";
import { PermissionState } from "./types";

type Props = {
  visible: boolean;
  onClose: () => void;
  /** Called with a validated PulseSoc path (e.g. "profile/123") to route in-app. */
  onPulseSocLink: (path: string, url: string) => void;
  /** Optional plain-text handler (e.g. paste into composer). */
  onText?: (text: string) => void;
};

export function ScanSheet({ visible, onClose, onPulseSocLink, onText }: Props) {
  const [permission, setPermission] = useState<PermissionState | "PENDING">("PENDING");
  const [result, setResult] = useState<ScannedPayload | null>(null);
  const locked = useRef(false);

  useEffect(() => {
    if (!visible) {
      setResult(null);
      locked.current = false;
      setPermission("PENDING");
      return;
    }
    let cancelled = false;
    // The sheet only opens from a user action, so requesting here is
    // permission-safe (never on app mount).
    requestPermission("CAMERA").then((snap) => {
      if (!cancelled) setPermission(snap.state);
    });
    return () => {
      cancelled = true;
    };
  }, [visible]);

  const handleScan = useCallback(
    ({ data }: { data: string }) => {
      if (locked.current) return;
      const payload = classifyScannedPayload(data);
      if (payload.kind === "rejected") return; // keep scanning; never act on rejected payloads
      locked.current = true;
      haptic("success");
      if (payload.kind === "pulsesoc_link") {
        onClose();
        onPulseSocLink(payload.path, payload.url);
        return;
      }
      setResult(payload); // external_url / text need explicit user confirmation
    },
    [onClose, onPulseSocLink]
  );

  const rescan = () => {
    locked.current = false;
    setResult(null);
  };

  return (
    <Modal visible={visible} animationType="slide" onRequestClose={onClose} transparent={false}>
      <View style={styles.root} testID="native-scan-sheet">
        {permission === "GRANTED" || permission === "LIMITED" ? (
          <CameraView
            style={styles.camera}
            facing="back"
            barcodeScannerSettings={{ barcodeTypes: ["qr", "ean13", "code128"] }}
            onBarcodeScanned={result ? undefined : handleScan}
          />
        ) : (
          <View style={styles.center}>
            {permission === "PENDING" ? (
              <Text style={styles.helper}>Waiting for camera access…</Text>
            ) : (
              <>
                <Text style={styles.title}>Camera access needed</Text>
                <Text style={styles.helper}>PulseSoc uses the camera only while this scanner is open.</Text>
                {permission === "BLOCKED" ? (
                  <Pressable accessibilityRole="button" style={styles.action} onPress={() => void openSystemSettings()}>
                    <Text style={styles.actionText}>Open Settings</Text>
                  </Pressable>
                ) : null}
              </>
            )}
          </View>
        )}

        {result?.kind === "external_url" ? (
          <View style={styles.panel}>
            <Text style={styles.title}>External link</Text>
            <Text style={styles.helper} numberOfLines={3}>{result.url}</Text>
            <Text style={styles.warning}>This is not a PulseSoc link. Open it only if you trust it.</Text>
            <Pressable
              accessibilityRole="button"
              style={styles.action}
              onPress={() => {
                Linking.openURL(result.url).catch(() => undefined);
                onClose();
              }}
            >
              <Text style={styles.actionText}>Open in browser</Text>
            </Pressable>
            <Pressable accessibilityRole="button" style={styles.secondary} onPress={rescan}>
              <Text style={styles.secondaryText}>Scan again</Text>
            </Pressable>
          </View>
        ) : null}

        {result?.kind === "text" ? (
          <View style={styles.panel}>
            <Text style={styles.title}>Scanned text</Text>
            <Text style={styles.helper} numberOfLines={4}>{result.text}</Text>
            {onText ? (
              <Pressable
                accessibilityRole="button"
                style={styles.action}
                onPress={() => {
                  onClose();
                  onText(result.text);
                }}
              >
                <Text style={styles.actionText}>Use text</Text>
              </Pressable>
            ) : null}
            <Pressable accessibilityRole="button" style={styles.secondary} onPress={rescan}>
              <Text style={styles.secondaryText}>Scan again</Text>
            </Pressable>
          </View>
        ) : null}

        <Pressable accessibilityRole="button" accessibilityLabel="Close scanner" style={styles.close} onPress={onClose}>
          <Text style={styles.closeText}>Close</Text>
        </Pressable>
      </View>
    </Modal>
  );
}

const styles = StyleSheet.create({
  root: { backgroundColor: "#06101B", flex: 1, justifyContent: "flex-end" },
  camera: { ...StyleSheet.absoluteFillObject },
  center: { alignItems: "center", flex: 1, gap: 10, justifyContent: "center", padding: 24 },
  panel: { backgroundColor: "#0B1826", borderTopLeftRadius: 20, borderTopRightRadius: 20, gap: 10, padding: 20 },
  title: { color: "#FFFFFF", fontSize: 17, fontWeight: "900" },
  helper: { color: "#9DB2C7", fontSize: 13, lineHeight: 18 },
  warning: { color: "#F5B14B", fontSize: 12, fontWeight: "700" },
  action: { alignItems: "center", backgroundColor: "#2E8CFF", borderRadius: 13, justifyContent: "center", minHeight: 48 },
  actionText: { color: "#06101B", fontSize: 14, fontWeight: "900" },
  secondary: { alignItems: "center", justifyContent: "center", minHeight: 44 },
  secondaryText: { color: "#9DB2C7", fontSize: 13, fontWeight: "700" },
  close: { alignItems: "center", justifyContent: "center", margin: 16, minHeight: 48 },
  closeText: { color: "#FFFFFF", fontSize: 14, fontWeight: "800" }
});
