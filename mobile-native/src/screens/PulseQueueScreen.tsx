import { Ionicons } from "@expo/vector-icons";
import { useEffect, useState } from "react";
import { FlatList, Pressable, StyleSheet, Text, View } from "react-native";
import { SafeAreaView } from "react-native-safe-area-context";
import {
  cyclePulseRadioRepeatMode,
  getPulseRadioState,
  moveQueueTrack,
  playQueueTrackAt,
  PulseRadioState,
  removeQueueTrackAt,
  subscribePulseRadio,
  togglePulseRadio,
  togglePulseRadioShuffle
} from "../core/pulseRadio";
import { colors } from "../theme/colors";
import { logiNexus } from "../theme/logiNexus";
import { createThemedStyles } from "../theme/themedStyles";

// "View and manage the queue" surface: reorder (move up/down), remove, and
// tap-to-play any track without leaving the queue. Mirrors the persistent
// radio state exactly — there is no separate queue data source.
export function PulseQueueScreen() {
  const [radio, setRadio] = useState<PulseRadioState>(getPulseRadioState());

  useEffect(() => subscribePulseRadio(setRadio), []);

  const repeatLabel = radio.repeatMode === "one" ? "Repeat one" : radio.repeatMode === "queue" ? "Repeat queue" : "Repeat off";
  const repeatIcon = radio.repeatMode === "one" ? "repeat-outline" : "repeat";

  return (
    <SafeAreaView style={styles.container} edges={["bottom"]} testID="pulse-queue-screen">
      <View style={styles.toolbar}>
        <Pressable
          accessibilityRole="button"
          accessibilityLabel={radio.shuffle ? "Disable shuffle" : "Enable shuffle"}
          testID="pulse-queue-shuffle"
          style={[styles.toolbarButton, radio.shuffle && styles.toolbarButtonActive]}
          onPress={() => togglePulseRadioShuffle()}
        >
          <Ionicons name="shuffle" size={18} color={radio.shuffle ? colors.background : colors.text} />
          <Text style={[styles.toolbarLabel, radio.shuffle && styles.toolbarLabelActive]}>Shuffle</Text>
        </Pressable>
        <Pressable
          accessibilityRole="button"
          accessibilityLabel={repeatLabel}
          testID="pulse-queue-repeat"
          style={[styles.toolbarButton, radio.repeatMode !== "off" && styles.toolbarButtonActive]}
          onPress={() => cyclePulseRadioRepeatMode()}
        >
          <Ionicons name={repeatIcon as keyof typeof Ionicons.glyphMap} size={18} color={radio.repeatMode !== "off" ? colors.background : colors.text} />
          <Text style={[styles.toolbarLabel, radio.repeatMode !== "off" && styles.toolbarLabelActive]}>{repeatLabel}</Text>
        </Pressable>
      </View>

      {radio.queue.length === 0 ? (
        <View style={styles.emptyState}>
          <Text style={styles.emptyTitle}>Queue is empty</Text>
          <Text style={styles.emptyBody}>Play Pulse Radio or a track from Music to build a queue.</Text>
        </View>
      ) : (
        <FlatList
          data={radio.queue}
          keyExtractor={(item, index) => `${item.id}-${index}`}
          contentContainerStyle={styles.listContent}
          renderItem={({ item, index }) => {
            const active = index === radio.queueIndex;
            return (
              <View style={[styles.row, active && styles.rowActive]} testID={`pulse-queue-row-${index}`}>
                <Pressable
                  accessibilityRole="button"
                  accessibilityLabel={`Play ${item.title} by ${item.artist}`}
                  style={styles.rowMain}
                  onPress={() => playQueueTrackAt(index)}
                >
                  <View style={[styles.rowSymbol, active && styles.rowSymbolActive]}>
                    {active ? (
                      <Ionicons name={radio.status === "playing" ? "volume-high" : "pause"} size={16} color={colors.background} />
                    ) : (
                      <Text style={styles.rowIndex}>{index + 1}</Text>
                    )}
                  </View>
                  <View style={styles.rowText}>
                    <Text style={[styles.rowTitle, active && styles.rowTitleActive]} numberOfLines={1}>
                      {item.title}
                    </Text>
                    <Text style={styles.rowArtist} numberOfLines={1}>
                      {item.artist}
                    </Text>
                  </View>
                </Pressable>
                <View style={styles.rowActions}>
                  <Pressable
                    accessibilityRole="button"
                    accessibilityLabel={`Move ${item.title} up`}
                    disabled={index === 0}
                    style={[styles.rowActionButton, index === 0 && styles.rowActionDisabled]}
                    onPress={() => moveQueueTrack(index, index - 1)}
                  >
                    <Ionicons name="chevron-up" size={16} color={index === 0 ? colors.muted : colors.text} />
                  </Pressable>
                  <Pressable
                    accessibilityRole="button"
                    accessibilityLabel={`Move ${item.title} down`}
                    disabled={index === radio.queue.length - 1}
                    style={[styles.rowActionButton, index === radio.queue.length - 1 && styles.rowActionDisabled]}
                    onPress={() => moveQueueTrack(index, index + 1)}
                  >
                    <Ionicons name="chevron-down" size={16} color={index === radio.queue.length - 1 ? colors.muted : colors.text} />
                  </Pressable>
                  <Pressable
                    accessibilityRole="button"
                    accessibilityLabel={`Remove ${item.title} from queue`}
                    style={styles.rowActionButton}
                    onPress={() => removeQueueTrackAt(index)}
                  >
                    <Ionicons name="close" size={16} color={colors.danger} />
                  </Pressable>
                </View>
              </View>
            );
          }}
        />
      )}

      {radio.track ? (
        <View style={styles.nowPlayingBar}>
          <View style={styles.rowText}>
            <Text style={styles.rowTitle} numberOfLines={1}>
              {radio.track.title}
            </Text>
            <Text style={styles.rowArtist} numberOfLines={1}>
              {radio.track.artist}
            </Text>
          </View>
          <Pressable
            accessibilityRole="button"
            accessibilityLabel={radio.status === "playing" ? "Pause" : "Play"}
            testID="pulse-queue-play-pause"
            style={styles.nowPlayingButton}
            onPress={() => togglePulseRadio()}
          >
            <Ionicons name={radio.status === "playing" ? "pause" : "play"} size={20} color={colors.background} />
          </Pressable>
        </View>
      ) : null}
    </SafeAreaView>
  );
}

const styles = createThemedStyles(() => ({
  container: {
    backgroundColor: colors.background,
    flex: 1
  },
  toolbar: {
    flexDirection: "row",
    gap: 10,
    paddingHorizontal: logiNexus.spacing.md,
    paddingTop: 10
  },
  toolbarButton: {
    alignItems: "center",
    borderColor: colors.border,
    borderRadius: logiNexus.radius.capsule,
    borderWidth: 1,
    flexDirection: "row",
    gap: 6,
    paddingHorizontal: 14,
    paddingVertical: 8
  },
  toolbarButtonActive: {
    backgroundColor: colors.accent,
    borderColor: colors.accent
  },
  toolbarLabel: {
    color: colors.text,
    fontSize: 12,
    fontWeight: "700"
  },
  toolbarLabelActive: {
    color: colors.background
  },
  emptyState: {
    alignItems: "center",
    flex: 1,
    justifyContent: "center",
    paddingHorizontal: logiNexus.spacing.lg
  },
  emptyTitle: {
    ...logiNexus.typography.sectionTitle,
    color: colors.text
  },
  emptyBody: {
    color: colors.muted,
    marginTop: 6,
    textAlign: "center"
  },
  listContent: {
    paddingBottom: 96,
    paddingHorizontal: logiNexus.spacing.md,
    paddingTop: 12
  },
  row: {
    alignItems: "center",
    borderRadius: 16,
    flexDirection: "row",
    marginBottom: 8,
    paddingHorizontal: 10,
    paddingVertical: 8
  },
  rowActive: {
    backgroundColor: "rgba(50, 230, 179, 0.12)"
  },
  rowMain: {
    alignItems: "center",
    flex: 1,
    flexDirection: "row",
    gap: 12,
    minWidth: 0
  },
  rowSymbol: {
    alignItems: "center",
    backgroundColor: "rgba(255,255,255,0.06)",
    borderRadius: 16,
    height: 32,
    justifyContent: "center",
    width: 32
  },
  rowSymbolActive: {
    backgroundColor: colors.accent
  },
  rowIndex: {
    color: colors.muted,
    fontSize: 12,
    fontWeight: "700"
  },
  rowText: {
    flex: 1,
    minWidth: 0
  },
  rowTitle: {
    color: colors.text,
    fontSize: 14,
    fontWeight: "700"
  },
  rowTitleActive: {
    color: colors.accent
  },
  rowArtist: {
    color: colors.muted,
    fontSize: 12,
    marginTop: 2
  },
  rowActions: {
    flexDirection: "row",
    gap: 2
  },
  rowActionButton: {
    alignItems: "center",
    height: 30,
    justifyContent: "center",
    width: 30
  },
  rowActionDisabled: {
    opacity: 0.35
  },
  nowPlayingBar: {
    alignItems: "center",
    backgroundColor: "rgba(8, 16, 29, 0.96)",
    borderTopColor: colors.border,
    borderTopWidth: StyleSheet.hairlineWidth,
    bottom: 0,
    flexDirection: "row",
    gap: 12,
    left: 0,
    paddingHorizontal: logiNexus.spacing.md,
    paddingVertical: 12,
    position: "absolute",
    right: 0
  },
  nowPlayingButton: {
    alignItems: "center",
    backgroundColor: colors.accent,
    borderRadius: 22,
    height: 44,
    justifyContent: "center",
    width: 44
  }
}));
