import { useEffect, useState } from "react";
import { StyleSheet, View } from "react-native";

// Keep the render-mode override opt-in so Feed can match its Mux replay without
// changing any other host, dedicated Live, or call surface.
export function agoraRenderModeForPresentation(presentation?: "cover" | "fit") {
  if (presentation === "cover") return 1; // RenderModeHidden: proportional crop-to-fill
  if (presentation === "fit") return 2; // RenderModeFit: proportional letterbox
  return undefined;
}

export function RtcVideoView({ videoTrack, style, agoraPresentation }: any) {
  const [AgoraView, setAgoraView] = useState<any>(null);
  useEffect(() => {
    import("react-native-agora").then(module => setAgoraView(() => module.RtcSurfaceView)).catch(() => undefined);
  }, []);
  const renderMode = agoraRenderModeForPresentation(agoraPresentation);
  return AgoraView ? <AgoraView canvas={{ uid: Number(videoTrack?.uid || 0), ...(renderMode ? { renderMode } : {}) }} style={style} zOrderMediaOverlay={Boolean(videoTrack?.local)} /> : <View style={[StyleSheet.absoluteFill, style]} />;
}
