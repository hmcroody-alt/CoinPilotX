import { NativeStackScreenProps } from "@react-navigation/native-stack";
import { ResizeMode, Video } from "expo-av";
import { useEffect, useRef, useState } from "react";
import { ActivityIndicator, Image, Pressable, StyleSheet, Text, View } from "react-native";
import { useSafeAreaInsets } from "react-native-safe-area-context";
import { getLiveState, livePlaybackUrl, livePosterUrl } from "../api/live";
import { RootStackParamList } from "../navigation/types";
import { colors } from "../theme/colors";
import { createThemedStyles } from "../theme/themedStyles";

type ResolveState = "resolving" | "ready" | "unavailable";

/**
 * Dedicated viewer for a finished broadcast's replay. Plays the real recorded
 * Mux HLS asset with native scrubbing controls. When the backend hasn't produced
 * a replay (recording still processing, or none was recorded), it shows an honest
 * "replay unavailable" surface instead of a blank or fake player.
 */
export function ReplayViewerScreen({ route, navigation }: NativeStackScreenProps<RootStackParamList, "ReplayViewer">) {
  const insets = useSafeAreaInsets();
  const liveId = Number(route.params?.liveId || 0);
  const paramUrl = String(route.params?.replayUrl || "");
  const [url, setUrl] = useState(paramUrl);
  const [poster, setPoster] = useState(String(route.params?.poster || ""));
  const [state, setState] = useState<ResolveState>(paramUrl ? "ready" : "resolving");
  const [failed, setFailed] = useState(false);
  const videoRef = useRef<Video | null>(null);

  useEffect(() => {
    if (paramUrl || liveId <= 0) {
      if (!paramUrl) setState("unavailable");
      return;
    }
    let cancelled = false;
    getLiveState(liveId)
      .then((liveState) => {
        if (cancelled) return;
        const resolved = livePlaybackUrl(liveState);
        if (resolved) {
          setUrl(resolved);
          if (!poster) setPoster(livePosterUrl(liveState));
          setState("ready");
        } else {
          setState("unavailable");
        }
      })
      .catch(() => {
        if (!cancelled) setState("unavailable");
      });
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [liveId, paramUrl]);

  const title = String(route.params?.title || "Replay");
  const creator = String(route.params?.creator || "");
  const showPlayer = state === "ready" && Boolean(url) && !failed;

  return (
    <View style={styles.root}>
      {showPlayer ? (
        <Video
          ref={videoRef}
          source={{ uri: url }}
          style={StyleSheet.absoluteFill}
          resizeMode={ResizeMode.CONTAIN}
          useNativeControls
          shouldPlay
          isLooping={false}
          usePoster={Boolean(poster)}
          posterSource={poster ? { uri: poster } : undefined}
          onError={() => setFailed(true)}
        />
      ) : (
        <View style={StyleSheet.absoluteFill}>
          {poster ? <Image source={{ uri: poster }} style={StyleSheet.absoluteFill} resizeMode="cover" blurRadius={14} /> : null}
          <View style={styles.scrim} />
          <View style={styles.messageCore}>
            {state === "resolving" && !failed ? (
              <>
                <ActivityIndicator color={colors.accent} style={{ marginBottom: 12 }} />
                <Text style={styles.messageTitle}>Loading replay…</Text>
                <Text style={styles.messageBody}>Fetching the recorded broadcast.</Text>
              </>
            ) : (
              <>
                <Text style={styles.messageTitle}>Replay unavailable</Text>
                <Text style={styles.messageBody}>
                  {failed
                    ? "This replay could not be played. It may still be processing — try again shortly."
                    : "No recording is available for this broadcast yet. Replays appear once the recording finishes processing."}
                </Text>
              </>
            )}
          </View>
        </View>
      )}

      <View style={[styles.topBar, { paddingTop: insets.top + 8 }]} pointerEvents="box-none">
        <Pressable style={styles.backButton} onPress={() => navigation.goBack()} accessibilityRole="button" accessibilityLabel="Close replay">
          <Text style={styles.backText}>✕</Text>
        </Pressable>
        <View style={styles.titleWrap} pointerEvents="none">
          <View style={styles.replayBadge}>
            <Text style={styles.replayBadgeText}>REPLAY</Text>
          </View>
          <Text style={styles.title} numberOfLines={1}>
            {title}
          </Text>
          {creator ? (
            <Text style={styles.creator} numberOfLines={1}>
              {creator}
            </Text>
          ) : null}
        </View>
      </View>
    </View>
  );
}

const styles = createThemedStyles(() => ({
  backButton: {
    alignItems: "center",
    backgroundColor: "rgba(2,4,10,0.6)",
    borderRadius: 999,
    height: 40,
    justifyContent: "center",
    width: 40
  },
  backText: {
    color: "#fff",
    fontSize: 18,
    fontWeight: "800"
  },
  creator: {
    color: colors.muted,
    fontSize: 13,
    fontWeight: "600"
  },
  messageBody: {
    color: colors.muted,
    fontSize: 14,
    lineHeight: 20,
    marginTop: 8,
    maxWidth: 300,
    textAlign: "center"
  },
  messageCore: {
    ...StyleSheet.absoluteFillObject,
    alignItems: "center",
    justifyContent: "center",
    padding: 24
  },
  messageTitle: {
    color: colors.text,
    fontSize: 19,
    fontWeight: "900",
    textAlign: "center"
  },
  replayBadge: {
    backgroundColor: "rgba(109,244,229,0.16)",
    borderColor: "rgba(109,244,229,0.4)",
    borderRadius: 6,
    borderWidth: 1,
    paddingHorizontal: 8,
    paddingVertical: 3
  },
  replayBadgeText: {
    color: colors.accent,
    fontSize: 11,
    fontWeight: "900",
    letterSpacing: 1
  },
  root: {
    backgroundColor: "#02040a",
    flex: 1
  },
  scrim: {
    ...StyleSheet.absoluteFillObject,
    backgroundColor: "rgba(3,10,20,0.6)"
  },
  title: {
    color: "#fff",
    fontSize: 16,
    fontWeight: "800",
    marginTop: 4
  },
  titleWrap: {
    flex: 1
  },
  topBar: {
    alignItems: "flex-start",
    flexDirection: "row",
    gap: 12,
    left: 0,
    paddingHorizontal: 16,
    position: "absolute",
    right: 0,
    top: 0
  }
}));
