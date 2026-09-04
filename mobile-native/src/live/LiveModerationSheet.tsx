/**
 * Stage 19 — the host's moderation surface.
 *
 * This sheet renders the options that `moderationOptionsFor` returns and adds
 * nothing to them. That is the whole design. A screen that assembles its own
 * menu is a screen that can offer a button the server will refuse, and a user
 * reads a refused button as the feature being broken rather than as a rule being
 * enforced. So the permission questions — may this actor moderate, may anyone
 * moderate the host, is the target even on stage — are answered in the pure
 * module and this component only draws the answer.
 *
 * Two things here are protections made visible rather than decoration:
 *
 *   1. The unmute action is labelled "ask to unmute", never "unmute". The host
 *      cannot open a guest's microphone (see `liveMediaOwnership`), and the
 *      wording has to say so, because a host who believes they flipped a switch
 *      will report the guest's silence as a bug and press it again.
 *
 *   2. Removal is confirmed inline, in this sheet, rather than fired on tap.
 *      `destructive` comes from the option itself, so a future destructive
 *      action gets the confirmation automatically instead of relying on whoever
 *      adds it to remember.
 *
 * No network here either. The caller performs the action; this component reports
 * which command the host chose.
 */

import React, { useCallback, useState } from "react";
import { Image, Pressable, Text, View } from "react-native";
import { Ionicons } from "@expo/vector-icons";
import { useTranslation } from "../i18n";
import { colors } from "../theme/colors";
import { createThemedStyles } from "../theme/themedStyles";
import { LiveBottomSheet } from "./liveHostUi";
import type { LiveStageParticipant } from "./liveParticipantRegistry";
import { moderationOptionsFor, type MediaActor, type MediaCommand, type ModerationOption } from "./liveMediaOwnership";

const ICONS: Record<MediaCommand, keyof typeof Ionicons.glyphMap> = {
  mute: "mic-off-outline",
  unmute: "mic-outline",
  remove: "exit-outline"
};

export type LiveModerationSheetProps = {
  visible: boolean;
  onClose: () => void;
  /** The person tapping. Their permissions decide what the sheet contains. */
  actor: MediaActor;
  /** The person being managed. Null closes the sheet's content down to nothing. */
  target: LiveStageParticipant | null;
  /** Perform the chosen command. The sheet closes once this is called. */
  onCommand: (command: MediaCommand, target: LiveStageParticipant) => void;
};

export function LiveModerationSheet({ visible, onClose, actor, target, onCommand }: LiveModerationSheetProps) {
  const { t } = useTranslation();
  // Which destructive option is awaiting a second tap. Held per-command rather
  // than as a boolean so the confirmation cannot bleed onto a different action.
  const [confirming, setConfirming] = useState<MediaCommand | null>(null);

  const close = useCallback(() => {
    setConfirming(null);
    onClose();
  }, [onClose]);

  const choose = useCallback(
    (option: ModerationOption) => {
      if (!target) return;
      if (option.destructive && confirming !== option.command) {
        setConfirming(option.command);
        return;
      }
      setConfirming(null);
      onCommand(option.command, target);
      onClose();
    },
    [confirming, onClose, onCommand, target]
  );

  const options = target ? moderationOptionsFor(actor, target) : [];
  const name = target?.displayName || "";

  return (
    <LiveBottomSheet
      visible={visible}
      onClose={close}
      title={t("extended:live.moderation.title", { name })}
      maxHeightRatio={0.5}
    >
      {target ? (
        <View style={styles.identity}>
          {target.avatarUrl ? (
            <Image source={{ uri: target.avatarUrl }} style={styles.avatar} />
          ) : (
            <View style={[styles.avatar, styles.avatarFallback]}>
              <Text style={styles.avatarInitial}>{(name || "?").slice(0, 1).toUpperCase()}</Text>
            </View>
          )}
          <View style={styles.identityText}>
            <Text style={styles.identityName} numberOfLines={1}>
              {name}
            </Text>
            <Text style={styles.identityRole} numberOfLines={1}>
              {target.roleLabel}
            </Text>
          </View>
        </View>
      ) : null}

      {options.length === 0 ? (
        <Text style={styles.empty}>{t("extended:live.moderation.noActions")}</Text>
      ) : null}

      {options.map((option) => {
        const pending = option.destructive && confirming === option.command;
        const tone = option.destructive ? colors.danger : colors.text;
        return (
          <View key={`${option.command}-${option.kind}`}>
            <Pressable
              style={({ pressed }) => [styles.row, pressed && styles.rowPressed, pending && styles.rowPending]}
              onPress={() => choose(option)}
              accessibilityRole="button"
              accessibilityLabel={t(option.labelKey)}
            >
              <View style={[styles.rowIcon, { borderColor: tone }]}>
                <Ionicons name={ICONS[option.command]} size={20} color={tone} />
              </View>
              <View style={styles.rowText}>
                <Text style={[styles.rowLabel, { color: tone }]} numberOfLines={1}>
                  {pending ? t("extended:live.moderation.confirmRemove") : t(option.labelKey)}
                </Text>
                {/* The host is asking, not switching. Say so under the action. */}
                {option.command === "unmute" ? (
                  <Text style={styles.rowHint} numberOfLines={2}>
                    {t("extended:live.moderation.unmuteHint")}
                  </Text>
                ) : null}
                {pending ? (
                  <Text style={styles.rowHint} numberOfLines={2}>
                    {t("extended:live.moderation.removeConfirm", { name })}
                  </Text>
                ) : null}
              </View>
            </Pressable>
            {pending ? (
              <Pressable style={styles.cancel} onPress={() => setConfirming(null)} accessibilityRole="button">
                <Text style={styles.cancelText}>{t("extended:live.moderation.cancel")}</Text>
              </Pressable>
            ) : null}
          </View>
        );
      })}
    </LiveBottomSheet>
  );
}

const styles = createThemedStyles(() => ({
  identity: { flexDirection: "row", alignItems: "center", gap: 12, paddingBottom: 16 },
  avatar: { width: 44, height: 44, borderRadius: 22, backgroundColor: "rgba(255,255,255,0.08)" },
  avatarFallback: { alignItems: "center", justifyContent: "center" },
  avatarInitial: { color: colors.text, fontSize: 18, fontWeight: "700" },
  identityText: { flex: 1 },
  identityName: { color: colors.text, fontSize: 16, fontWeight: "700" },
  identityRole: { color: colors.muted, fontSize: 12, marginTop: 2 },
  empty: { color: colors.muted, fontSize: 14, paddingVertical: 12 },
  row: {
    flexDirection: "row",
    alignItems: "center",
    gap: 14,
    paddingVertical: 14,
    paddingHorizontal: 12,
    borderRadius: 16,
    backgroundColor: "rgba(255,255,255,0.05)",
    borderWidth: 1,
    borderColor: "rgba(255,255,255,0.08)",
    marginBottom: 10
  },
  rowPressed: { opacity: 0.7 },
  rowPending: { borderColor: colors.danger, backgroundColor: colors.dangerSoft },
  rowIcon: {
    width: 40,
    height: 40,
    borderRadius: 20,
    borderWidth: 1,
    alignItems: "center",
    justifyContent: "center"
  },
  rowText: { flex: 1 },
  rowLabel: { fontSize: 15, fontWeight: "600" },
  rowHint: { color: colors.muted, fontSize: 12, marginTop: 4 },
  cancel: { alignSelf: "flex-start", paddingHorizontal: 12, paddingBottom: 14 },
  cancelText: { color: colors.muted, fontSize: 13, fontWeight: "600" }
}));
