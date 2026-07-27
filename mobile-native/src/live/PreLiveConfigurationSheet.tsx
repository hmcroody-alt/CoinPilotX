import { useEffect, useState } from "react";
import { ActivityIndicator, Modal, Pressable, ScrollView, StyleSheet, Switch, Text, TextInput, View } from "react-native";
import { useSafeAreaInsets } from "react-native-safe-area-context";
import { colors } from "../theme/colors";
import {
  AUDIENCE_OPTIONS,
  emptyLiveStudioDraft,
  LIVE_TYPE_OPTIONS,
  loadLiveStudioDraft,
  LiveStudioDraft
} from "./liveStudioReadiness";

type Props = {
  visible: boolean;
  busy: boolean;
  error?: string;
  onClose: () => void;
  onGoLive: (draft: LiveStudioDraft) => void;
};

/**
 * Compact native pre-live sheet. Slides up over the live camera preview so the
 * creator confirms only the essentials before broadcasting — the camera stays
 * visible behind it. Advanced options live behind an expander so a simple live
 * is a couple of taps. Confirming hands the draft to the parent, which starts
 * the native broadcast and enters the native host surface.
 */
export function PreLiveConfigurationSheet({ visible, busy, error, onClose, onGoLive }: Props) {
  const insets = useSafeAreaInsets();
  const [draft, setDraft] = useState<LiveStudioDraft>(emptyLiveStudioDraft());
  const [showAdvanced, setShowAdvanced] = useState(false);

  useEffect(() => {
    if (!visible) return;
    loadLiveStudioDraft()
      .then(setDraft)
      .catch(() => undefined);
  }, [visible]);

  function update(patch: Partial<LiveStudioDraft>) {
    setDraft((current) => ({ ...current, ...patch }));
  }

  return (
    <Modal visible={visible} animationType="slide" transparent onRequestClose={onClose}>
      <View style={styles.backdrop}>
        <Pressable style={styles.backdropFill} onPress={busy ? undefined : onClose} accessibilityLabel="Dismiss live setup" />
        <View style={[styles.sheet, { paddingBottom: insets.bottom + 16 }]}>
          <View style={styles.handle} />
          <View style={styles.header}>
            <Text style={styles.title}>Go Live</Text>
            <Pressable onPress={busy ? undefined : onClose} accessibilityLabel="Close live setup" style={styles.closeButton}>
              <Text style={styles.closeText}>Close</Text>
            </Pressable>
          </View>

          <ScrollView keyboardShouldPersistTaps="handled" contentContainerStyle={styles.body}>
            <TextInput
              style={styles.input}
              value={draft.title}
              onChangeText={(title) => update({ title })}
              placeholder="Broadcast title"
              placeholderTextColor={colors.muted}
              maxLength={120}
              accessibilityLabel="Broadcast title"
            />

            <Text style={styles.fieldLabel}>Audience</Text>
            <View style={styles.chipWrap}>
              {AUDIENCE_OPTIONS.map((option) => {
                const active = draft.audience === option.key;
                return (
                  <Pressable
                    key={option.key}
                    style={[styles.chip, active && styles.chipActive]}
                    onPress={() => update({ audience: option.key })}
                    accessibilityRole="button"
                    accessibilityState={{ selected: active }}
                    accessibilityLabel={`${option.label}: ${option.helper}`}
                  >
                    <Text style={[styles.chipText, active && styles.chipTextActive]}>{option.label}</Text>
                  </Pressable>
                );
              })}
            </View>

            <View style={styles.toggleRow}>
              <View style={styles.toggleBody}>
                <Text style={styles.toggleLabel}>Allow comments</Text>
                <Text style={styles.toggleHelper}>Viewers can chat during the broadcast.</Text>
              </View>
              <Switch
                value={draft.allowComments}
                onValueChange={(allowComments) => update({ allowComments })}
                trackColor={{ true: colors.accent, false: colors.border }}
                thumbColor={colors.text}
                accessibilityLabel="Allow comments"
              />
            </View>

            <View style={styles.toggleRow}>
              <View style={styles.toggleBody}>
                <Text style={styles.toggleLabel}>Record replay</Text>
                <Text style={styles.toggleHelper}>Save a replay after the broadcast ends.</Text>
              </View>
              <Switch
                value={draft.recordReplay}
                onValueChange={(recordReplay) => update({ recordReplay })}
                trackColor={{ true: colors.accent, false: colors.border }}
                thumbColor={colors.text}
                accessibilityLabel="Record replay"
              />
            </View>

            <Pressable
              style={styles.advancedToggle}
              onPress={() => setShowAdvanced((current) => !current)}
              accessibilityRole="button"
              accessibilityState={{ expanded: showAdvanced }}
              accessibilityLabel="Advanced live settings"
            >
              <Text style={styles.advancedToggleText}>{showAdvanced ? "Hide advanced" : "Advanced"}</Text>
            </Pressable>

            {showAdvanced ? (
              <View style={styles.advanced}>
                <Text style={styles.fieldLabel}>Live type</Text>
                <View style={styles.chipWrap}>
                  {LIVE_TYPE_OPTIONS.map((option) => {
                    const active = draft.liveType === option.key;
                    return (
                      <Pressable
                        key={option.key}
                        style={[styles.chip, active && styles.chipActive]}
                        onPress={() => update({ liveType: option.key })}
                        accessibilityRole="button"
                        accessibilityState={{ selected: active }}
                        accessibilityLabel={`${option.label}: ${option.helper}`}
                      >
                        <Text style={[styles.chipText, active && styles.chipTextActive]}>{option.label}</Text>
                      </Pressable>
                    );
                  })}
                </View>
                <TextInput
                  style={[styles.input, styles.multiline]}
                  value={draft.description}
                  onChangeText={(description) => update({ description })}
                  placeholder="What's this live about? (optional)"
                  placeholderTextColor={colors.muted}
                  maxLength={500}
                  multiline
                  textAlignVertical="top"
                  accessibilityLabel="Broadcast description"
                />
              </View>
            ) : null}

            {error ? <Text style={styles.error}>{error}</Text> : null}
          </ScrollView>

          <Pressable
            style={[styles.goLive, busy && styles.goLiveBusy]}
            onPress={() => onGoLive(draft)}
            disabled={busy}
            accessibilityRole="button"
            accessibilityLabel="Start broadcast"
            testID="pre-live-go-live"
          >
            {busy ? <ActivityIndicator color="#fff" /> : <Text style={styles.goLiveText}>Go Live</Text>}
          </Pressable>
        </View>
      </View>
    </Modal>
  );
}

const styles = StyleSheet.create({
  advanced: {
    gap: 10
  },
  advancedToggle: {
    alignSelf: "flex-start",
    borderColor: colors.border,
    borderRadius: 8,
    borderWidth: 1,
    paddingHorizontal: 12,
    paddingVertical: 8
  },
  advancedToggleText: {
    color: colors.accent,
    fontWeight: "800"
  },
  backdrop: {
    backgroundColor: "rgba(2, 4, 10, 0.55)",
    flex: 1,
    justifyContent: "flex-end"
  },
  backdropFill: {
    flex: 1
  },
  body: {
    gap: 12,
    paddingBottom: 12
  },
  chip: {
    borderColor: colors.border,
    borderRadius: 999,
    borderWidth: 1,
    paddingHorizontal: 14,
    paddingVertical: 8
  },
  chipActive: {
    backgroundColor: colors.accent,
    borderColor: colors.accent
  },
  chipText: {
    color: colors.text,
    fontWeight: "800"
  },
  chipTextActive: {
    color: colors.background
  },
  chipWrap: {
    flexDirection: "row",
    flexWrap: "wrap",
    gap: 8
  },
  closeButton: {
    paddingHorizontal: 8,
    paddingVertical: 4
  },
  closeText: {
    color: colors.muted,
    fontWeight: "800"
  },
  error: {
    color: colors.danger,
    fontWeight: "800"
  },
  fieldLabel: {
    color: colors.muted,
    fontWeight: "800",
    textTransform: "uppercase"
  },
  goLive: {
    alignItems: "center",
    backgroundColor: colors.danger,
    borderRadius: 16,
    marginTop: 12,
    paddingVertical: 16
  },
  goLiveBusy: {
    opacity: 0.7
  },
  goLiveText: {
    color: "#fff",
    fontSize: 16,
    fontWeight: "900"
  },
  handle: {
    alignSelf: "center",
    backgroundColor: colors.border,
    borderRadius: 3,
    height: 5,
    marginBottom: 12,
    width: 44
  },
  header: {
    alignItems: "center",
    flexDirection: "row",
    justifyContent: "space-between",
    marginBottom: 12
  },
  input: {
    backgroundColor: colors.surface,
    borderColor: colors.border,
    borderRadius: 10,
    borderWidth: 1,
    color: colors.text,
    paddingHorizontal: 12,
    paddingVertical: 12
  },
  multiline: {
    minHeight: 80
  },
  sheet: {
    backgroundColor: colors.background,
    borderTopLeftRadius: 22,
    borderTopRightRadius: 22,
    maxHeight: "82%",
    paddingHorizontal: 18,
    paddingTop: 12
  },
  title: {
    color: colors.text,
    fontSize: 20,
    fontWeight: "900"
  },
  toggleBody: {
    flex: 1,
    gap: 2
  },
  toggleHelper: {
    color: colors.muted,
    fontSize: 12
  },
  toggleLabel: {
    color: colors.text,
    fontWeight: "900"
  },
  toggleRow: {
    alignItems: "center",
    borderColor: colors.border,
    borderRadius: 12,
    borderWidth: 1,
    flexDirection: "row",
    gap: 12,
    paddingHorizontal: 12,
    paddingVertical: 12
  }
});
