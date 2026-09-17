import { useCallback, useEffect, useMemo, useState } from "react";
import {
  ActivityIndicator,
  Modal,
  Pressable,
  ScrollView,
  StyleSheet,
  Text,
  TextInput,
  View
} from "react-native";

import {
  MusicAuditEntry,
  MusicCapabilities,
  MusicLifecycleState,
  MusicTrackImpact,
  cancelMusicTrackPurge,
  describeMusicAuthorityError,
  fetchMusicTrackAudit,
  fetchMusicTrackImpact,
  purgeMusicTrack,
  requestMusicStepUp,
  restoreMusicTrack,
  scheduleMusicTrackPurge,
  takedownMusicTrack
} from "../api/musicAuthority";
import { useTranslation } from "../i18n";
import { colors } from "../theme/colors";

/**
 * The platform owner's removal controls for one track.
 *
 * Contains no audio of any kind — no `expo-av`, no session configuration, no
 * playback. That is deliberate and worth stating: this file sits next to the
 * music surfaces and the obvious next feature ("preview before removing")
 * would put a second audio owner into a screen that already has one.
 *
 * Three things here are load-bearing beyond the buttons:
 *
 * **Every action is offered from the state it is legal in, and no other.** The
 * server's transition table is the authority and refuses the rest with 409;
 * mirroring it here is purely so the owner is not offered a button that always
 * fails. Where the two disagree the server wins and the refusal is shown.
 *
 * **The CDN caveat is displayed, not buried.** A takedown stops PulseSoc
 * serving the file, but the bucket is public and the objects were marked
 * immutable for a year, so anything already cached keeps playing until a purge.
 * An owner handling a copyright complaint needs to know that before they choose
 * takedown over quarantine, and the honest place to say it is next to the
 * buttons.
 *
 * **Impact is shown before the decision, not after.** The reference count comes
 * from the server and covers Reels, posts and statuses; a takedown that would
 * silence four hundred videos should say so while it can still be reconsidered.
 */

type Props = {
  trackId: string;
  trackTitle: string;
  capabilities: MusicCapabilities;
  visible: boolean;
  onClose: () => void;
  /** Fired after any successful transition so the list can refresh. */
  onChanged?: () => void;
};

const DEFAULT_REASON = "OWNER_DECISION";

/**
 * Reason codes are shown as the codes themselves, lightly spaced — deliberately
 * untranslated. They are a controlled vocabulary that is written verbatim into
 * the audit trail, and an owner reading that trail has to be able to match what
 * they picked against what was recorded. A localized label would make the two
 * disagree in exactly the situation the trail exists for.
 */
function reasonLabel(code: string) {
  return String(code || "").replace(/_/g, " ");
}

export function MusicOwnerPanel({ trackId, trackTitle, capabilities, visible, onClose, onChanged }: Props) {
  const { t } = useTranslation();
  const [impact, setImpact] = useState<MusicTrackImpact | null>(null);
  const [audit, setAudit] = useState<MusicAuditEntry[]>([]);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [reasonCode, setReasonCode] = useState(DEFAULT_REASON);
  const [note, setNote] = useState("");
  const [password, setPassword] = useState("");
  const [confirmId, setConfirmId] = useState("");
  const [stepUpUntil, setStepUpUntil] = useState(0);

  const permissions = capabilities.permissions;
  const state: MusicLifecycleState = impact?.state || "ACTIVE";

  const reasonCodes = useMemo(() => {
    const fromServer = impact?.reasonCodes?.length ? impact.reasonCodes : capabilities.reasonCodes;
    return fromServer.length ? fromServer : [DEFAULT_REASON];
  }, [capabilities.reasonCodes, impact?.reasonCodes]);

  const reload = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      const [nextImpact, nextAudit] = await Promise.all([
        fetchMusicTrackImpact(trackId),
        fetchMusicTrackAudit(trackId).catch(() => [] as MusicAuditEntry[])
      ]);
      setImpact(nextImpact);
      setAudit(nextAudit);
    } catch (failure) {
      // The panel shows an error *instead of* an impact, never beside one: a
      // reference count left on screen from a previous load would be read as
      // current, and "0 posts affected" next to a failed refresh is the most
      // dangerous thing this screen could say.
      setImpact(null);
      setAudit([]);
      setError(describeMusicAuthorityError(failure));
    } finally {
      setLoading(false);
    }
  }, [trackId]);

  useEffect(() => {
    if (!visible) return;
    setNotice("");
    setPassword("");
    setConfirmId("");
    reload().catch(() => undefined);
  }, [reload, visible]);

  const run = useCallback(
    async (label: string, action: () => Promise<{ changed: boolean }>) => {
      setBusy(true);
      setError("");
      setNotice("");
      try {
        const result = await action();
        // A repeated action is not a failure. The server answers 200 with
        // `changed: false` so a retried request does not report an error for
        // work that already succeeded, and saying "nothing changed" is the only
        // honest rendering of that.
        setNotice(result.changed ? t("discovery:music.ownerApplied", { action: label }) : t("discovery:music.ownerNoChange"));
        await reload();
        onChanged?.();
      } catch (failure) {
        setError(describeMusicAuthorityError(failure));
      } finally {
        setBusy(false);
      }
    },
    [onChanged, reload, t]
  );

  const mutation = useMemo(
    () => ({
      reasonCode,
      reasonNote: note.trim(),
      // Sent on every mutation so a decision made against this screen is
      // refused if someone else moved the track in the meantime. Without it
      // two moderators working from stale screens silently overwrite each
      // other and only the audit trail shows it happened.
      expectedState: state
    }),
    [note, reasonCode, state]
  );

  const stepUpValid = stepUpUntil > Date.now();
  const canTakedown = permissions["music.takedown"] && (state === "ACTIVE" || state === "TAKEN_DOWN");
  const canQuarantine =
    permissions["music.takedown"] && (state === "ACTIVE" || state === "TAKEN_DOWN" || state === "QUARANTINED");
  const canRestore = permissions["music.restore"] && state !== "PURGED";
  const canSchedulePurge =
    permissions["music.purge"] && (state === "TAKEN_DOWN" || state === "QUARANTINED" || state === "PURGE_PENDING");
  const canCancelPurge = permissions["music.purge"] && (state === "PURGE_PENDING" || state === "TAKEN_DOWN");
  const canPurge = permissions["music.purge"] && state === "PURGE_PENDING" && !impact?.legalHold;

  return (
    <Modal visible={visible} animationType="slide" transparent={false} onRequestClose={onClose}>
      <View style={styles.root}>
        <View style={styles.headerRow}>
          <View style={styles.headerCopy}>
            <Text style={styles.kicker}>{t("discovery:music.ownerSectionTitle")}</Text>
            <Text style={styles.title} numberOfLines={2}>{trackTitle}</Text>
          </View>
          <Pressable accessibilityRole="button" accessibilityLabel={t("discovery:music.ownerClose")} style={styles.closeButton} onPress={onClose}>
            <Text style={styles.closeText}>{t("discovery:music.ownerClose")}</Text>
          </Pressable>
        </View>

        {loading ? (
          <View style={styles.center}>
            <ActivityIndicator color={colors.accent} />
            <Text style={styles.centerText}>{t("discovery:music.ownerLoading")}</Text>
          </View>
        ) : error && !impact ? (
          <View style={styles.center}>
            <Text style={styles.errorText}>{error}</Text>
            <Pressable accessibilityRole="button" style={styles.button} onPress={() => reload().catch(() => undefined)}>
              <Text style={styles.buttonText}>{t("discovery:music.ownerRetry")}</Text>
            </Pressable>
          </View>
        ) : (
          <ScrollView contentContainerStyle={styles.content} keyboardShouldPersistTaps="handled">
            <View style={styles.card}>
              <Text style={styles.cardTitle}>{t("discovery:music.ownerImpactTitle")}</Text>
              <Text style={styles.stateBadge}>{state.replace(/_/g, " ")}</Text>
              <Text style={styles.body}>
                {t("discovery:music.ownerImpactUses", { count: impact?.references.total || 0 })}
              </Text>
              <Text style={styles.body}>
                {t("discovery:music.ownerImpactReports", { count: impact?.openReports || 0 })}
              </Text>
              {impact?.cachedCopiesRemainUntilPurge ? (
                <Text style={styles.caveat}>{t("discovery:music.ownerCdnCaveat")}</Text>
              ) : null}
              {impact?.legalHold ? <Text style={styles.warning}>{t("discovery:music.ownerLegalHold")}</Text> : null}
            </View>

            <View style={styles.card}>
              <Text style={styles.cardTitle}>{t("discovery:music.ownerReason")}</Text>
              <View style={styles.chipRow}>
                {reasonCodes.map((code) => (
                  <Pressable
                    key={code}
                    accessibilityRole="button"
                    accessibilityState={{ selected: reasonCode === code }}
                    style={[styles.chip, reasonCode === code && styles.chipActive]}
                    onPress={() => setReasonCode(code)}
                  >
                    <Text style={[styles.chipText, reasonCode === code && styles.chipTextActive]}>{reasonLabel(code)}</Text>
                  </Pressable>
                ))}
              </View>
              <TextInput
                style={styles.input}
                value={note}
                onChangeText={setNote}
                multiline
                placeholder={t("discovery:music.ownerNotePlaceholder")}
                placeholderTextColor={colors.muted}
                accessibilityLabel={t("discovery:music.ownerNote")}
              />
            </View>

            {notice ? <Text style={styles.notice}>{notice}</Text> : null}
            {error ? <Text style={styles.errorText}>{error}</Text> : null}

            <View style={styles.card}>
              <Text style={styles.cardTitle}>{t("discovery:music.ownerActionsTitle")}</Text>
              <OwnerAction
                label={t("discovery:music.ownerTakedown")}
                enabled={canTakedown && !busy}
                onPress={() =>
                  run(t("discovery:music.ownerTakedown"), () => takedownMusicTrack(trackId, mutation))
                }
              />
              <OwnerAction
                label={t("discovery:music.ownerQuarantine")}
                hint={t("discovery:music.ownerQuarantineHint")}
                enabled={canQuarantine && !busy}
                onPress={() =>
                  run(t("discovery:music.ownerQuarantine"), () =>
                    takedownMusicTrack(trackId, { ...mutation, quarantine: true })
                  )
                }
              />
              <OwnerAction
                label={t("discovery:music.ownerRestore")}
                enabled={canRestore && !busy}
                onPress={() => run(t("discovery:music.ownerRestore"), () => restoreMusicTrack(trackId, mutation))}
              />
              <OwnerAction
                label={t("discovery:music.ownerSchedulePurge")}
                enabled={canSchedulePurge && !busy}
                onPress={() =>
                  run(t("discovery:music.ownerSchedulePurge"), () => scheduleMusicTrackPurge(trackId, mutation))
                }
              />
              <OwnerAction
                label={t("discovery:music.ownerCancelPurge")}
                enabled={canCancelPurge && !busy}
                onPress={() =>
                  run(t("discovery:music.ownerCancelPurge"), () => cancelMusicTrackPurge(trackId, mutation))
                }
              />
            </View>

            {permissions["music.purge"] ? (
              <View style={[styles.card, styles.dangerCard]}>
                <Text style={styles.cardTitle}>{t("discovery:music.ownerPurge")}</Text>
                <Text style={styles.body}>{t("discovery:music.ownerPurgeExplainer")}</Text>
                <TextInput
                  style={styles.input}
                  value={password}
                  onChangeText={setPassword}
                  secureTextEntry
                  autoCapitalize="none"
                  placeholder={t("discovery:music.ownerStepUpPlaceholder")}
                  placeholderTextColor={colors.muted}
                  accessibilityLabel={t("discovery:music.ownerStepUpTitle")}
                />
                <OwnerAction
                  label={t("discovery:music.ownerStepUpAction")}
                  enabled={Boolean(password) && !busy}
                  onPress={async () => {
                    setBusy(true);
                    setError("");
                    try {
                      const ttl = await requestMusicStepUp(password);
                      setStepUpUntil(Date.now() + ttl * 1000);
                      // Cleared immediately: the grant is a server-side row now,
                      // and keeping the password in component state past the one
                      // request that needed it buys nothing.
                      setPassword("");
                      setNotice(t("discovery:music.ownerStepUpGranted", { seconds: ttl }));
                    } catch (failure) {
                      setError(describeMusicAuthorityError(failure));
                    } finally {
                      setBusy(false);
                    }
                  }}
                />
                <TextInput
                  style={styles.input}
                  value={confirmId}
                  onChangeText={setConfirmId}
                  autoCapitalize="none"
                  keyboardType="number-pad"
                  placeholder={t("discovery:music.ownerPurgeConfirm", { id: trackId })}
                  placeholderTextColor={colors.muted}
                  accessibilityLabel={t("discovery:music.ownerPurgeConfirm", { id: trackId })}
                />
                <OwnerAction
                  label={t("discovery:music.ownerPurgeAction")}
                  danger
                  // Three independent conditions, none of which is the others'
                  // proxy: the track must already be scheduled, the password
                  // must have been re-entered inside the window, and the id must
                  // be typed. The server enforces all three again; this only
                  // keeps the button from being a trap.
                  enabled={canPurge && stepUpValid && confirmId.trim() === String(trackId) && !busy}
                  onPress={() => run(t("discovery:music.ownerPurgeAction"), () => purgeMusicTrack(trackId, mutation))}
                />
              </View>
            ) : null}

            <View style={styles.card}>
              <Text style={styles.cardTitle}>{t("discovery:music.ownerAuditTitle")}</Text>
              {audit.length ? (
                audit.map((entry) => (
                  <View key={String(entry.actionId)} style={styles.auditRow}>
                    <Text style={styles.auditAction}>
                      {entry.action.replace(/_/g, " ")} → {entry.newState.replace(/_/g, " ")}
                    </Text>
                    <Text style={styles.auditMeta} numberOfLines={2}>
                      {[entry.createdAt, entry.actorRole, entry.reasonCode].filter(Boolean).join(" · ")}
                    </Text>
                    {entry.reasonNote ? <Text style={styles.auditNote}>{entry.reasonNote}</Text> : null}
                  </View>
                ))
              ) : (
                <Text style={styles.body}>{t("discovery:music.ownerAuditEmpty")}</Text>
              )}
            </View>
          </ScrollView>
        )}
      </View>
    </Modal>
  );
}

function OwnerAction({
  label,
  hint,
  enabled,
  danger,
  onPress
}: {
  label: string;
  hint?: string;
  enabled: boolean;
  danger?: boolean;
  onPress: () => void;
}) {
  return (
    <Pressable
      accessibilityRole="button"
      accessibilityLabel={label}
      accessibilityHint={hint}
      accessibilityState={{ disabled: !enabled }}
      disabled={!enabled}
      style={[styles.button, danger && styles.dangerButton, !enabled && styles.buttonDisabled]}
      onPress={onPress}
    >
      <Text style={[styles.buttonText, danger && styles.dangerButtonText, !enabled && styles.buttonTextDisabled]}>
        {label}
      </Text>
      {hint ? <Text style={styles.buttonHint}>{hint}</Text> : null}
    </Pressable>
  );
}

const styles = StyleSheet.create({
  auditAction: { color: colors.text, fontSize: 13, fontWeight: "700" },
  auditMeta: { color: colors.muted, fontSize: 11, marginTop: 2 },
  auditNote: { color: colors.muted, fontSize: 12, marginTop: 4 },
  auditRow: { borderTopColor: colors.border, borderTopWidth: 1, paddingVertical: 8 },
  body: { color: colors.muted, fontSize: 13, marginTop: 4 },
  button: {
    alignItems: "center",
    backgroundColor: colors.surfaceRaised,
    borderColor: colors.border,
    borderRadius: 12,
    borderWidth: 1,
    marginTop: 8,
    paddingHorizontal: 14,
    paddingVertical: 12
  },
  buttonDisabled: { opacity: 0.4 },
  buttonHint: { color: colors.muted, fontSize: 11, marginTop: 2 },
  buttonText: { color: colors.text, fontSize: 14, fontWeight: "700" },
  buttonTextDisabled: { color: colors.muted },
  card: {
    backgroundColor: colors.surface,
    borderColor: colors.border,
    borderRadius: 16,
    borderWidth: 1,
    marginTop: 12,
    padding: 14
  },
  cardTitle: { color: colors.text, fontSize: 15, fontWeight: "800" },
  caveat: { color: colors.warning, fontSize: 12, lineHeight: 17, marginTop: 10 },
  center: { alignItems: "center", flex: 1, gap: 10, justifyContent: "center", padding: 24 },
  centerText: { color: colors.muted, fontSize: 13 },
  chip: {
    backgroundColor: colors.surfaceRaised,
    borderColor: colors.border,
    borderRadius: 999,
    borderWidth: 1,
    paddingHorizontal: 10,
    paddingVertical: 6
  },
  chipActive: { backgroundColor: colors.accent, borderColor: colors.accent },
  chipRow: { flexDirection: "row", flexWrap: "wrap", gap: 6, marginTop: 8 },
  chipText: { color: colors.muted, fontSize: 11, fontWeight: "700", textTransform: "uppercase" },
  chipTextActive: { color: colors.background },
  closeButton: { paddingHorizontal: 10, paddingVertical: 6 },
  closeText: { color: colors.accent, fontSize: 14, fontWeight: "700" },
  content: { padding: 16, paddingBottom: 48 },
  dangerButton: { backgroundColor: colors.danger, borderColor: colors.danger },
  dangerButtonText: { color: colors.text },
  dangerCard: { borderColor: colors.danger },
  errorText: { color: colors.danger, fontSize: 13, marginTop: 10 },
  headerCopy: { flex: 1 },
  headerRow: {
    alignItems: "center",
    borderBottomColor: colors.border,
    borderBottomWidth: 1,
    flexDirection: "row",
    paddingBottom: 12,
    paddingHorizontal: 16,
    paddingTop: 16
  },
  input: {
    backgroundColor: colors.surfaceRaised,
    borderColor: colors.border,
    borderRadius: 12,
    borderWidth: 1,
    color: colors.text,
    fontSize: 14,
    marginTop: 10,
    minHeight: 44,
    paddingHorizontal: 12,
    paddingVertical: 10
  },
  kicker: { color: colors.muted, fontSize: 11, fontWeight: "800", letterSpacing: 1, textTransform: "uppercase" },
  notice: { color: colors.accent, fontSize: 13, marginTop: 10 },
  root: { backgroundColor: colors.background, flex: 1 },
  stateBadge: {
    color: colors.accent,
    fontSize: 12,
    fontWeight: "800",
    letterSpacing: 0.8,
    marginTop: 6,
    textTransform: "uppercase"
  },
  title: { color: colors.text, fontSize: 18, fontWeight: "800" },
  warning: { color: colors.danger, fontSize: 12, fontWeight: "700", marginTop: 10 }
});
