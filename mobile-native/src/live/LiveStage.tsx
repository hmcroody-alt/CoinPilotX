import { memo, useMemo } from "react";
import { Image, Pressable, StyleSheet, Text, View } from "react-native";
import { Ionicons } from "@expo/vector-icons";
import { LinearGradient } from "expo-linear-gradient";
import { colors } from "../theme/colors";
import { createThemedStyles } from "../theme/themedStyles";
import { useTranslation } from "../i18n";
import type { LiveStageParticipant } from "./liveParticipantRegistry";
import { planStageLayout, type StageLayout, type StageTile } from "./liveStageLayout";
import { RtcVideoView } from "./RtcVideoView";

/**
 * The visible stage of a PulseSoc Live.
 *
 * This component renders the arrangement that `planStageLayout` decided and
 * adds nothing of its own to that decision. That separation is the point: the
 * rules that keep a six-person Live reading as a broadcast rather than a
 * conference call — host largest, host first, tiles never move because someone
 * spoke — are enforced in a pure module with tests, and this file is only
 * allowed to draw the result.
 *
 * Three consequences are deliberate and load-bearing:
 *
 *  1. Tile order comes from `layout.tiles` and nothing else. There is no sort,
 *     no filter, and no "put the speaker first" here. A tile that moved under a
 *     viewer's thumb mid-sentence would be a worse bug than any it solved.
 *  2. The active speaker is a *ring on a tile*, never a position. It is driven
 *     by `participant.speaking`, which the registry sets for at most one person.
 *  3. A tile with no media shows an avatar and a state line — never an empty
 *     black rectangle, which reads to an audience as a broken stream.
 *
 * Visually this is broadcast furniture, not meeting furniture: soft-cornered
 * tiles floating on a dark field, gradient scrims so names stay legible over any
 * frame, and a host tile that is unmistakably the show.
 */

export type LiveStageProps = {
  participants: LiveStageParticipant[];
  /** Rendered above the stage, e.g. the LIVE badge and viewer count. */
  header?: React.ReactNode;
  /** Tapping a tile — used by the host to open moderation for that guest. */
  onSelectParticipant?: (participant: LiveStageParticipant) => void;
  /** Fills the space when nobody is publishing yet. */
  placeholder?: React.ReactNode;
  style?: any;
};

/** Right-hand cap of a tile's speaking ring; kept in one place so it cannot drift. */
const SPEAKING_RING = colors.accent;

function initials(name: string): string {
  const parts = String(name || "")
    .trim()
    .split(/\s+/)
    .filter(Boolean);
  if (parts.length === 0) return "?";
  if (parts.length === 1) return parts[0].slice(0, 2).toUpperCase();
  return (parts[0][0] + parts[parts.length - 1][0]).toUpperCase();
}

/**
 * One person on the stage.
 *
 * Memoised on the fields that actually change what is drawn. Without this, a
 * volume report three times a second would re-render every tile on the stage,
 * and re-rendering an Agora surface is not free.
 */
const StageTileView = memo(
  function StageTileView({
    tile,
    onPress
  }: {
    tile: StageTile;
    onPress?: (participant: LiveStageParticipant) => void;
  }) {
    const { t } = useTranslation();
    const participant = tile.participant;
    const speaking = Boolean(participant.speaking) && !participant.audioMuted;

    const roleLabel = participant.isHost
      ? t("extended:live.stage.hostLabel")
      : participant.role === "cohost"
        ? t("extended:live.stage.cohostLabel")
        : t("extended:live.stage.guestLabel");

    // A guest who is on their way to the stage holds their slot and says so.
    // The alternative — hiding them until their first frame — makes the whole
    // stage jump the moment they connect.
    const pendingLabel =
      participant.phase === "joining"
        ? t("extended:live.guest.state.joining")
        : participant.phase === "preparing"
          ? t("extended:live.guest.state.preparing")
          : "";

    const body = (
      <View
        style={[
          styles.tile,
          tile.featured && styles.tileFeatured,
          speaking && { borderColor: SPEAKING_RING, borderWidth: 2 }
        ]}
      >
        {tile.showsVideo ? (
          <RtcVideoView
            videoTrack={{ uid: participant.isLocal ? 0 : participant.rtcUid, local: participant.isLocal }}
            style={StyleSheet.absoluteFill}
            agoraPresentation="cover"
          />
        ) : (
          <View style={styles.avatarWrap}>
            {participant.avatarUrl ? (
              <Image source={{ uri: participant.avatarUrl }} style={styles.avatarImage} />
            ) : (
              <View style={styles.avatarFallback}>
                <Text style={styles.avatarInitials}>{initials(participant.displayName)}</Text>
              </View>
            )}
            {pendingLabel ? (
              <Text style={styles.pendingLabel} numberOfLines={1}>
                {pendingLabel}
              </Text>
            ) : null}
          </View>
        )}

        {/* Scrim, so a name stays readable over a bright frame. */}
        <LinearGradient
          colors={["transparent", "rgba(3,8,14,0.82)"]}
          style={styles.scrim}
          pointerEvents="none"
        />

        <View style={styles.tileFooter} pointerEvents="none">
          <View style={[styles.roleChip, participant.isHost && styles.roleChipHost]}>
            <Text style={[styles.roleChipText, participant.isHost && styles.roleChipTextHost]} numberOfLines={1}>
              {roleLabel}
            </Text>
          </View>
          <Text style={[styles.tileName, tile.featured && styles.tileNameFeatured]} numberOfLines={1}>
            {participant.displayName}
          </Text>
          {participant.audioMuted ? (
            <Ionicons name="mic-off" size={tile.featured ? 16 : 13} color={colors.danger} />
          ) : speaking ? (
            <Ionicons name="volume-medium" size={tile.featured ? 16 : 13} color={SPEAKING_RING} />
          ) : null}
        </View>
      </View>
    );

    if (!onPress) return body;
    return (
      <Pressable
        onPress={() => onPress(participant)}
        accessibilityRole="button"
        accessibilityLabel={`${participant.displayName}, ${roleLabel}`}
        accessibilityState={{ selected: speaking }}
        style={({ pressed }) => [styles.tilePressable, pressed && styles.tilePressed]}
      >
        {body}
      </Pressable>
    );
  },
  (previous, next) => {
    const a = previous.tile;
    const b = next.tile;
    return (
      a.key === b.key &&
      a.row === b.row &&
      a.column === b.column &&
      a.columnSpan === b.columnSpan &&
      a.heightRatio === b.heightRatio &&
      a.featured === b.featured &&
      a.showsVideo === b.showsVideo &&
      a.participant.speaking === b.participant.speaking &&
      a.participant.audioMuted === b.participant.audioMuted &&
      a.participant.phase === b.participant.phase &&
      a.participant.displayName === b.participant.displayName &&
      a.participant.avatarUrl === b.participant.avatarUrl &&
      previous.onPress === next.onPress
    );
  }
);

/**
 * Group the planned tiles into rows.
 *
 * `planStageLayout` already assigned every tile a row and a column, so this is
 * bucketing, not layout. Doing any arithmetic here would put a second opinion
 * about arrangement in the codebase, which is exactly the thing the pure module
 * exists to prevent.
 */
function rowsOf(layout: StageLayout): StageTile[][] {
  const rows: StageTile[][] = [];
  for (const tile of layout.tiles) {
    if (!rows[tile.row]) rows[tile.row] = [];
    rows[tile.row].push(tile);
  }
  return rows.filter(Boolean);
}

export function LiveStage({ participants, header, onSelectParticipant, placeholder, style }: LiveStageProps) {
  const { t } = useTranslation();
  const layout = useMemo(() => planStageLayout(participants), [participants]);
  const rows = useMemo(() => rowsOf(layout), [layout]);

  if (layout.tiles.length === 0) {
    return <View style={[styles.root, style]}>{placeholder || null}</View>;
  }

  return (
    <View style={[styles.root, style]}>
      {rows.map((row, rowIndex) => (
        <View
          key={`row-${rowIndex}`}
          style={[styles.row, { flex: Math.max(row[0]?.heightRatio || 0, 0.01) * 100 }]}
        >
          {row.map((tile) => (
            <View
              key={tile.key}
              style={{ flex: tile.columnSpan, minWidth: 0 }}
              // The whole point of the pure layout: a tile's width comes from
              // its span, so the host widens as guests arrive rather than
              // shrinking into one cell of a grid.
            >
              <StageTileView tile={tile} onPress={onSelectParticipant} />
            </View>
          ))}
        </View>
      ))}

      {layout.overflow > 0 ? (
        <View style={styles.overflowChip} pointerEvents="none">
          <Ionicons name="people" size={13} color={colors.text} />
          <Text style={styles.overflowText}>+{layout.overflow}</Text>
        </View>
      ) : null}

      {header ? (
        <View style={styles.header} pointerEvents="box-none">
          {header}
        </View>
      ) : null}

      {/* Screen readers get the stage as a sentence, since the visual hierarchy
          that communicates "this is someone's show" is not available to them. */}
      <View
        accessible
        accessibilityRole="summary"
        accessibilityLabel={
          layout.overflow > 0
            ? t("extended:live.stage.full", { count: layout.tiles.length, max: layout.tiles.length + layout.overflow })
            : undefined
        }
        style={styles.a11yProbe}
        pointerEvents="none"
      />
    </View>
  );
}

const styles = createThemedStyles(() => ({
  root: {
    backgroundColor: colors.background,
    flex: 1,
    gap: 4,
    overflow: "hidden"
  },
  row: {
    flexDirection: "row",
    gap: 4
  },
  tilePressable: {
    flex: 1
  },
  tilePressed: {
    opacity: 0.88
  },
  tile: {
    backgroundColor: colors.surface,
    borderColor: "rgba(255,255,255,0.06)",
    borderRadius: 18,
    borderWidth: 1,
    flex: 1,
    overflow: "hidden"
  },
  tileFeatured: {
    borderRadius: 22
  },
  avatarWrap: {
    alignItems: "center",
    flex: 1,
    gap: 10,
    justifyContent: "center"
  },
  avatarImage: {
    borderRadius: 999,
    height: 68,
    width: 68
  },
  avatarFallback: {
    alignItems: "center",
    backgroundColor: colors.surfaceRaised,
    borderColor: "rgba(255,255,255,0.1)",
    borderRadius: 999,
    borderWidth: 1,
    height: 68,
    justifyContent: "center",
    width: 68
  },
  avatarInitials: {
    color: colors.text,
    fontSize: 22,
    fontWeight: "900"
  },
  pendingLabel: {
    color: colors.muted,
    fontSize: 12,
    fontWeight: "700",
    paddingHorizontal: 10,
    textAlign: "center"
  },
  scrim: {
    bottom: 0,
    height: 84,
    left: 0,
    position: "absolute",
    right: 0
  },
  tileFooter: {
    alignItems: "center",
    bottom: 8,
    flexDirection: "row",
    gap: 6,
    left: 10,
    position: "absolute",
    right: 10
  },
  roleChip: {
    backgroundColor: "rgba(6,14,24,0.7)",
    borderColor: "rgba(255,255,255,0.14)",
    borderRadius: 999,
    borderWidth: 1,
    paddingHorizontal: 7,
    paddingVertical: 2
  },
  roleChipHost: {
    backgroundColor: "rgba(50,230,179,0.16)",
    borderColor: colors.accent
  },
  roleChipText: {
    color: colors.muted,
    fontSize: 10,
    fontWeight: "800"
  },
  roleChipTextHost: {
    color: colors.accent
  },
  tileName: {
    color: colors.text,
    flexShrink: 1,
    fontSize: 12,
    fontWeight: "800"
  },
  tileNameFeatured: {
    fontSize: 14
  },
  overflowChip: {
    alignItems: "center",
    backgroundColor: "rgba(6,14,24,0.72)",
    borderColor: "rgba(255,255,255,0.16)",
    borderRadius: 999,
    borderWidth: 1,
    bottom: 10,
    flexDirection: "row",
    gap: 4,
    paddingHorizontal: 9,
    paddingVertical: 4,
    position: "absolute",
    right: 10
  },
  overflowText: {
    color: colors.text,
    fontSize: 11,
    fontWeight: "900"
  },
  header: {
    left: 0,
    position: "absolute",
    right: 0,
    top: 0
  },
  a11yProbe: {
    height: 0,
    width: 0
  }
}));
