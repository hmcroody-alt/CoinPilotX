import { ResizeMode, Video } from "expo-av";
import { useEffect, useMemo, useState } from "react";
import { ActivityIndicator, Image, StyleSheet, Text, View } from "react-native";
import { getLiveKitToken } from "../../api/live";
import { RtcVideoView } from "../../live/RtcVideoView";
import { PulseReel } from "../../api/reels";
import { useLiveBroadcastRoom } from "../../live/useLiveBroadcastRoom";
import { claimLivePlaybackOwner, releaseLivePlaybackOwner } from "../../live/livePlaybackOwnership";
import { reelLiveSessionId } from "../../reels/reelMediaKind";
import { colors } from "../../theme/colors";

/**
 * In-feed LIVE viewer. A live Reel plays inside the feed instead of bouncing the
 * user out to a web page or a detail screen. Transport, in priority order:
 *   1. LiveKit subscribe (real multi-guest stage — host + co-hosts as tiles)
 *   2. HLS playback_url via expo-av (when the room has no token / fails)
 *   3. An honest "not available" surface — NEVER a fake camera preview.
 *
 * We only render a video tile when there is a real subscribed track, and only
 * play HLS when the backend actually handed us a playback url. Anything else is
 * a labeled state, so the UI can never claim to be showing a broadcast it isn't.
 */

type ViewerMode = "connecting" | "livekit" | "hls" | "error" | "offline";

export function ReelLiveViewerSurface({ reel, active, muted, poster }: { reel: PulseReel; active: boolean; muted: boolean; poster?: string }) {
  const liveId = reelLiveSessionId(reel);
  const hlsUrl = String(reel.live?.playback_url || "");
  const room = useLiveBroadcastRoom();
  const { joinAsViewer: connect, leaveViewer: disconnect, setRemoteAudioEnabled } = room;
  const [mode, setMode] = useState<ViewerMode>("connecting");
  const VideoTileView = RtcVideoView;

  useEffect(() => {
    let cancelled = false;
    async function joinAsViewer() {
      if (!active) {
        await disconnect("left_feed_item").catch(() => undefined);
        await releaseLivePlaybackOwner("feed", liveId || reel.id || "unknown");
        return;
      }
      if (!liveId) {
        setMode(hlsUrl ? "hls" : "offline");
        return;
      }
      setMode("connecting");
      const playbackGranted = await claimLivePlaybackOwner("feed", liveId || reel.id || "unknown", () => disconnect("feed_live_backgrounded").then(() => undefined)).catch(() => false);
      if (!playbackGranted) {
        setMode("error");
        return;
      }
      try {
        const credentials = await getLiveKitToken(liveId, "viewer");
        if (cancelled) return;
        if (!credentials) {
          setMode(hlsUrl ? "hls" : "error");
          return;
        }
        const connected = await connect(credentials, { publish: false, refreshCredentials: () => getLiveKitToken(liveId, "viewer") });
        if (cancelled) return;
        setMode(connected ? "livekit" : hlsUrl ? "hls" : "error");
      } catch {
        if (!cancelled) setMode(hlsUrl ? "hls" : "error");
      }
    }
    joinAsViewer();
    return () => {
      cancelled = true;
      disconnect("left_feed_item").catch(() => undefined);
      releaseLivePlaybackOwner("feed", liveId || reel.id || "unknown");
    };
  }, [active, liveId, hlsUrl, connect, disconnect]);

  useEffect(() => {
    if (mode !== "livekit" || !room.connected) return;
    // Truly enable/disable the subscribed host audio track(s) rather than only
    // re-routing output. When the feed is not muted (the default) this also acts
    // as a belt-and-suspenders re-subscribe so host audio always plays; when the
    // viewer mutes, it actually silences the host instead of routing to earpiece.
    setRemoteAudioEnabled(!muted).catch(() => undefined);
  }, [mode, muted, room.connected, room.remoteAudioTrackCount, setRemoteAudioEnabled]);

  useEffect(() => {
    if (!active || mode !== "livekit" || !room.connected) {
      releaseLivePlaybackOwner("feed", liveId || reel.id || "unknown");
      return;
    }
    claimLivePlaybackOwner("feed", liveId || reel.id || "unknown", () => disconnect("feed_live_backgrounded").then(() => undefined)).catch(() => undefined);
    return () => { releaseLivePlaybackOwner("feed", liveId || reel.id || "unknown"); };
  }, [active, disconnect, liveId, mode, reel.id, room.connected]);

  const videoParticipants = useMemo(
    () =>
      room.participants
        .filter((participant) => participant.hasVideo && participant.videoTrack && !participant.isLocal)
        .sort((a, b) => Number(b.isHost) - Number(a.isHost))
        .slice(0, 4),
    [room.participants]
  );

  if (mode === "livekit") {
    if (!room.connected) {
      return <LiveMessage poster={poster} spinner title={room.reconnecting ? "Reconnecting to LIVE" : "Joining the broadcast"} body="Holding your place in the room." />;
    }
    if (room.error) {
      return <LiveMessage poster={poster} title="Live signal dropped" body={room.error} />;
    }
    if (!videoParticipants.length) {
      return <LiveMessage poster={poster} spinner title="Waiting for the host" body="The broadcaster's camera will appear here the moment it goes live." />;
    }
    return (
      <View style={styles.stage}>
        {videoParticipants.map((participant) => (
          <View key={participant.identity} style={[styles.tile, videoParticipants.length > 1 && styles.tileSplit]}>
            <VideoTileView videoTrack={participant.videoTrack} style={StyleSheet.absoluteFill} objectFit="cover" mirror={false} zOrder={0} />
            {participant.speaking ? <View style={styles.speakingRing} pointerEvents="none" /> : null}
          </View>
        ))}
      </View>
    );
  }

  if (mode === "hls" && hlsUrl) {
    return (
      <Video
        source={{ uri: hlsUrl }}
        style={StyleSheet.absoluteFill}
        resizeMode={ResizeMode.COVER}
        shouldPlay={active}
        isMuted={muted}
        isLooping={false}
        usePoster={Boolean(poster)}
        posterSource={poster ? { uri: poster } : undefined}
      />
    );
  }

  if (mode === "connecting") {
    return <LiveMessage poster={poster} spinner title="Tuning into LIVE" body="Connecting you to the broadcast." />;
  }

  return <LiveMessage poster={poster} title="This live isn't available" body="The broadcast can't be played right now. Pull to refresh or try again shortly." />;
}

function LiveMessage({ poster, title, body, spinner }: { poster?: string; title: string; body: string; spinner?: boolean }) {
  return (
    <View style={StyleSheet.absoluteFill}>
      {poster ? <Image source={{ uri: poster }} style={StyleSheet.absoluteFill} resizeMode="cover" blurRadius={12} /> : null}
      <View style={styles.messageScrim} />
      <View style={styles.messageCore}>
        {spinner ? <ActivityIndicator color="#36f0cf" style={{ marginBottom: 10 }} /> : null}
        <Text style={styles.messageTitle}>{title}</Text>
        <Text style={styles.messageBody}>{body}</Text>
      </View>
    </View>
  );
}

const styles = StyleSheet.create({
  messageBody: {
    color: colors.muted,
    fontSize: 13,
    lineHeight: 19,
    marginTop: 6,
    maxWidth: 280,
    textAlign: "center"
  },
  messageCore: {
    ...StyleSheet.absoluteFillObject,
    alignItems: "center",
    justifyContent: "center",
    padding: 24
  },
  messageScrim: {
    ...StyleSheet.absoluteFillObject,
    backgroundColor: "rgba(3,10,20,0.62)"
  },
  messageTitle: {
    color: colors.text,
    fontSize: 17,
    fontWeight: "900",
    textAlign: "center"
  },
  speakingRing: {
    ...StyleSheet.absoluteFillObject,
    borderColor: "#36f0cf",
    borderRadius: 6,
    borderWidth: 2
  },
  stage: {
    ...StyleSheet.absoluteFillObject,
    backgroundColor: "#02050b",
    flexDirection: "row",
    flexWrap: "wrap"
  },
  tile: {
    backgroundColor: "#04101c",
    height: "100%",
    overflow: "hidden",
    width: "100%"
  },
  tileSplit: {
    height: "50%",
    width: "50%"
  }
});
