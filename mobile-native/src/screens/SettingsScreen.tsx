import { useEffect, useState } from "react";
import { Alert, Pressable, StyleSheet, Text } from "react-native";
import { useNavigation } from "@react-navigation/native";
import { NativeStackNavigationProp } from "@react-navigation/native-stack";
import { registerPushDevice } from "../api/push";
import { openSupportWebFallback } from "../api/support";
import { Panel } from "../components/Panel";
import { Screen } from "../components/Screen";
import { RootStackParamList } from "../navigation/types";
import { signOut, signOutEverywhere, useAuth } from "../session/auth";
import {
  BiometricCapability,
  confirmAndEnableBiometricLogin,
  disableBiometricLogin,
  getBiometricCapability,
  isBiometricEnabledForCurrentSession
} from "../session/biometricAuth";
import { colors } from "../theme/colors";

export function SettingsScreen() {
  const { authState, setAuthState } = useAuth();
  const navigation = useNavigation<NativeStackNavigationProp<RootStackParamList>>();
  const currentUserId = Number(authState.user?.user_id || 0);

  const [biometricCapability, setBiometricCapability] = useState<BiometricCapability | null>(null);
  const [biometricEnabled, setBiometricEnabled] = useState(false);
  const [biometricBusy, setBiometricBusy] = useState(false);

  useEffect(() => {
    let mounted = true;
    Promise.all([getBiometricCapability(), isBiometricEnabledForCurrentSession()])
      .then(([capability, enabled]) => {
        if (!mounted) return;
        setBiometricCapability(capability);
        setBiometricEnabled(enabled);
      })
      .catch(() => undefined);
    return () => {
      mounted = false;
    };
  }, []);

  const biometricLabel = biometricCapability?.kind === "touchId" ? "Touch ID" : "Face ID";

  async function toggleBiometric() {
    if (biometricBusy || !currentUserId) return;
    setBiometricBusy(true);
    try {
      if (biometricEnabled) {
        await new Promise<void>((resolve) => {
          Alert.alert(
            `Turn off ${biometricLabel}?`,
            `Your saved biometric sign-in will be removed from this device. You'll use your password next time.`,
            [
              { text: "Cancel", style: "cancel", onPress: () => resolve() },
              {
                text: "Turn off",
                style: "destructive",
                onPress: async () => {
                  await disableBiometricLogin().catch(() => undefined);
                  setBiometricEnabled(false);
                  resolve();
                }
              }
            ]
          );
        });
      } else {
        const enabled = await confirmAndEnableBiometricLogin(currentUserId).catch(() => false);
        setBiometricEnabled(enabled);
        Alert.alert(
          enabled ? `${biometricLabel} enabled` : `${biometricLabel} not enabled`,
          enabled
            ? `Tap ${biometricLabel} on the sign-in screen to unlock PulseSoc next time.`
            : "We couldn't confirm your biometrics. Your password sign-in still works."
        );
      }
    } finally {
      setBiometricBusy(false);
    }
  }

  async function enablePush() {
    const result = await registerPushDevice();
    Alert.alert(result.ok === false ? "Push not enabled" : "Push ready", String(result.message || "Device registration sent."));
  }

  async function logout() {
    setAuthState(await signOut());
  }

  function logoutEverywhere() {
    Alert.alert("Sign out everywhere?", "This revokes your PulseSoc sessions on every device, including the WebView app.", [
      { text: "Cancel", style: "cancel" },
      {
        text: "Sign out everywhere",
        style: "destructive",
        onPress: () => signOutEverywhere().then(setAuthState).catch((error) => Alert.alert("Could not sign out", error instanceof Error ? error.message : "Try again."))
      }
    ]);
  }

  return (
    <Screen title="Settings" subtitle="Native permissions and session controls for the parallel app.">
      <Panel>
        <Text style={styles.title}>Native permissions</Text>
        <Pressable accessibilityRole="button" style={styles.button} onPress={enablePush}>
          <Text style={styles.buttonText}>Enable push notifications</Text>
        </Pressable>
        <Pressable accessibilityRole="button" style={styles.secondaryButton} onPress={() => navigation.navigate("NotificationPreferences")}>
          <Text style={styles.secondaryText}>Notification preferences</Text>
        </Pressable>
        <Pressable accessibilityRole="button" style={styles.secondaryButton} onPress={() => navigation.navigate("RegionTime")}>
          <Text style={styles.secondaryText}>Language, Region & Time</Text>
        </Pressable>
        <Pressable accessibilityRole="button" style={styles.secondaryButton} onPress={() => navigation.navigate("ActivityInbox", { title: "Activity Inbox" })}>
          <Text style={styles.secondaryText}>Activity Inbox</Text>
        </Pressable>
        <Pressable accessibilityRole="button" style={styles.secondaryButton} onPress={() => navigation.navigate("AccountCenter", { section: "account", title: "Account Center" })}>
          <Text style={styles.secondaryText}>Account Center</Text>
        </Pressable>
        <Pressable accessibilityRole="button" style={styles.secondaryButton} onPress={() => navigation.navigate("AccountCenter", { section: "security", title: "Security Center" })}>
          <Text style={styles.secondaryText}>Security Center</Text>
        </Pressable>
        <Pressable accessibilityRole="button" style={styles.secondaryButton} onPress={() => navigation.navigate("AccountCenter", { section: "privacy", title: "Privacy Center" })}>
          <Text style={styles.secondaryText}>Privacy Center</Text>
        </Pressable>
        <Pressable accessibilityRole="button" style={styles.secondaryButton} onPress={() => navigation.navigate("AccountCenter", { section: "devices", title: "Sessions and Devices" })}>
          <Text style={styles.secondaryText}>Sessions and devices</Text>
        </Pressable>
        <Pressable accessibilityRole="button" style={styles.secondaryButton} onPress={() => navigation.navigate("AccountHealth", { title: "Account Health" })}>
          <Text style={styles.secondaryText}>Account Health and Appeals</Text>
        </Pressable>
        <Pressable accessibilityRole="button" style={styles.secondaryButton} onPress={() => navigation.navigate("SafetyHub", { title: "Safety Hub" })}>
          <Text style={styles.secondaryText}>Safety Hub</Text>
        </Pressable>
        <Pressable accessibilityRole="button" style={styles.secondaryButton} onPress={() => navigation.navigate("Premium")}>
          <Text style={styles.secondaryText}>Premium and entitlements</Text>
        </Pressable>
        <Pressable accessibilityRole="button" style={styles.secondaryButton} onPress={() => navigation.navigate("CreatorStudio")}>
          <Text style={styles.secondaryText}>Creator Studio</Text>
        </Pressable>
        <Pressable accessibilityRole="button" style={styles.secondaryButton} onPress={() => navigation.navigate("ContentPlanner", { title: "Content Planner" })}>
          <Text style={styles.secondaryText}>Content Planner</Text>
        </Pressable>
        <Pressable accessibilityRole="button" style={styles.secondaryButton} onPress={() => navigation.navigate("Courses", { title: "Courses" })}>
          <Text style={styles.secondaryText}>Courses and Learning</Text>
        </Pressable>
        <Pressable accessibilityRole="button" style={styles.secondaryButton} onPress={() => navigation.navigate("SellerStore", { title: "Seller / Store" })}>
          <Text style={styles.secondaryText}>Seller / Store Management</Text>
        </Pressable>
        <Pressable accessibilityRole="button" style={styles.secondaryButton} onPress={() => navigation.navigate("BuyerOrders", { title: "Purchase History" })}>
          <Text style={styles.secondaryText}>Purchase History</Text>
        </Pressable>
        <Pressable accessibilityRole="button" style={styles.secondaryButton} onPress={() => navigation.navigate("GrowthCenter")}>
          <Text style={styles.secondaryText}>Growth Center</Text>
        </Pressable>
        <Pressable accessibilityRole="button" style={styles.secondaryButton} onPress={() => navigation.navigate("Events", { title: "Events" })}>
          <Text style={styles.secondaryText}>Events and scheduled Live</Text>
        </Pressable>
        <Pressable accessibilityRole="button" style={styles.secondaryButton} onPress={() => navigation.navigate("IntelligenceCenter")}>
          <Text style={styles.secondaryText}>Intelligence and alerts</Text>
        </Pressable>
        <Pressable accessibilityRole="button" style={styles.secondaryButton} onPress={() => navigation.navigate("AlertManagement", { title: "Alerts" })}>
          <Text style={styles.secondaryText}>Alert Management</Text>
        </Pressable>
        <Pressable accessibilityRole="button" style={styles.secondaryButton} onPress={() => navigation.navigate("TrustSafety", { title: "Trust & Safety", mode: "support" })}>
          <Text style={styles.secondaryText}>Trust and Safety</Text>
        </Pressable>
        <Pressable accessibilityRole="button" style={styles.secondaryButton} onPress={() => navigation.navigate("TrustSafetySupport", { title: "Support" })}>
          <Text style={styles.secondaryText}>Support Center</Text>
        </Pressable>
        <Pressable accessibilityRole="button" style={styles.secondaryButton} onPress={() => navigation.navigate("VerificationCenter", { title: "Verification Center" })}>
          <Text style={styles.secondaryText}>Verification Center</Text>
        </Pressable>
        <Pressable accessibilityRole="button" style={styles.secondaryButton} onPress={() => openSupportWebFallback("/privacy")}>
          <Text style={styles.secondaryText}>Privacy Policy</Text>
        </Pressable>
        <Pressable accessibilityRole="button" style={styles.secondaryButton} onPress={() => openSupportWebFallback("/terms")}>
          <Text style={styles.secondaryText}>Terms of Service</Text>
        </Pressable>
        <Pressable accessibilityRole="button" style={styles.secondaryButton} onPress={() => openSupportWebFallback("/account/settings")}>
          <Text style={styles.secondaryText}>Telegram companion setup</Text>
        </Pressable>
        <Text style={styles.muted}>Camera, microphone, media compression, and LiveKit call controls are Phase 2/3 QA-gated.</Text>
      </Panel>
      <Panel>
        <Text style={styles.title}>Login and Security</Text>
        {biometricCapability?.available ? (
          <>
            <Text style={styles.muted}>
              {biometricEnabled
                ? `${biometricLabel} is on. Unlock PulseSoc with ${biometricLabel} instead of your password. Your password still works as a fallback.`
                : `Turn on ${biometricLabel} to unlock PulseSoc without typing your password. PulseSoc never receives or stores your face.`}
            </Text>
            <Pressable
              accessibilityRole="switch"
              accessibilityState={{ checked: biometricEnabled, disabled: biometricBusy || !currentUserId }}
              accessibilityLabel={biometricEnabled ? `Turn off ${biometricLabel}` : `Enable ${biometricLabel}`}
              testID="settings-biometric-toggle"
              disabled={biometricBusy || !currentUserId}
              style={[biometricEnabled ? styles.secondaryButton : styles.button, (biometricBusy || !currentUserId) && styles.disabledButton]}
              onPress={toggleBiometric}
            >
              <Text style={biometricEnabled ? styles.secondaryText : styles.buttonText}>
                {biometricBusy ? "Please wait…" : biometricEnabled ? `Turn off ${biometricLabel}` : `Enable ${biometricLabel}`}
              </Text>
            </Pressable>
          </>
        ) : (
          <Text style={styles.muted}>
            {biometricCapability?.reason === "not_enrolled"
              ? "Set up Face ID or Touch ID in your device settings to unlock PulseSoc with biometrics."
              : "This device does not support Face ID or Touch ID."}
          </Text>
        )}
      </Panel>
      <Panel>
        <Text style={styles.title}>Session</Text>
        <Pressable accessibilityRole="button" style={styles.secondaryButton} onPress={logout}>
          <Text style={styles.secondaryText}>Sign out</Text>
        </Pressable>
        <Pressable accessibilityRole="button" style={styles.secondaryButton} onPress={logoutEverywhere}>
          <Text style={styles.dangerText}>Sign out on all devices</Text>
        </Pressable>
      </Panel>
    </Screen>
  );
}

const styles = StyleSheet.create({
  title: {
    color: colors.text,
    fontSize: 17,
    fontWeight: "800"
  },
  muted: {
    color: colors.muted,
    fontSize: 14,
    lineHeight: 20
  },
  button: {
    alignItems: "center",
    backgroundColor: colors.accent,
    borderRadius: 8,
    minHeight: 46,
    justifyContent: "center"
  },
  buttonText: {
    color: "#08110f",
    fontWeight: "900"
  },
  secondaryButton: {
    alignItems: "center",
    borderColor: colors.border,
    borderRadius: 8,
    borderWidth: StyleSheet.hairlineWidth,
    minHeight: 46,
    justifyContent: "center"
  },
  secondaryText: {
    color: colors.text,
    fontWeight: "800"
  },
  disabledButton: {
    opacity: 0.5
  },
  dangerText: {
    color: "#ff6b7a",
    fontWeight: "800"
  }
});
