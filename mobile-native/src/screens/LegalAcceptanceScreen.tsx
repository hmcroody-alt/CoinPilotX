import { useCallback, useState } from "react";
import { ActivityIndicator, Linking, Pressable, ScrollView, Text, View } from "react-native";
import { SafeAreaView } from "react-native-safe-area-context";
import { LegalAcceptanceChallenge, PulseUser } from "../api/auth";
import { PULSE_API_BASE_URL } from "../api/config";
import { PulseApiError } from "../api/pulseApi";
import { useTranslation } from "../i18n";
import { completeLegalAcceptance, signOut, unauthenticatedState, useAuth } from "../session/auth";
import { colors } from "../theme/colors";
import { createThemedStyles } from "../theme/themedStyles";

type Props = {
  challenge: LegalAcceptanceChallenge;
  user?: PulseUser | null;
};

/**
 * The step a member sees when PulseSoc's Terms or Privacy Policy have been
 * revised since this account last agreed to them.
 *
 * It is not a checkbox on the sign-in form. The question it answers is "does
 * this account have acceptance on file for the versions in force", which the
 * server has already decided; this screen exists to let the member read what
 * changed and answer it. So it has exactly two outcomes — accept, or sign out —
 * and no way to reach the application in between.
 *
 * Every document row opens the canonical page on pulsesoc.com rather than the
 * copy bundled under `settings/legalContent.ts`. That copy is a snapshot and is
 * dated behind the documents in force; a member who accepted version X after
 * reading X-1 has a ledger row that asserts something untrue, which is worse
 * than no row at all.
 */
export function LegalAcceptanceScreen({ challenge, user }: Props) {
  const { t } = useTranslation();
  const { setAuthState } = useAuth();
  const [busy, setBusy] = useState(false);
  const [signingOut, setSigningOut] = useState(false);
  const [error, setError] = useState("");
  const [current, setCurrent] = useState(challenge);

  const openDocument = useCallback((path: string) => {
    if (!path) return;
    Linking.openURL(`${PULSE_API_BASE_URL}${path}`).catch(() => setError(t("errors:auth.unreachable")));
  }, [t]);

  const accept = useCallback(async () => {
    if (busy || signingOut) return;
    setBusy(true);
    setError("");
    try {
      setAuthState(await completeLegalAcceptance(current));
    } catch (rejection) {
      // The documents were revised while this screen was open. The server
      // refused the stale ticket and described the new versions, so re-enter the
      // step against those rather than reporting a failure the member cannot
      // act on -- and rather than recording agreement to superseded text.
      const fresh =
        rejection instanceof PulseApiError && rejection.code === "legal_acceptance_required"
          ? (rejection.details?.legal_acceptance as LegalAcceptanceChallenge | undefined)
          : undefined;
      if (fresh?.documents?.length) {
        setCurrent(fresh);
        setError(t("auth:legalAcceptance.documentsChanged"));
      } else if (rejection instanceof PulseApiError && (rejection.status === 401 || rejection.status === 403)) {
        // Credentials no longer answer for this account -- an expired ticket, a
        // revoked token, a restriction applied since sign-in. Returning the
        // member to sign-in is the only honest next step.
        setAuthState(await signOut());
        return;
      } else {
        setError(rejection instanceof Error ? rejection.message : t("errors:auth.unableToSignIn"));
      }
    } finally {
      setBusy(false);
    }
  }, [busy, signingOut, current, setAuthState, t]);

  const leave = useCallback(async () => {
    if (busy || signingOut) return;
    setSigningOut(true);
    // A failed sign-out still ends in the signed-out state: this step is the
    // only thing standing between the member and the sign-in form, and leaving
    // them on it because a cleanup call failed would trap them here.
    setAuthState(await signOut().catch(() => unauthenticatedState()));
  }, [busy, signingOut, setAuthState]);

  const greeting = String(user?.display_name || "").trim();

  return (
    <SafeAreaView style={styles.root}>
      <ScrollView contentContainerStyle={styles.content} keyboardShouldPersistTaps="handled">
        <Text accessibilityRole="header" style={styles.title}>
          {t("auth:legalAcceptance.title")}
        </Text>
        <Text style={styles.body}>
          {greeting
            ? t("auth:legalAcceptance.bodyNamed", { name: greeting })
            : t("auth:legalAcceptance.body")}
        </Text>

        <View style={styles.documents}>
          {current.documents.map((document) => (
            <Pressable
              key={document.document}
              accessibilityRole="link"
              accessibilityLabel={t("auth:legalAcceptance.readDocument", { document: document.title })}
              accessibilityHint={t("auth:legalAcceptance.opensInBrowser")}
              disabled={busy || signingOut}
              onPress={() => openDocument(document.path)}
              style={styles.document}
            >
              <Text style={styles.documentTitle}>{document.title}</Text>
              <Text style={styles.documentAction}>{t("auth:legalAcceptance.read")}</Text>
            </Pressable>
          ))}
        </View>

        {error ? (
          <Text accessibilityLiveRegion="polite" style={styles.error}>
            {error}
          </Text>
        ) : null}

        <Pressable
          accessibilityRole="button"
          accessibilityLabel={t("auth:legalAcceptance.accept")}
          accessibilityState={{ disabled: busy || signingOut, busy }}
          disabled={busy || signingOut}
          onPress={accept}
          style={[styles.accept, (busy || signingOut) && styles.disabled]}
        >
          {busy ? (
            <ActivityIndicator color={colors.background} />
          ) : (
            <Text style={styles.acceptLabel}>{t("auth:legalAcceptance.accept")}</Text>
          )}
        </Pressable>
        <Text style={styles.disclosure}>{t("auth:legalAcceptance.disclosure")}</Text>

        <Pressable
          accessibilityRole="button"
          accessibilityLabel={t("auth:session.signOut")}
          accessibilityState={{ disabled: busy || signingOut, busy: signingOut }}
          disabled={busy || signingOut}
          onPress={leave}
          style={styles.leave}
        >
          <Text style={styles.leaveLabel}>
            {signingOut ? t("auth:session.signingOut") : t("auth:session.signOut")}
          </Text>
        </Pressable>
      </ScrollView>
    </SafeAreaView>
  );
}

const styles = createThemedStyles(() => ({
  root: {
    flex: 1,
    backgroundColor: colors.background
  },
  content: {
    flexGrow: 1,
    justifyContent: "center",
    paddingHorizontal: 24,
    paddingVertical: 32
  },
  title: {
    color: colors.text,
    fontSize: 26,
    fontWeight: "800",
    marginBottom: 12
  },
  body: {
    color: colors.muted,
    fontSize: 15,
    lineHeight: 22,
    marginBottom: 24
  },
  documents: {
    borderColor: colors.border,
    borderRadius: 14,
    borderWidth: 1,
    marginBottom: 20,
    overflow: "hidden"
  },
  document: {
    alignItems: "center",
    borderBottomColor: colors.border,
    borderBottomWidth: 1,
    flexDirection: "row",
    justifyContent: "space-between",
    paddingHorizontal: 16,
    paddingVertical: 16
  },
  documentTitle: {
    color: colors.text,
    flexShrink: 1,
    fontSize: 16,
    fontWeight: "600"
  },
  documentAction: {
    color: colors.accent,
    fontSize: 14,
    fontWeight: "700",
    marginLeft: 12
  },
  error: {
    color: colors.danger,
    fontSize: 14,
    lineHeight: 20,
    marginBottom: 16
  },
  accept: {
    alignItems: "center",
    backgroundColor: colors.accent,
    borderRadius: 14,
    justifyContent: "center",
    minHeight: 52
  },
  disabled: {
    opacity: 0.6
  },
  acceptLabel: {
    color: colors.background,
    fontSize: 16,
    fontWeight: "800"
  },
  disclosure: {
    color: colors.muted,
    fontSize: 12,
    lineHeight: 18,
    marginTop: 12,
    textAlign: "center"
  },
  leave: {
    alignItems: "center",
    marginTop: 24,
    paddingVertical: 12
  },
  leaveLabel: {
    color: colors.muted,
    fontSize: 14,
    fontWeight: "600"
  }
}));
