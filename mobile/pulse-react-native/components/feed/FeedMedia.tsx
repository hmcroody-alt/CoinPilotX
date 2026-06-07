import React from "react";
import { Image, Text, View } from "react-native";
import { useVideoPlayer, VideoView } from "expo-video";
import { PulseMedia } from "../../services/feed/types";
import { colors, screenStyles } from "../theme";

export function FeedMedia({ media }: { media: PulseMedia[] }) {
  if (!media.length) return null;
  return (
    <View style={{ gap: 10, marginTop: 12 }}>
      {media.slice(0, 4).map((item, index) => <MediaItem key={String(item.id || index)} item={item} />)}
    </View>
  );
}

function MediaItem({ item }: { item: PulseMedia }) {
  const type = String(item.media_type || item.type || "").toLowerCase();
  const source = item.playback_url || item.mux_hls_url || item.valid_url || item.media_url || "";
  const poster = item.poster_url || item.thumbnail_url || item.valid_url || "";
  const aspectRatio = safeAspect(item.aspect_ratio, item.width, item.height);

  if (!source && !poster) {
    return (
      <View style={[screenStyles.card, { marginBottom: 0 }]}>
        <Text style={screenStyles.muted}>Media is processing.</Text>
      </View>
    );
  }

  if (type.includes("video")) {
    return <VideoMediaItem source={source || poster} aspectRatio={aspectRatio} />;
  }

  return (
    <Image
      source={{ uri: source || poster }}
      resizeMode="cover"
      style={{ width: "100%", aspectRatio, borderRadius: 8, backgroundColor: colors.surfaceSoft }}
    />
  );
}

function VideoMediaItem({ source, aspectRatio }: { source: string; aspectRatio: number }) {
  const player = useVideoPlayer(source, nextPlayer => {
    nextPlayer.loop = false;
    nextPlayer.muted = false;
  });

  return (
    <VideoView
      player={player}
      nativeControls
      contentFit="contain"
      style={{ width: "100%", aspectRatio, borderRadius: 8, backgroundColor: colors.surfaceSoft }}
    />
  );
}

function safeAspect(aspect?: number, width?: number, height?: number) {
  if (aspect && aspect > 0.2 && aspect < 4) return aspect;
  if (width && height) return Math.max(0.5, Math.min(2.2, width / height));
  return 1;
}
