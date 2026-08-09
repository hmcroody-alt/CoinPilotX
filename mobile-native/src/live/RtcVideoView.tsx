import { useEffect, useState } from "react";
import { StyleSheet, View } from "react-native";

export function RtcVideoView({ videoTrack, style, objectFit = "cover", mirror = false, zOrder = 0 }: any) {
  const [LiveKitView, setLiveKitView] = useState<any>(null);
  const [AgoraView, setAgoraView] = useState<any>(null);
  useEffect(() => {
    if (videoTrack?.provider === "agora") import("react-native-agora").then(module => setAgoraView(() => module.RtcSurfaceView)).catch(() => undefined);
    else import("@livekit/react-native").then(module => setLiveKitView(() => module.VideoView)).catch(() => undefined);
  }, [videoTrack?.provider]);
  if (videoTrack?.provider === "agora") {
    return AgoraView ? <AgoraView canvas={{ uid: Number(videoTrack.uid || 0) }} style={style} zOrderMediaOverlay={Boolean(videoTrack.local)} /> : <View style={[StyleSheet.absoluteFill, style]} />;
  }
  return LiveKitView ? <LiveKitView videoTrack={videoTrack} style={style} objectFit={objectFit} mirror={mirror} zOrder={zOrder} /> : <View style={[StyleSheet.absoluteFill, style]} />;
}
