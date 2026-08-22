import { useCallback, useEffect, useRef, useState } from "react";
import { Alert, Linking, Platform } from "react-native";
import { useNavigation } from "@react-navigation/native";
import { NativeStackNavigationProp } from "@react-navigation/native-stack";
import { SettingsHeader, SettingsSection, SettingsShell } from "../../settings/components/SettingsShell";
import {
  confirm,
  SettingsBadge,
  SettingsRow,
  SettingsSwitch,
  SettingsValue
} from "../../settings/components/SettingsControls";
import { usePreferenceGroup } from "../../settings/store";
import { RootStackParamList } from "../../navigation/types";
import { useAuth } from "../../session/auth";
import { AccountSecurity, disableTwoFactor, enableTwoFactor, getAccountSecurity, requestAccountPasswordChange } from "../../api/account";
import {
  BiometricCapability,
  confirmAndEnableBiometricLogin,
  disableBiometricLogin,
  getBiometricCapability,
  isBiometricEnabledForCurrentSession
} from "../../session/biometricAuth";

type Nav = NativeStackNavigationProp<RootStackParamList>;

/**
 * Name the sensor the way the platform names it. Calling a fingerprint reader
 * "Touch ID" on a Pixel is the kind of detail that makes a security screen feel
 * untrustworthy, and trust is the entire product of this screen.
 */
function biometricLabel(capability: BiometricCapability | null): string {
  const kind = capability?.kind ?? "none";
  if (Platform.OS === "android") {
    if (kind === "faceId") return "Face unlock";
    if (kind === "touchId") return "Fingerprint unlock";
    if (kind === "iris") return "Iris unlock";
    return "Biometric unlock";
  }
  if (kind === "touchId") return "Touch ID";
  if (kind === "iris") return "Iris unlock";
  return "Face ID";
}

/** Why the sensor is unusable, phrased as something the user can act on. */
function unavailableCopy(capability: BiometricCapability, label: string): string {
  if (capability.reason === "no_hardware") {
    return "This device doesn't have a biometric sensor, so PulseSoc can only be unlocked with your password.";
  }
  if (capability.reason === "not_enrolled") {
    return `Your device has the hardware, but no face or fingerprint is enrolled yet. Set up ${label} in your device settings, then come back.`;
  }
  return `${label} isn't available on this device right now.`;
}

/**
 * Security.
 *
 * Three different sources of truth meet here, and the screen keeps them
 * distinct rather than pretending they are one setting:
 *  - the server owns 2FA (`/api/account/security`),
 *  - the device keychain owns biometric enrolment (`session/biometricAuth`),
 *  - the preference store owns the advisory toggles (alerts, re-auth prompts).
 *
 * Server- and device-owned rows are therefore written through their own API
 * first and only mirrored into the preference store once the real mutation
 * succeeds — otherwise an optimistic switch would claim 2FA is on when the
 * account is still unprotected.
 */
export function SecuritySettingsScreen() {
  const navigation = useNavigation<Nav>();
  const { authState } = useAuth();
  const { value, setGroup, pending } = usePreferenceGroup("security");
  const currentUserId = Number(authState.user?.user_id || 0);

  const [security, setSecurity] = useState<AccountSecurity | null>(null);
  const [loadingSecurity, setLoadingSecurity] = useState(true);
  const [twoFactorBusy, setTwoFactorBusy] = useState(false);
  const [passwordBusy, setPasswordBusy] = useState(false);

  const [capability, setCapability] = useState<BiometricCapability | null>(null);
  const [biometricEnabled, setBiometricEnabled] = useState(false);
  const [biometricBusy, setBiometricBusy] = useState(false);

  const mounted = useRef(true);
  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
    };
  }, []);

  /**
   * The loaders below need to *read* the current preference to decide whether
   * the server disagrees with it, but must not *depend* on it — depending on it
   * would re-run the fetch on every toggle and fight the user's own change
   * mid-flight. A ref gives them the live value with a stable identity; closing
   * over `value` directly would have pinned them to the first render's snapshot
   * and made a later pull-to-refresh "correct" the switch against a stale
   * comparison.
   */
  const latestValue = useRef(value);
  latestValue.current = value;

  /* ------------------------------ Server state ----------------------------- */

  const loadSecurity = useCallback(async () => {
    try {
      const remote = await getAccountSecurity();
      if (!mounted.current) return;
      setSecurity(remote);
      // The account is authoritative for 2FA. If the cached preference drifted
      // (another device, an admin action), correct it rather than showing a
      // switch that disagrees with the account.
      if (Boolean(remote.two_factor_enabled) !== latestValue.current.twoFactorEnabled) {
        void setGroup({ twoFactorEnabled: Boolean(remote.two_factor_enabled) });
      }
    } catch {
      // Reads must never break the screen — the local mirror still renders, and
      // the row explains that we're showing the last known state.
      if (mounted.current) setSecurity(null);
    } finally {
      if (mounted.current) setLoadingSecurity(false);
    }
  }, [setGroup]);

  const loadBiometrics = useCallback(async () => {
    const [nextCapability, enabled] = await Promise.all([
      getBiometricCapability(),
      isBiometricEnabledForCurrentSession()
    ]).catch(() => [null, false] as [BiometricCapability | null, boolean]);
    if (!mounted.current) return;
    setCapability(nextCapability);
    setBiometricEnabled(enabled);
    // The keychain is the truth for biometrics; the preference is a device-local
    // mirror so other surfaces can read it without touching SecureStore. It is
    // listed in `DEVICE_LOCAL_KEYS`, so this correction is written to disk and
    // never to the account — otherwise enrolling a face here would tell every
    // other signed-in device that it has biometric unlock too, and each device
    // would overwrite the shared value with its own answer on every launch.
    if (enabled !== latestValue.current.biometricUnlock) void setGroup({ biometricUnlock: enabled });
  }, [setGroup]);

  useEffect(() => {
    void loadSecurity();
    void loadBiometrics();
  }, [loadSecurity, loadBiometrics]);

  const refresh = useCallback(async () => {
    await Promise.all([loadSecurity(), loadBiometrics()]);
  }, [loadSecurity, loadBiometrics]);

  /* ------------------------------- Password -------------------------------- */

  const accountEmail = String(security?.email || authState.user?.email || "").trim();

  /**
   * PulseSoc has no authenticated "change password" mutation — the only real
   * path is the emailed reset link. Sending that link is a genuine backend
   * action and, unlike an in-app form, it never puts the password on this
   * device at all.
   */
  const changePassword = useCallback(async () => {
    if (passwordBusy) return;
    if (!accountEmail) {
      Alert.alert(
        "Add an email first",
        "Password changes are confirmed by email. Add and verify an email address in Account, then try again.",
        [
          { text: "Not now", style: "cancel" },
          { text: "Open Account", onPress: () => navigation.navigate("AccountCenter", { section: "account", title: "Account" }) }
        ]
      );
      return;
    }
    const ok = await confirm({
      title: "Change your password",
      message: `We'll email a secure password-change link to ${accountEmail}. The link expires shortly and can only be used once.`,
      confirmLabel: "Send link"
    });
    if (!ok) return;
    setPasswordBusy(true);
    try {
      // No address is passed: `accountEmail` is masked for display ("h***@…"),
      // and sending it as the recovery identifier matched no account, so the
      // link was never sent while this screen still said "Check your email".
      // The server resolves the real address from the session.
      await requestAccountPasswordChange();
      Alert.alert("Check your email", `A password-change link is on its way to ${accountEmail}.`);
    } catch (error) {
      Alert.alert("Couldn't send the link", error instanceof Error ? error.message : "Check your connection and try again.");
    } finally {
      if (mounted.current) setPasswordBusy(false);
    }
  }, [accountEmail, navigation, passwordBusy]);

  /* --------------------------------- 2FA ----------------------------------- */

  const twoFactorOn = value.twoFactorEnabled;

  const toggleTwoFactor = useCallback(
    async (next: boolean) => {
      if (twoFactorBusy) return;
      if (!next) {
        // Turning protection off is the dangerous direction — make it deliberate.
        const ok = await confirm({
          title: "Turn off two-factor authentication?",
          message: "Anyone with your password alone will be able to sign in and approve sensitive changes.",
          confirmLabel: "Turn off",
          destructive: true
        });
        if (!ok) return;
      }
      setTwoFactorBusy(true);
      try {
        const response = next ? await enableTwoFactor() : await disableTwoFactor();
        if (!mounted.current) return;
        // Only mirror into preferences after the account actually changed.
        await setGroup({ twoFactorEnabled: next });
        setSecurity((current) => (current ? { ...current, two_factor_enabled: next } : current));
        Alert.alert(
          next ? "Two-factor is on" : "Two-factor is off",
          String(response?.message || (next ? "You'll confirm sensitive actions with a second step." : "Two-factor protection has been removed."))
        );
      } catch (error) {
        if (!mounted.current) return;
        Alert.alert(
          next ? "Couldn't turn on two-factor" : "Couldn't turn off two-factor",
          error instanceof Error ? error.message : "Your account is unchanged. Try again."
        );
      } finally {
        if (mounted.current) setTwoFactorBusy(false);
      }
    },
    [setGroup, twoFactorBusy]
  );

  /* ------------------------------- Biometrics ------------------------------ */

  const label = biometricLabel(capability);

  const toggleBiometric = useCallback(
    async (next: boolean) => {
      if (biometricBusy || !currentUserId) return;
      setBiometricBusy(true);
      try {
        if (!next) {
          const ok = await confirm({
            title: `Turn off ${label}?`,
            message: "Your saved biometric sign-in is removed from this device. You'll sign in with your password next time.",
            confirmLabel: "Turn off",
            destructive: true
          });
          if (!ok) return;
          await disableBiometricLogin().catch(() => undefined);
          if (!mounted.current) return;
          setBiometricEnabled(false);
          await setGroup({ biometricUnlock: false });
          return;
        }

        // Re-read capability at the moment of the tap: the user may have just
        // enrolled a face in OS settings and come back without remounting.
        const fresh = await getBiometricCapability();
        if (!mounted.current) return;
        setCapability(fresh);
        if (!fresh.available) {
          Alert.alert(`${label} unavailable`, unavailableCopy(fresh, label));
          return;
        }

        const enabled = await confirmAndEnableBiometricLogin(currentUserId).catch(() => false);
        if (!mounted.current) return;
        setBiometricEnabled(enabled);
        await setGroup({ biometricUnlock: enabled });
        Alert.alert(
          enabled ? `${label} enabled` : `${label} not enabled`,
          enabled
            ? `Tap ${label} on the sign-in screen to unlock PulseSoc next time.`
            : "We couldn't confirm your biometrics. Your password sign-in still works."
        );
      } finally {
        if (mounted.current) setBiometricBusy(false);
      }
    },
    [biometricBusy, currentUserId, label, setGroup]
  );

  const biometricUnavailable = capability !== null && !capability.available;

  return (
    <SettingsShell bottomDock={false} onRefresh={refresh}>
      <SettingsHeader title="Security" subtitle="Control how you sign in and what PulseSoc asks you to confirm." />

      <SettingsSection
        title="Password"
        footnote="PulseSoc never asks for your password inside a settings screen — password changes always go through a one-time link sent to your email."
      >
        <SettingsRow
          testID="security-change-password"
          title="Change password"
          subtitle={accountEmail ? `Send a change link to ${accountEmail}` : "Add an email address to your account first"}
          icon="key-outline"
          chevron
          busy={passwordBusy}
          onPress={changePassword}
          accessibilityRole="button"
          accessibilityHint="Sends a one-time password-change link to your account email."
        />
      </SettingsSection>

      <SettingsSection
        title="Two-factor authentication"
        busy={loadingSecurity || twoFactorBusy}
        footnote={
          security === null && !loadingSecurity
            ? "We couldn't reach your account just now, so this shows the last state we saw on this device."
            : "A second confirmation step is required for sign-ins and sensitive account changes."
        }
      >
        <SettingsRow
          testID="security-two-factor-status"
          title="Status"
          subtitle={twoFactorOn ? "Your account requires a second step." : "Your password alone can sign in to this account."}
          icon="shield-checkmark-outline"
          accessory={<SettingsBadge label={twoFactorOn ? "ON" : "OFF"} tone={twoFactorOn ? "accent" : "warning"} />}
        />
        <SettingsSwitch
          testID="security-two-factor-toggle"
          title="Require two-factor"
          subtitle="Confirm a second step when signing in or changing account details."
          icon="lock-closed-outline"
          value={twoFactorOn}
          busy={twoFactorBusy}
          disabled={loadingSecurity || twoFactorBusy}
          onValueChange={(next) => void toggleTwoFactor(next)}
        />
      </SettingsSection>

      <SettingsSection
        title="Unlock"
        footnote="Biometric data never leaves your device. PulseSoc only stores a device-encrypted sign-in token that your face or fingerprint releases."
      >
        {biometricUnavailable && capability ? (
          <SettingsRow
            testID="security-biometric-unavailable"
            title={label}
            subtitle={unavailableCopy(capability, label)}
            icon="finger-print-outline"
            accessory={<SettingsBadge label={capability.reason === "no_hardware" ? "NO SENSOR" : "NOT SET UP"} tone="muted" />}
            chevron={capability.reason === "not_enrolled"}
            // Nothing to toggle here, but "open the place where you can fix it"
            // is still a real action — better than a disabled row with no exit.
            onPress={
              capability.reason === "not_enrolled"
                ? () => {
                    void Linking.openSettings().catch(() =>
                      Alert.alert("Couldn't open settings", `Open your device settings and enrol ${label} manually.`)
                    );
                  }
                : undefined
            }
            accessibilityRole={capability.reason === "not_enrolled" ? "button" : "none"}
            accessibilityHint={capability.reason === "not_enrolled" ? "Opens your device settings to enrol biometrics." : undefined}
          />
        ) : (
          <SettingsSwitch
            testID="security-biometric-toggle"
            title={`Unlock with ${label}`}
            subtitle={
              biometricEnabled
                ? `${label} is on for this device. Your password still works as a fallback.`
                : `Sign in without typing your password on this device.`
            }
            icon="finger-print-outline"
            value={biometricEnabled}
            busy={biometricBusy}
            disabled={biometricBusy || !currentUserId || capability === null}
            onValueChange={(next) => void toggleBiometric(next)}
          />
        )}
      </SettingsSection>

      <SettingsSection title="Alerts & confirmations" busy={pending}>
        <SettingsSwitch
          testID="security-login-alerts"
          title="Login alerts"
          subtitle="Notify me when a new device or unrecognised location signs in."
          icon="notifications-outline"
          value={value.loginAlerts}
          onValueChange={(next) => void setGroup({ loginAlerts: next })}
        />
        <SettingsSwitch
          testID="security-require-password"
          title="Confirm sensitive changes"
          subtitle="Re-enter your password before changing your email, phone, or security settings."
          icon="shield-outline"
          value={value.requirePasswordForSensitiveChanges}
          onValueChange={(next) => void setGroup({ requirePasswordForSensitiveChanges: next })}
        />
      </SettingsSection>

      <SettingsSection title="Where you're signed in">
        <SettingsRow
          testID="security-open-sessions"
          title="Sessions & devices"
          subtitle="Review active sessions and sign out remotely."
          icon="phone-portrait-outline"
          chevron
          accessory={
            security?.active_sessions_count ? <SettingsValue>{security.active_sessions_count} active</SettingsValue> : undefined
          }
          onPress={() => navigation.navigate("SessionsDevices")}
          accessibilityRole="button"
        />
      </SettingsSection>
    </SettingsShell>
  );
}
