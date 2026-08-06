import { NativeStackScreenProps } from "@react-navigation/native-stack";
import { useCallback, useEffect, useMemo, useState } from "react";
import { ActivityIndicator, AppState, Pressable, RefreshControl, ScrollView, StyleSheet, Text, TextInput, View } from "react-native";
import {
  loadCachedVerificationState,
  loadVerificationState,
  pickAndUploadVerificationDocument,
  startVerificationRequest,
  submitVerificationAppeal,
  verificationActions,
  VerificationState,
  VerificationTrackKey,
  verificationStatusLabel,
  verificationTrackLabel
} from "../api/verification";
import { Panel } from "../components/Panel";
import { RootStackParamList } from "../navigation/types";
import { colors } from "../theme/colors";
import { createThemedStyles } from "../theme/themedStyles";

type Props =
  | NativeStackScreenProps<RootStackParamList, "VerificationCenter">
  | NativeStackScreenProps<RootStackParamList, "VerificationWebCenter">;

const documentTypes = ["government_id", "business_document", "selfie"] as const;

export function VerificationCenterScreen({ navigation, route }: Props) {
  const [state, setState] = useState<VerificationState | null>(null);
  const [selectedTrack, setSelectedTrack] = useState<VerificationTrackKey>(normalizeRouteTrack(route.params?.track));
  const [documentType, setDocumentType] = useState<(typeof documentTypes)[number]>("government_id");
  const [appealNote, setAppealNote] = useState("");
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [offline, setOffline] = useState(false);
  const [busy, setBusy] = useState("");
  const [notice, setNotice] = useState("");
  const [error, setError] = useState("");

  /**
   * Every control on this screen asks the same question — what is this person
   * allowed to do right now — so it is answered once, here, from the derivation
   * in `api/verification`. The screen renders the answer and does not second
   * guess it.
   */
  const actions = useMemo(
    () =>
      verificationActions({
        status: state?.status || "not_started",
        requestId: state?.requestId,
        selectedTrack,
        currentTrack: state?.verificationType
      }),
    [state?.status, state?.requestId, state?.verificationType, selectedTrack]
  );

  const load = useCallback(async (mode: "initial" | "refresh" = "initial") => {
    setError("");
    setOffline(false);
    if (mode === "initial") setLoading(true);
    if (mode === "refresh") setRefreshing(true);
    try {
      const next = await loadVerificationState();
      setState(next);
      if (!route.params?.track) setSelectedTrack(next.verificationType);
    } catch (loadError) {
      const cached = await loadCachedVerificationState();
      if (cached) {
        setState(cached);
        setOffline(true);
      }
      setError(loadError instanceof Error ? loadError.message : "Verification state could not load.");
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }, [route.params?.track]);

  useEffect(() => {
    navigation.setOptions({ title: route.params?.title || "Verification Center" });
  }, [navigation, route.params?.title]);

  useEffect(() => {
    load("initial").catch(() => undefined);
  }, [load]);

  useEffect(() => {
    const sub = AppState.addEventListener("change", (next) => {
      if (next === "active") load("refresh").catch(() => undefined);
    });
    return () => sub.remove();
  }, [load]);

  async function submitRequest() {
    setBusy("request");
    setNotice("");
    setError("");
    try {
      const result = await startVerificationRequest(selectedTrack);
      setNotice(result.message || "Verification request submitted.");
      await load("refresh");
    } catch (requestError) {
      setError(requestError instanceof Error ? requestError.message : "Verification request could not be submitted.");
    } finally {
      setBusy("");
    }
  }

  async function uploadDocument() {
    if (!state?.requestId) {
      setError("Start a verification request before uploading private evidence.");
      setNotice("");
      return;
    }
    setBusy("document");
    setNotice("");
    setError("");
    try {
      const result = await pickAndUploadVerificationDocument(state.requestId, documentType);
      setNotice(result.message || "Verification document uploaded for private review.");
      await load("refresh");
    } catch (uploadError) {
      setError(uploadError instanceof Error ? uploadError.message : "Verification document upload failed.");
    } finally {
      setBusy("");
    }
  }

  async function submitAppeal() {
    if (!state?.requestId || appealNote.trim().length < 8) {
      setError("Add an appeal note for an existing rejected, suspended, or needs-more-info request.");
      setNotice("");
      return;
    }
    setBusy("appeal");
    setNotice("");
    setError("");
    try {
      const result = await submitVerificationAppeal(state.requestId, appealNote.trim());
      setAppealNote("");
      setNotice(result.message || "Verification appeal submitted.");
      await load("refresh");
    } catch (appealError) {
      setError(appealError instanceof Error ? appealError.message : "Verification appeal could not be submitted.");
    } finally {
      setBusy("");
    }
  }

  if (loading && !state) {
    return (
      <View style={styles.center}>
        <ActivityIndicator color={colors.accent} />
        <Text style={styles.centerText}>Loading Verification Center</Text>
      </View>
    );
  }

  return (
    <ScrollView
      style={styles.root}
      contentContainerStyle={styles.content}
      refreshControl={<RefreshControl refreshing={refreshing} onRefresh={() => load("refresh").catch(() => undefined)} tintColor={colors.accent} />}
    >
      <View style={styles.header}>
        <Text style={styles.eyebrow}>PulseSoc trust passport</Text>
        <Text style={styles.title}>Verification Center</Text>
        <Text style={styles.subtitle}>
          {offline
            ? "This is the last status we saved. Pull down to check for a newer one."
            : "Your checkmark, your identity, and any documents you send are reviewed by people at PulseSoc. This screen shows you where that review has got to."}
        </Text>
      </View>

      {error ? <Text style={styles.error}>{error}</Text> : null}
      {notice ? <Text style={styles.notice}>{notice}</Text> : null}

      <Panel>
        <View style={styles.statusRow}>
          <View style={styles.statusOrb}>
            <Text style={styles.statusScore}>{state?.score || 0}</Text>
          </View>
          <View style={styles.statusCopy}>
            <Text style={styles.panelTitle}>{verificationStatusLabel(state?.status)}</Text>
            {/* The derived headline, not the server's `primaryAction` string:
                that field says "Continue Verification" even to someone already
                approved, which is the same defect as the button below it. */}
            <Text style={styles.muted}>{actions.headline}</Text>
            <Text style={styles.muted}>
              {verificationTrackLabel(state?.verificationType || "identity")}
              {state?.requestId ? ` · Request #${state.requestId}, worth quoting if you contact support` : ""}
            </Text>
          </View>
        </View>
      </Panel>

      <Panel>
        <Text style={styles.panelTitle}>Badge preview</Text>
        <View style={styles.profilePreview}>
          <View style={styles.avatarGlow}>
            <Text style={styles.avatarText}>{initials(state?.profilePreview.displayName)}</Text>
          </View>
          <View style={styles.profileCopy}>
            <Text style={styles.profileName}>{state?.profilePreview.displayName || "PulseSoc member"} {state?.profilePreview.verifiedBadge ? "✓" : ""}</Text>
            <Text style={styles.muted}>@{state?.profilePreview.username || "pending-handle"}</Text>
            <View style={styles.badgeRow}>
              <Badge label={state?.profilePreview.verifiedBadge ? "Verified" : "Verification pending"} active={Boolean(state?.profilePreview.verifiedBadge)} />
              <Badge label={state?.premiumBadges.premiumActive ? "Premium" : "Free"} active={Boolean(state?.premiumBadges.premiumActive)} />
              <Badge label={state?.premiumBadges.founderActive ? `Founder #${state?.premiumBadges.founderNumber || ""}`.trim() : "Founder locked"} active={Boolean(state?.premiumBadges.founderActive)} />
            </View>
          </View>
        </View>
      </Panel>

      <Panel>
        <Text style={styles.panelTitle}>Verification checklist</Text>
        {(state?.checklist || []).map((item) => (
          <View key={item.key} style={styles.checkRow}>
            <Text style={[styles.checkMark, item.complete ? styles.checkDone : undefined]}>{item.complete ? "✓" : "•"}</Text>
            <View style={styles.checkCopy}>
              <Text style={styles.checkTitle}>{item.label}</Text>
              <Text style={styles.muted}>{item.detail}</Text>
            </View>
          </View>
        ))}
      </Panel>

      <Panel>
        <Text style={styles.panelTitle}>Choose what to verify</Text>
        <Text style={styles.muted}>Chosen: {verificationTrackLabel(selectedTrack)}</Text>
        <View style={styles.choiceRow}>
          {(state?.tracks || []).map((track) => (
            <Pressable
              key={track.key}
              accessibilityRole="button"
              accessibilityState={{ disabled: !actions.canChooseTrack, selected: selectedTrack === track.key }}
              disabled={!actions.canChooseTrack}
              style={[styles.choice, selectedTrack === track.key && styles.choiceActive, !actions.canChooseTrack && styles.disabled]}
              onPress={() => setSelectedTrack(track.key)}
            >
              <Text style={[styles.choiceTitle, selectedTrack === track.key && styles.choiceTitleActive]}>{track.label}</Text>
              <Text style={styles.choiceDetail}>{track.detail}</Text>
            </Pressable>
          ))}
        </View>
        {/* No action here means there is genuinely nothing to press, so the
            panel says why rather than offering a button that would fail. */}
        {actions.path.length ? (
          actions.path.map((action) => (
            <ActionButton
              key={action.key}
              label={busy === "request" ? "Sending..." : action.label}
              variant={action.variant}
              disabled={Boolean(busy)}
              onPress={submitRequest}
            />
          ))
        ) : (
          <Text style={styles.muted}>You cannot change or resend this while your request is with a reviewer.</Text>
        )}
      </Panel>

      <Panel>
        <Text style={styles.panelTitle}>Send a document</Text>
        <Text style={styles.muted}>Anything you send here is kept private and seen only by the review team. This app never opens, keeps, or checks your documents itself.</Text>
        {actions.document.length ? (
          <>
            <View style={styles.choiceRow}>
              {documentTypes.map((type) => (
                <Pressable
                  key={type}
                  accessibilityRole="button"
                  accessibilityState={{ selected: documentType === type }}
                  style={[styles.docPill, documentType === type && styles.docPillActive]}
                  onPress={() => setDocumentType(type)}
                >
                  <Text style={[styles.docPillText, documentType === type && styles.docPillTextActive]}>{documentTypeLabel(type)}</Text>
                </Pressable>
              ))}
            </View>
            {actions.document.map((action) => (
              <ActionButton key={action.key} label={busy === "document" ? "Sending..." : action.label} variant={action.variant} disabled={Boolean(busy)} onPress={uploadDocument} />
            ))}
          </>
        ) : (
          <Text style={styles.muted}>{documentUnavailableReason(state?.status)}</Text>
        )}
      </Panel>

      <Panel>
        <Text style={styles.panelTitle}>Ask for another look</Text>
        {actions.appeal.length ? (
          <>
            <Text style={styles.muted}>Tell the review team what they may have missed. A person reads this.</Text>
            <TextInput
              accessibilityLabel="What you would like the review team to reconsider"
              multiline
              onChangeText={setAppealNote}
              placeholder="What would you like them to look at again?"
              placeholderTextColor={colors.muted}
              style={[styles.input, styles.textArea]}
              value={appealNote}
            />
            {actions.appeal.map((action) => (
              <ActionButton key={action.key} label={busy === "appeal" ? "Sending..." : action.label} variant={action.variant} disabled={Boolean(busy)} onPress={submitAppeal} />
            ))}
          </>
        ) : (
          <Text style={styles.muted}>{appealUnavailableReason(state?.status)}</Text>
        )}
      </Panel>

      <Panel>
        <Text style={styles.panelTitle}>Recommendations</Text>
        {(state?.recommendations?.length ? state.recommendations : ["Review verification status periodically."]).map((item, index) => (
          <Text key={`${item}-${index}`} style={styles.recommendation}>- {item}</Text>
        ))}
      </Panel>
    </ScrollView>
  );
}

function ActionButton({ label, onPress, disabled, variant = "primary" }: { label: string; onPress: () => void; disabled?: boolean; variant?: "primary" | "secondary" }) {
  return (
    <Pressable accessibilityRole="button" disabled={disabled} style={[styles.actionButton, variant === "secondary" && styles.secondaryButton, disabled && styles.disabled]} onPress={onPress}>
      <Text style={[styles.actionText, variant === "secondary" && styles.secondaryText]}>{label}</Text>
    </Pressable>
  );
}

function Badge({ label, active }: { label: string; active: boolean }) {
  return <Text style={[styles.badge, active && styles.badgeActive]}>{label}</Text>;
}

function initials(name?: string) {
  return String(name || "PS")
    .split(/\s+/)
    .map((part) => part[0])
    .join("")
    .slice(0, 2)
    .toUpperCase();
}

function normalizeRouteTrack(track?: string): VerificationTrackKey {
  if (track === "business" || track === "blue_check" || track === "government_id") return track;
  return "identity";
}

function documentTypeLabel(type: (typeof documentTypes)[number]) {
  if (type === "government_id") return "Passport or ID";
  if (type === "business_document") return "Business paperwork";
  return "Photo of you";
}

/**
 * Why the document panel has no button. Each reason names the state the person
 * is actually in, so the absence of a control reads as an explanation rather
 * than as something broken.
 */
function documentUnavailableReason(status?: string) {
  if (status === "approved") return "You are verified, so there is nothing more to send.";
  if (status === "rejected" || status === "suspended") return "Sending more documents will not reopen this. Ask for another look instead.";
  return "Send a request first. Once it exists, you can attach documents to it.";
}

function appealUnavailableReason(status?: string) {
  if (status === "appealed") return "Your appeal is already with the review team. They will come back to you.";
  if (status === "approved") return "You are verified, so there is nothing to appeal.";
  if (status === "not_started" || status === "draft") return "There is no decision to appeal yet.";
  return "Your request has not been decided yet, so there is nothing to appeal.";
}

const styles = createThemedStyles(() => ({
  actionButton: {
    alignItems: "center",
    backgroundColor: colors.accent,
    borderRadius: 8,
    justifyContent: "center",
    minHeight: 44,
    paddingHorizontal: 12
  },
  actionText: {
    color: "#08110f",
    fontWeight: "900"
  },
  avatarGlow: {
    alignItems: "center",
    backgroundColor: "rgba(37,208,167,0.16)",
    borderColor: colors.accent,
    borderRadius: 44,
    borderWidth: 1,
    height: 72,
    justifyContent: "center",
    shadowColor: colors.accent,
    shadowOpacity: 0.25,
    shadowRadius: 16,
    width: 72
  },
  avatarText: {
    color: colors.text,
    fontSize: 24,
    fontWeight: "900"
  },
  badge: {
    borderColor: colors.border,
    borderRadius: 999,
    borderWidth: StyleSheet.hairlineWidth,
    color: colors.muted,
    fontSize: 12,
    fontWeight: "900",
    paddingHorizontal: 9,
    paddingVertical: 5
  },
  badgeActive: {
    backgroundColor: "rgba(37,208,167,0.14)",
    borderColor: colors.accent,
    color: colors.accent
  },
  badgeRow: {
    flexDirection: "row",
    flexWrap: "wrap",
    gap: 8
  },
  center: {
    alignItems: "center",
    backgroundColor: "transparent",
    flex: 1,
    gap: 12,
    justifyContent: "center"
  },
  centerText: {
    color: colors.text,
    fontWeight: "800"
  },
  checkCopy: {
    flex: 1,
    gap: 2
  },
  checkDone: {
    color: colors.accent
  },
  checkMark: {
    color: colors.muted,
    fontSize: 20,
    fontWeight: "900",
    width: 24
  },
  checkRow: {
    alignItems: "flex-start",
    flexDirection: "row",
    gap: 8
  },
  checkTitle: {
    color: colors.text,
    fontWeight: "900"
  },
  choice: {
    borderColor: colors.border,
    borderRadius: 8,
    borderWidth: StyleSheet.hairlineWidth,
    flexBasis: "48%",
    flexGrow: 1,
    gap: 5,
    minHeight: 96,
    padding: 10
  },
  choiceActive: {
    backgroundColor: "rgba(37,208,167,0.12)",
    borderColor: colors.accent
  },
  choiceDetail: {
    color: colors.muted,
    fontSize: 12,
    lineHeight: 17
  },
  choiceRow: {
    flexDirection: "row",
    flexWrap: "wrap",
    gap: 8
  },
  choiceTitle: {
    color: colors.text,
    fontSize: 14,
    fontWeight: "900"
  },
  choiceTitleActive: {
    color: colors.accent
  },
  content: {
    gap: 14,
    padding: 16
  },
  disabled: {
    opacity: 0.55
  },
  docPill: {
    borderColor: colors.border,
    borderRadius: 999,
    borderWidth: StyleSheet.hairlineWidth,
    minHeight: 34,
    justifyContent: "center",
    paddingHorizontal: 11
  },
  docPillActive: {
    backgroundColor: "rgba(79,140,255,0.14)",
    borderColor: colors.accentStrong
  },
  docPillText: {
    color: colors.muted,
    fontSize: 13,
    fontWeight: "800"
  },
  docPillTextActive: {
    color: colors.text
  },
  error: {
    backgroundColor: "rgba(255,107,107,0.12)",
    borderColor: colors.danger,
    borderRadius: 8,
    borderWidth: StyleSheet.hairlineWidth,
    color: colors.danger,
    padding: 10
  },
  eyebrow: {
    color: colors.accent,
    fontSize: 12,
    fontWeight: "900",
    textTransform: "uppercase"
  },
  header: {
    backgroundColor: colors.surface,
    borderColor: colors.border,
    borderRadius: 8,
    borderWidth: StyleSheet.hairlineWidth,
    gap: 6,
    padding: 16
  },
  input: {
    backgroundColor: colors.surfaceRaised,
    borderColor: colors.border,
    borderRadius: 8,
    borderWidth: StyleSheet.hairlineWidth,
    color: colors.text,
    minHeight: 44,
    paddingHorizontal: 12,
    paddingVertical: 10
  },
  muted: {
    color: colors.muted,
    fontSize: 13,
    lineHeight: 19
  },
  notice: {
    backgroundColor: "rgba(37,208,167,0.1)",
    borderColor: colors.accent,
    borderRadius: 8,
    borderWidth: StyleSheet.hairlineWidth,
    color: colors.accent,
    padding: 10
  },
  panelTitle: {
    color: colors.text,
    fontSize: 17,
    fontWeight: "900"
  },
  profileCopy: {
    flex: 1,
    gap: 6
  },
  profileName: {
    color: colors.text,
    fontSize: 18,
    fontWeight: "900"
  },
  profilePreview: {
    alignItems: "center",
    flexDirection: "row",
    gap: 12
  },
  recommendation: {
    color: colors.text,
    fontSize: 13,
    lineHeight: 20
  },
  root: {
    backgroundColor: "transparent",
    flex: 1
  },
  secondaryButton: {
    backgroundColor: "transparent",
    borderColor: colors.border,
    borderWidth: StyleSheet.hairlineWidth
  },
  secondaryText: {
    color: colors.text
  },
  statusCopy: {
    flex: 1,
    gap: 4
  },
  statusOrb: {
    alignItems: "center",
    aspectRatio: 1,
    backgroundColor: "rgba(79,140,255,0.12)",
    borderColor: colors.accentStrong,
    borderRadius: 8,
    borderWidth: StyleSheet.hairlineWidth,
    justifyContent: "center",
    width: 74
  },
  statusRow: {
    alignItems: "center",
    flexDirection: "row",
    gap: 14
  },
  statusScore: {
    color: colors.accent,
    fontSize: 24,
    fontWeight: "900"
  },
  subtitle: {
    color: colors.muted,
    fontSize: 14,
    lineHeight: 20
  },
  textArea: {
    minHeight: 92,
    textAlignVertical: "top"
  },
  title: {
    color: colors.text,
    fontSize: 28,
    fontWeight: "900"
  }
}));
