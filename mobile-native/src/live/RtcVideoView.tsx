import { useEffect, useState } from "react";
import { StyleSheet, View } from "react-native";

// Agora's native canvas does not read LiveKit's objectFit prop. Keep the
// override opt-in so Feed can match its Mux replay without changing any other
// host, dedicated Live, or call surface.
export function agoraRenderModeForPresentation(presentation?: "cover" | "fit") {
  if (presentation === "cover") return 1; // RenderModeHidden: proportional crop-to-fill
  if (presentation === "fit") return 2; // RenderModeFit: proportional letterbox
  return undefined;
}

export function RtcVideoView({ videoTrack, style, objectFit = "cover", mirror = false, zOrder = 0, agoraPresentation }: any) {
  const [LiveKitView, setLiveKitView] = useState<any>(null);
  const [AgoraView, setAgoraView] = useState<any>(null);
  useEffect(() => {
    if (videoTrack?.provider === "agora") import("react-native-agora").then(module => setAgoraView(() => module.RtcSurfaceView)).catch(() => undefined);
    else import("@livekit/react-native").then(module => setLiveKitView(() => module.VideoView)).catch(() => undefined);
  }, [videoTrack?.provider]);
  if (videoTrack?.provider === "agora") {
    const renderMode = agoraRenderModeForPresentation(agoraPresentation);
    return AgoraView ? <AgoraView canvas={{ uid: Number(videoTrack.uid || 0), ...(renderMode ? { renderMode } : {}) }} style={style} zOrderMediaOverlay={Boolean(videoTrack.local)} /> : <View style={[StyleSheet.absoluteFill, style]} />;
  }
  return LiveKitView ? <LiveKitView videoTrack={videoTrack} style={style} objectFit={objectFit} mirror={mirror} zOrder={zOrder} /> : <View style={[StyleSheet.absoluteFill, style]} />;
}
