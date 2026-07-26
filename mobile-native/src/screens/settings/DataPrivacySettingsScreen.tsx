import { useCallback, useEffect, useRef, useState } from "react";
import { StyleSheet, Text, View } from "react-native";
import { Ionicons } from "@expo/vector-icons";
import { animateNextLayout, SettingsHeader, SettingsSection, SettingsShell } from "../../settings/components/SettingsShell";
import {
  confirm,
  DestructiveConfirmField,
  SettingsButton,
  SettingsRow,
  SettingsSwitch
} from "../../settings/components/SettingsControls";
import { usePreferenceGroup } from "../../settings/store";
import { pulseApi } from "../../api/pulseApi";
import { signOut, useAuth } from "../../session/auth";
import { useTheme } from "../../theme/ThemeContext";

const DATA_EXPORT_PATH = "/api/pulse/mobile/settings/data-export";
const DELETE_ACCOUNT_PATH = "/api/pulse/mobile/settings/delete-account";

/** Typed exactly, in capitals, before the delete button becomes usable. */
const DELETE_PHRASE = "DELETE";

type DataRequestResponse = { message?: unknown };

/** Prefer whatever the server said; fall back to something the user can act on. */
function serverMessage(payload: DataRequestResponse, fallback: string): string {
  return typeof payload.message === "string" && payload.message.trim() ? payload.message.trim() : fallback;
}

function readableError(error: unknown, fallback: string): string {
  const message = error instanceof Error ? error.message.trim() : "";
  return message || fallback;
}

/* -------------------------------------------------------------------------- */
/*                                Result banner                                */
/* -------------------------------------------------------------------------- */

function ResultNote({ tone, message, testID }: { tone: "success" | "error"; message: string; testID: string }) {
  const theme = useTheme();
  const color = tone === "error" ? theme.colors.danger : theme.colors.accent;
  return (
    <View testID={testID} accessibilityLiveRegion="polite" style={styles.result}>
      <Ionicons
        name={tone === "error" ? "alert-circle-outline" : "checkmark-circle-outline"}
        size={theme.scaleFont(16)}
        color={color}
      />
      <Text style={{ color, flex: 1, fontSize: theme.scaleFont(13), lineHeight: theme.scaleFont(18) }}>{message}</Text>
    </View>
  );
}

/* -------------------------------------------------------------------------- */
/*                                   Screen                                    */
/* -------------------------------------------------------------------------- */

/**
 * Data & personalization.
 *
 * The switches are ordinary preferences. The two actions at the bottom are not:
 * they leave the device, they cannot be un-done by flipping a toggle back, and
 * a failure has to be visible. Every path through them ends in either a stated
 * outcome or a stated error — there is no branch that quietly does nothing.
 */
export function DataPrivacySettingsScreen() {
  const theme = useTheme();
  const { value, setGroup, pending } = usePreferenceGroup("data");
  const { setAuthState } = useAuth();

  const [exporting, setExporting] = useState(false);
  const [exportResult, setExportResult] = useState<string | null>(null);
  const [exportError, setExportError] = useState<string | null>(null);

  const [deleteOpen, setDeleteOpen] = useState(false);
  const [deletePhrase, setDeletePhrase] = useState("");
  const [deleting, setDeleting] = useState(false);
  const [deleteResult, setDeleteResult] = useState<string | null>(null);
  const [deleteError, setDeleteError] = useState<string | null>(null);
  const [signingOut, setSigningOut] = useState(false);

  // These requests outlive a fast back-swipe; the result must not be written
  // into a component that is already gone.
  const mounted = useRef(true);
  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
    };
  }, []);

  const phraseMatched = deletePhrase.trim().toUpperCase() === DELETE_PHRASE;

  const requestExport = useCallback(async () => {
    const proceed = await confirm({
      title: "Request your data?",
      message: "We'll build an archive of your account and email you a private download link when it's ready. This can take up to 48 hours.",
      confirmLabel: "Request export"
    });
    if (!proceed) return;

    setExporting(true);
    setExportError(null);
    setExportResult(null);
    try {
      const payload = await pulseApi<DataRequestResponse>(DATA_EXPORT_PATH, {
        method: "POST",
        body: JSON.stringify({ format: "json", source: "native_settings" })
      });
      if (!mounted.current) return;
      setExportResult(serverMessage(payload, "Export requested. We'll email a download link to the address on your account."));
    } catch (error) {
      if (!mounted.current) return;
      setExportError(readableError(error, "We couldn't request your export. Check your connection and try again."));
    } finally {
      if (mounted.current) setExporting(false);
    }
  }, []);

  const openDeletion = useCallback(() => {
    animateNextLayout(theme.reduceMotion);
    setDeleteError(null);
    setDeletePhrase("");
    setDeleteOpen(true);
  }, [theme.reduceMotion]);

  const cancelDeletion = useCallback(() => {
    animateNextLayout(theme.reduceMotion);
    setDeleteOpen(false);
    setDeletePhrase("");
    setDeleteError(null);
  }, [theme.reduceMotion]);

  const requestDeletion = useCallback(async () => {
    if (!phraseMatched) return;
    // Typed phrase *and* a dialog: the phrase proves intent, the dialog states
    // the consequence one last time in the user's own language.
    const proceed = await confirm({
      title: "Delete your PulseSoc account?",
      message: "Your profile, posts, messages, and followers will be removed. This cannot be undone from the app.",
      confirmLabel: "Delete account",
      destructive: true
    });
    if (!proceed) return;

    setDeleting(true);
    setDeleteError(null);
    try {
      const payload = await pulseApi<DataRequestResponse>(DELETE_ACCOUNT_PATH, {
        method: "POST",
        body: JSON.stringify({ confirmation: DELETE_PHRASE, source: "native_settings" })
      });
      if (!mounted.current) return;
      animateNextLayout(theme.reduceMotion);
      setDeleteResult(serverMessage(payload, "Your account is scheduled for deletion. Sign out to finish — signing back in before it completes will cancel it."));
      setDeleteOpen(false);
      setDeletePhrase("");
    } catch (error) {
      if (!mounted.current) return;
      setDeleteError(readableError(error, "We couldn't submit your deletion request. Nothing has been deleted — try again."));
    } finally {
      if (mounted.current) setDeleting(false);
    }
  }, [phraseMatched, theme.reduceMotion]);

  const finishSignOut = useCallback(async () => {
    setSigningOut(true);
    try {
      // The session is on its way out server-side; leaving the user inside a
      // half-live account is the one ending this flow must not have.
      setAuthState(await signOut());
    } catch (error) {
      if (mounted.current) {
        setSigningOut(false);
        setDeleteError(readableError(error, "We couldn't sign you out. Your deletion request was still received."));
      }
    }
  }, [setAuthState]);

  return (
    <SettingsShell bottomDock={false}>
      <SettingsHeader title="Data & personalization" subtitle="What PulseSoc collects, what it does with it, and how to take it back." />

      <SettingsSection
        title="Personalization"
        busy={pending}
        footnote="Turning off personalized ads doesn't reduce how many ads you see — only how well they match your interests."
      >
        <SettingsSwitch
          testID="data-personalized-ads"
          title="Personalized ads"
          subtitle="Use your activity on PulseSoc to choose which ads you're shown."
          icon="megaphone-outline"
          value={value.personalizedAds}
          onValueChange={(next) => void setGroup({ personalizedAds: next })}
        />
        <SettingsSwitch
          testID="data-activity-status-sharing"
          title="Share activity status"
          subtitle="Let partner surfaces and integrations see that you're active on PulseSoc."
          icon="share-social-outline"
          value={value.activityStatusSharing}
          onValueChange={(next) => void setGroup({ activityStatusSharing: next })}
        />
      </SettingsSection>

      <SettingsSection
        title="Diagnostics"
        description="Neither of these carries the contents of your posts or messages."
      >
        <SettingsSwitch
          testID="data-share-analytics"
          title="Share usage analytics"
          subtitle="Which screens and features get used, so we know what to improve."
          icon="bar-chart-outline"
          value={value.shareAnalytics}
          onValueChange={(next) => void setGroup({ shareAnalytics: next })}
        />
        <SettingsSwitch
          testID="data-share-crash-reports"
          title="Share crash reports"
          subtitle="Sends a stack trace when PulseSoc crashes. With this off, crashes you hit are invisible to us."
          icon="bug-outline"
          value={value.shareCrashReports}
          onValueChange={(next) => void setGroup({ shareCrashReports: next })}
        />
      </SettingsSection>

      <SettingsSection
        title="Your data"
        footnote="An export includes your profile, posts, comments, messages, and follower lists in machine-readable JSON."
      >
        <SettingsRow
          testID="data-export-row"
          title="Request a copy of your data"
          subtitle="Emailed to you as a private download link."
          icon="download-outline"
          busy={exporting}
          onPress={() => void requestExport()}
          accessibilityRole="button"
          accessibilityHint="Starts a data export request."
          chevron={!exporting}
        />
        {exportResult ? <ResultNote testID="data-export-success" tone="success" message={exportResult} /> : null}
        {exportError ? <ResultNote testID="data-export-error" tone="error" message={exportError} /> : null}
      </SettingsSection>

      <SettingsSection
        title="Delete account"
        description="Permanent. Your profile, posts, messages, and followers are removed."
      >
        {deleteResult ? (
          <View style={[styles.panel, { paddingHorizontal: theme.metrics.rowPaddingHorizontal }]}>
            <ResultNote testID="data-delete-success" tone="success" message={deleteResult} />
            <View style={{ marginTop: 12 }}>
              <SettingsButton
                testID="data-delete-sign-out"
                label="Sign out now"
                icon="log-out-outline"
                variant="secondary"
                busy={signingOut}
                onPress={() => void finishSignOut()}
              />
            </View>
          </View>
        ) : deleteOpen ? (
          <View style={styles.panel}>
            <DestructiveConfirmField
              phrase={DELETE_PHRASE}
              value={deletePhrase}
              onChangeText={setDeletePhrase}
              label={`Type ${DELETE_PHRASE} to confirm`}
            />
            <View style={[styles.panelActions, { paddingHorizontal: theme.metrics.rowPaddingHorizontal }]}>
              <SettingsButton
                testID="data-delete-submit"
                label="Delete my account"
                icon="trash-outline"
                variant="destructive"
                busy={deleting}
                disabled={!phraseMatched}
                onPress={() => void requestDeletion()}
              />
              <SettingsButton
                testID="data-delete-cancel"
                label="Keep my account"
                variant="secondary"
                disabled={deleting}
                onPress={cancelDeletion}
              />
              {deleteError ? <ResultNote testID="data-delete-error" tone="error" message={deleteError} /> : null}
            </View>
          </View>
        ) : (
          <SettingsRow
            testID="data-delete-row"
            title="Delete my account"
            subtitle="You'll be asked to type a confirmation phrase."
            icon="trash-outline"
            tone="danger"
            onPress={openDeletion}
            accessibilityRole="button"
            accessibilityHint="Opens the account deletion confirmation."
            chevron
          />
        )}
      </SettingsSection>
    </SettingsShell>
  );
}

const styles = StyleSheet.create({
  panel: { paddingBottom: 14, paddingTop: 4, width: "100%" },
  panelActions: { gap: 10, marginTop: 4 },
  result: { alignItems: "flex-start", flexDirection: "row", gap: 8, paddingHorizontal: 16, paddingVertical: 12 }
});
