/**
 * The Apple and Google buttons on the login screen.
 *
 * ## Why neither mark is drawn here
 *
 * Both providers publish presentation requirements, and both ship a control
 * that satisfies them: Apple's `AppleAuthenticationButton` and Google's
 * `GoogleSigninButton` are native views rendered by the provider's own SDK.
 * Using them is not a shortcut -- it is the only way to be certain the mark,
 * its clear space, the corner radius and the type are the ones the provider
 * currently requires, including after an SDK upgrade changes them. A
 * hand-built `<Pressable>` with a bundled PNG would look right today and
 * silently drift.
 *
 * It costs a little visual control: the two buttons are not pixel-identical to
 * each other or to PulseSoc's own buttons. That is the correct trade. Inside a
 * provider button, the provider's recognisability is the thing being sold --
 * a member needs to believe the Apple button is really Apple's.
 *
 * ## Why a button may be absent
 *
 * Neither button is rendered on availability alone. Apple's is gated on the
 * device actually offering Sign in with Apple, Google's on this build having
 * been configured with an iOS client id. A provider button that opens a sheet
 * which cannot complete is worse than no button: the member has already
 * committed to an identity choice by the time it fails, and the failure looks
 * like PulseSoc rejecting their Apple account.
 *
 * ## Why Google's button is required lazily
 *
 * Same reason `signInWithGoogleSheet` requires its module lazily: the native
 * Google module only exists in a build whose config plugin was applied, and
 * that plugin is only applied when an iOS client id is configured. A static
 * import would make the entire login screen fail to load in a build without
 * one -- which is every build until the owner creates that client. The require
 * sits behind the same `available` flag that decides whether to render it, so
 * it is never reached in a build that lacks the module.
 */

import { useEffect, useState } from "react";
import { ActivityIndicator, StyleSheet, Text, View } from "react-native";
import * as AppleAuthentication from "expo-apple-authentication";
import { FederatedProvider, appleSignInAvailable, googleSignInAvailable } from "../../auth/providerSheets";
import { useTranslation } from "../../i18n";
import { colors } from "../../theme/colors";

export type ProviderSignInButtonsProps = {
  onSelect: (provider: FederatedProvider) => void;
  /** The provider whose sheet is open, or null. Disables both buttons. */
  busyProvider?: FederatedProvider | null;
  /** Test seam: skip the async availability probe and force both states. */
  availabilityOverride?: { apple: boolean; google: boolean };
};

export function ProviderSignInButtons({
  onSelect,
  busyProvider = null,
  availabilityOverride
}: ProviderSignInButtonsProps) {
  const { t } = useTranslation();
  const [appleReady, setAppleReady] = useState(availabilityOverride?.apple ?? false);

  // Google's answer is synchronous (a config value), Apple's is a native call.
  const googleReady = availabilityOverride?.google ?? googleSignInAvailable();

  useEffect(() => {
    if (availabilityOverride) return undefined;
    let mounted = true;
    appleSignInAvailable()
      .then((available) => {
        if (mounted) setAppleReady(available);
      })
      .catch(() => undefined);
    return () => {
      mounted = false;
    };
  }, [availabilityOverride]);

  // Render nothing at all rather than an empty divider. A lone "or continue
  // with" above no buttons reads as a broken screen.
  if (!appleReady && !googleReady) return null;

  const busy = busyProvider !== null;

  return (
    <View style={styles.container} testID="provider-sign-in-buttons">
      <View style={styles.dividerRow}>
        <View style={styles.dividerLine} />
        <Text style={styles.dividerText} maxFontSizeMultiplier={1.4}>
          {t("auth:signIn.federatedDivider")}
        </Text>
        <View style={styles.dividerLine} />
      </View>

      {appleReady ? (
        <View style={styles.buttonSlot}>
          <AppleAuthentication.AppleAuthenticationButton
            testID="continue-with-apple"
            // CONTINUE rather than SIGN_IN: the flow behind it resolves to a
            // sign-in or a signup on its own, so the member must not be asked
            // to classify themselves before tapping it.
            buttonType={AppleAuthentication.AppleAuthenticationButtonType.CONTINUE}
            buttonStyle={AppleAuthentication.AppleAuthenticationButtonStyle.WHITE}
            cornerRadius={12}
            style={styles.appleButton}
            onPress={() => {
              if (!busy) onSelect("apple");
            }}
          />
          {busyProvider === "apple" ? <ProviderBusyVeil /> : null}
        </View>
      ) : null}

      {googleReady ? (
        <View style={styles.buttonSlot}>
          <GoogleButton disabled={busy} onPress={() => onSelect("google")} />
          {busyProvider === "google" ? <ProviderBusyVeil /> : null}
        </View>
      ) : null}
    </View>
  );
}

/**
 * Google's own button, required at render time.
 *
 * Returns null if the module is genuinely missing rather than throwing: this
 * component is only reached when `googleSignInAvailable()` is true, so a
 * missing module here means the build was configured with a client id but
 * without the plugin. That is a build mistake, and it should cost the Google
 * button, not the whole login screen.
 */
function GoogleButton({ disabled, onPress }: { disabled: boolean; onPress: () => void }) {
  let GoogleSigninButton: typeof import("@react-native-google-signin/google-signin").GoogleSigninButton;
  try {
    ({ GoogleSigninButton } = require("@react-native-google-signin/google-signin") as typeof import("@react-native-google-signin/google-signin"));
  } catch {
    return null;
  }

  return (
    <GoogleSigninButton
      testID="continue-with-google"
      size={GoogleSigninButton.Size.Wide}
      color={GoogleSigninButton.Color.Light}
      disabled={disabled}
      style={styles.googleButton}
      onPress={onPress}
    />
  );
}

/**
 * Covers the provider button while its sheet is opening.
 *
 * Neither vendor control exposes a loading state, and neither can be given one
 * without reimplementing it. An overlay keeps the provider's presentation
 * intact and still tells the member the tap registered -- the alternative is a
 * button that looks idle for the second before the sheet animates in, which is
 * exactly how double-taps happen.
 */
function ProviderBusyVeil() {
  return (
    <View style={styles.busyVeil} pointerEvents="none">
      <ActivityIndicator color={colors.text} />
    </View>
  );
}

const styles = StyleSheet.create({
  container: {
    gap: 10,
    width: "100%"
  },
  dividerRow: {
    alignItems: "center",
    flexDirection: "row",
    gap: 10
  },
  dividerLine: {
    backgroundColor: colors.border,
    flex: 1,
    height: StyleSheet.hairlineWidth
  },
  dividerText: {
    color: colors.muted,
    fontSize: 12,
    fontWeight: "700",
    letterSpacing: 0.6,
    textTransform: "uppercase"
  },
  buttonSlot: {
    justifyContent: "center",
    width: "100%"
  },
  appleButton: {
    height: 48,
    width: "100%"
  },
  googleButton: {
    height: 48,
    width: "100%"
  },
  busyVeil: {
    alignItems: "center",
    backgroundColor: "rgba(0,0,0,0.35)",
    borderRadius: 12,
    bottom: 0,
    justifyContent: "center",
    left: 0,
    position: "absolute",
    right: 0,
    top: 0
  }
});
