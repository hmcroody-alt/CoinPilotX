import { CameraType, CameraView, useCameraPermissions, useMicrophonePermissions } from "expo-camera";
import { useEffect, useMemo, useRef, useState } from "react";
import { ActivityIndicator, Linking, Platform, Pressable, ScrollView, StyleSheet, Text, TextInput, View } from "react-native";
import {
  CameraMode,
  CameraTarget,
  createCameraPreview,
  createPostFromCamera,
  createReelFromCamera,
  getCameraConfig,
  markCameraPreviewPublished,
  PulseCameraConfig
} from "../api/camera";
import { PULSE_API_BASE_URL } from "../api/config";
import { sendConversationMessage, uploadMessengerMedia } from "../api/messenger";
import { uploadProfileAvatar, uploadProfileCover } from "../api/profile";
import { createStatus, StatusVisibility } from "../api/status";
import { MediaUploadPreview } from "../media/MediaUploadPreview";
import {
  cameraCompressionPolicy,
  nativeMediaAssetFromUri,
  NativeMediaAsset,
  uploadResultMediaId
} from "../media/nativeMediaUpload";
import { useNativeMediaUpload } from "../media/useNativeMediaUpload";
import { RootStackParamList } from "../navigation/types";
import { colors } from "../theme/colors";

type Props = {
  route: { params?: RootStackParamList["CameraStudio"] };
  navigation: {
    navigate: (name: keyof RootStackParamList | "Tabs", params?: Record<string, unknown>) => void;
    goBack: () => void;
  };
};

type DestinationKey = "feed" | "status" | "reel" | "avatar" | "cover" | "message" | "creator" | "marketplace";

type DestinationOption = {
  key: DestinationKey;
  label: string;
  target: CameraTarget;
  mode: CameraMode;
  helper: string;
  webPath: string;
};

const destinations: DestinationOption[] = [
  { key: "feed", label: "Feed", target: "feed", mode: "photo", helper: "Post to PulseSoc feed", webPath: "/pulse/camera/post" },
  { key: "status", label: "Status", target: "status", mode: "status", helper: "Publish a Status", webPath: "/pulse/camera/status" },
  { key: "reel", label: "Reel", target: "reel", mode: "reel", helper: "Create a Reel", webPath: "/pulse/camera/reel" },
  { key: "avatar", label: "Avatar", target: "avatar", mode: "photo", helper: "Update profile photo", webPath: "/pulse/camera/photo?target=avatar" },
  { key: "cover", label: "Cover", target: "cover", mode: "photo", helper: "Update profile cover", webPath: "/pulse/camera/photo?target=cover" },
  { key: "message", label: "Message", target: "message", mode: "photo", helper: "Send to Messenger", webPath: "/pulse/camera/photo?target=message" },
  { key: "creator", label: "Creator", target: "creator", mode: "photo", helper: "Creator tools fallback", webPath: "/pulse/camera" },
  { key: "marketplace", label: "Market", target: "marketplace", mode: "photo", helper: "Marketplace fallback", webPath: "/pulse/camera" }
];

const visibilityOptions: Array<{ label: string; value: StatusVisibility }> = [
  { label: "Public", value: "public" },
  { label: "Followers", value: "followers" },
  { label: "Private", value: "private" }
];

const nativeUnsupportedDestinations = new Set<DestinationKey>(["creator", "marketplace"]);

export function CameraStudioScreen({ route, navigation }: Props) {
  const initialDestination = destinationFromParams(route.params);
  const [destination, setDestination] = useState<DestinationOption>(initialDestination);
  const [captureMode, setCaptureMode] = useState<"photo" | "video">(initialModeFromParams(route.params, initialDestination));
  const [cameraFacing, setCameraFacing] = useState<CameraType>("back");
  const [micEnabled, setMicEnabled] = useState(true);
  const [cameraPermission, requestCameraPermission] = useCameraPermissions();
  const [microphonePermission, requestMicrophonePermission] = useMicrophonePermissions();
  const [config, setConfig] = useState<PulseCameraConfig | null>(null);
  const [configError, setConfigError] = useState("");
  const [caption, setCaption] = useState("");
  const [privacy, setPrivacy] = useState<StatusVisibility>("public");
  const [selectedEffect, setSelectedEffect] = useState("natural");
  const [recording, setRecording] = useState(false);
  const [publishing, setPublishing] = useState(false);
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");
  const cameraRef = useRef<CameraView | null>(null);
  const uploadOptions = useMemo(
    () => ({
      contextType: "pulse_camera",
      contextId: "native-camera-draft",
      target: destination.target,
      mode: captureMode,
      destination: destination.key,
      compressionPolicy: cameraCompressionPolicy(captureMode === "video" ? "video" : destination.mode, destination.key).key,
      filterName: selectedEffect,
      effectKey: selectedEffect
    }),
    [captureMode, destination.key, destination.mode, destination.target, selectedEffect]
  );
  const mediaUpload = useNativeMediaUpload(uploadOptions);
  const policy = cameraCompressionPolicy(captureMode === "video" ? "video" : destination.mode, destination.key);
  const nativeCameraUnavailable = Platform.OS === "web";

  useEffect(() => {
    getCameraConfig({ target: destination.target, mode: destination.mode })
      .then(setConfig)
      .catch((configLoadError) => setConfigError(configLoadError instanceof Error ? configLoadError.message : "Camera config unavailable."));
  }, [destination.mode, destination.target]);

  useEffect(() => {
    setCaptureMode(destination.mode === "reel" || destination.mode === "video" ? "video" : "photo");
  }, [destination.mode]);

  async function ensureCameraReady() {
    setError("");
    if (!cameraPermission?.granted) {
      const next = await requestCameraPermission();
      if (!next.granted) {
        setError("Camera permission is required. Gallery fallback is still available.");
        return false;
      }
    }
    if (captureMode === "video" && !microphonePermission?.granted) {
      const next = await requestMicrophonePermission();
      if (!next.granted) setMicEnabled(false);
    }
    return true;
  }

  async function capture() {
    if (nativeUnsupportedDestinations.has(destination.key)) {
      openWebFallback();
      return;
    }
    const ready = await ensureCameraReady();
    if (!ready) return;
    if (captureMode === "video") {
      await recordVideo();
      return;
    }
    try {
      const photo = await cameraRef.current?.takePictureAsync({
        quality: policy.imageQuality,
        exif: false,
        skipProcessing: false
      });
      if (!photo?.uri) throw new Error("Camera did not return a photo.");
      const asset = await nativeMediaAssetFromUri(photo.uri, "image", {
        width: photo.width,
        height: photo.height,
        mimeType: "image/jpeg"
      });
      mediaUpload.setAsset(asset);
      setMessage("Photo captured. Review and publish when ready.");
    } catch (captureError) {
      setError(captureError instanceof Error ? captureError.message : "Photo capture failed.");
    }
  }

  async function recordVideo() {
    if (recording) {
      cameraRef.current?.stopRecording();
      return;
    }
    setRecording(true);
    setMessage("Recording video.");
    try {
      const video = await cameraRef.current?.recordAsync({
        maxDuration: policy.maxVideoDurationSeconds,
        maxFileSize: policy.maxVideoBytes
      });
      if (!video?.uri) throw new Error("Camera did not return a video.");
      const asset = await nativeMediaAssetFromUri(video.uri, "video", { mimeType: "video/mp4" });
      mediaUpload.setAsset(asset);
      setMessage("Video captured. Review and publish when ready.");
    } catch (captureError) {
      setError(captureError instanceof Error ? captureError.message : "Video capture failed.");
    } finally {
      setRecording(false);
    }
  }

  async function chooseFromGallery() {
    setError("");
    const asset = captureMode === "video" ? await mediaUpload.chooseVideo() : await mediaUpload.chooseImage();
    if (asset) setMessage("Media selected from gallery.");
  }

  async function publish() {
    if (!mediaUpload.asset) {
      setError("Capture or choose media before publishing.");
      return;
    }
    if (nativeUnsupportedDestinations.has(destination.key)) {
      openWebFallback();
      return;
    }
    setPublishing(true);
    setError("");
    setMessage("");
    try {
      const result = await publishForDestination(mediaUpload.asset);
      setMessage(result.message || "Camera media published.");
      mediaUpload.reset();
      navigateAfterPublish(result);
    } catch (publishError) {
      setError(publishError instanceof Error ? publishError.message : "Camera publish failed.");
    } finally {
      setPublishing(false);
    }
  }

  async function publishForDestination(asset: NativeMediaAsset) {
    if (destination.key === "avatar") {
      await uploadProfileAvatar({ uri: asset.uri, name: asset.name, mimeType: asset.mimeType });
      return { ok: true, message: "Profile photo updated." };
    }
    if (destination.key === "cover") {
      await uploadProfileCover({ uri: asset.uri, name: asset.name, mimeType: asset.mimeType });
      return { ok: true, message: "Cover photo updated." };
    }
    if (destination.key === "message") {
      const conversationId = Number(route.params?.conversationId || 0);
      if (!conversationId) throw new Error("Open Camera Studio from a conversation to send media.");
      const uploaded = await uploadMessengerMedia({
        conversationId,
        uri: asset.uri,
        name: asset.name,
        mimeType: asset.mimeType
      });
      await sendConversationMessage(conversationId, {
        body: caption.trim(),
        message_type: uploaded.message_type || uploaded.type || asset.mediaType,
        media_url: uploaded.media_url || "",
        thumbnail_url: uploaded.thumbnail_url || "",
        file_size: uploaded.file_size || asset.size || 0
      });
      return { ok: true, message: "Media sent to Messenger.", conversationId };
    }

    const uploaded = await mediaUpload.upload(uploadOptions);
    const mediaId = uploaded ? uploadResultMediaId(uploaded) : 0;
    const media = uploaded?.media || {};
    const mediaUrl = uploaded?.media_url || uploaded?.playback_url || media.media_url || media.valid_url || media.playback_url || "";
    if (!mediaId && !mediaUrl) throw new Error("Upload completed but no media record was returned.");
    const preview = await createCameraPreview({
      destination: destination.target,
      media: { ...media, id: mediaId, media_id: mediaId, media_url: mediaUrl },
      caption: caption.trim(),
      privacy,
      effect_key: selectedEffect,
      beauty_key: selectedEffect
    }).catch(() => null);
    const previewToken = preview?.preview_token || preview?.token || "";

    if (destination.key === "status") {
      const status = await createStatus({
        status_type: asset.mediaType === "video" ? "video" : "photo",
        body: caption.trim(),
        visibility: privacy,
        duration_hours: 24,
        media_ids: mediaId ? [mediaId] : [],
        effect_name: selectedEffect,
        ai_context: { source: "native_camera_studio", compression_policy: policy.key }
      });
      await markCameraPreviewPublished({ preview_token: previewToken, entity_type: "status", entity_id: status.status_id }).catch(() => undefined);
      return { ...status, message: status.message || "Status published." };
    }

    if (destination.key === "reel") {
      const reel = await createReelFromCamera({
        media_id: mediaId,
        media_url: mediaUrl,
        thumbnail_url: media.thumbnail_url || media.poster_url || mediaUrl,
        caption: caption.trim(),
        title: caption.trim() || "Camera Reel"
      });
      await markCameraPreviewPublished({ preview_token: previewToken, entity_type: "reel", entity_id: Number(reel.reel_id || 0) }).catch(() => undefined);
      return { ...reel, message: reel.message || "Reel created." };
    }

    const post = await createPostFromCamera({
      media_id: mediaId,
      media_url: mediaUrl,
      body: caption.trim() || "Created with PulseSoc Camera",
      title: caption.trim() ? "PulseSoc Camera" : "",
      post_type: asset.mediaType
    });
    await markCameraPreviewPublished({ preview_token: previewToken, entity_type: "post", entity_id: post.post_id }).catch(() => undefined);
    return { ...post, message: post.message || "Post published." };
  }

  function navigateAfterPublish(result: { post_id?: number; status_id?: number; reel_id?: number; conversationId?: number }) {
    if (result.post_id) navigation.navigate("PostDetail", { postId: result.post_id, title: "Camera Post" });
    else if (result.status_id) navigation.navigate("StatusDetail", { statusId: result.status_id, title: "Camera Status" });
    else if (result.reel_id) navigation.navigate("ReelDetail", { reelId: result.reel_id, title: "Camera Reel" });
    else if (result.conversationId) navigation.navigate("Chat", { conversationId: result.conversationId, title: "Chat" });
    else if (destination.key === "avatar" || destination.key === "cover") navigation.navigate("ProfileEdit");
  }

  function openWebFallback() {
    const url = `${PULSE_API_BASE_URL}${destination.webPath}`;
    Linking.openURL(url).catch(() => setError("Advanced camera fallback could not open."));
  }

  return (
    <View style={styles.root}>
      <View style={styles.stage}>
        {nativeCameraUnavailable ? (
          <View style={styles.webFallback}>
            <Text style={styles.webTitle}>Camera preview requires a device build</Text>
            <Text style={styles.webText}>Use gallery fallback in QA browser. Real camera, microphone, compression, and recording remain device-unverified.</Text>
          </View>
        ) : cameraPermission?.granted ? (
          <CameraView
            ref={cameraRef}
            style={styles.camera}
            facing={cameraFacing}
            mode={captureMode === "video" ? "video" : "picture"}
            mute={captureMode !== "video" || !micEnabled}
            videoQuality={policy.videoQuality}
            mirror={cameraFacing === "front"}
          />
        ) : (
          <View style={styles.webFallback}>
            <Text style={styles.webTitle}>Camera permission needed</Text>
            <Text style={styles.webText}>Gallery fallback is available if camera access is denied.</Text>
            <Pressable style={styles.primaryButton} onPress={() => requestCameraPermission()}>
              <Text style={styles.primaryText}>Allow Camera</Text>
            </Pressable>
          </View>
        )}

        <View style={styles.topBar}>
          <Pressable style={styles.iconButton} onPress={() => navigation.goBack()}>
            <Text style={styles.iconText}>Close</Text>
          </Pressable>
          <View style={styles.modeSwitch}>
            <Pressable style={[styles.modeButton, captureMode === "photo" && styles.modeActive]} onPress={() => setCaptureMode("photo")}>
              <Text style={styles.modeText}>Photo</Text>
            </Pressable>
            <Pressable style={[styles.modeButton, captureMode === "video" && styles.modeActive]} onPress={() => setCaptureMode("video")}>
              <Text style={styles.modeText}>Video</Text>
            </Pressable>
          </View>
          <Pressable style={styles.iconButton} onPress={() => setCameraFacing((current) => (current === "back" ? "front" : "back"))}>
            <Text style={styles.iconText}>Flip</Text>
          </Pressable>
        </View>

        <View style={styles.captureDock}>
          <Pressable style={styles.dockButton} onPress={chooseFromGallery}>
            <Text style={styles.dockText}>Gallery</Text>
          </Pressable>
          <Pressable style={[styles.captureButton, recording && styles.recording]} disabled={publishing} onPress={capture}>
            <Text style={styles.captureText}>{recording ? "Stop" : captureMode === "video" ? "Record" : "Snap"}</Text>
          </Pressable>
          <Pressable
            style={[styles.dockButton, captureMode !== "video" && styles.disabled]}
            disabled={captureMode !== "video"}
            onPress={async () => {
              if (!microphonePermission?.granted) {
                const next = await requestMicrophonePermission();
                setMicEnabled(next.granted);
              } else {
                setMicEnabled((current) => !current);
              }
            }}
          >
            <Text style={styles.dockText}>{micEnabled ? "Mic" : "Muted"}</Text>
          </Pressable>
        </View>
      </View>

      <ScrollView style={styles.panel} contentContainerStyle={styles.panelContent} keyboardShouldPersistTaps="handled">
        <Text style={styles.title}>PulseSoc Camera</Text>
        <Text style={styles.subtitle}>
          {configError || config?.provider ? `Provider: ${config?.provider || "native fallback"}` : "Loading camera config"}
        </Text>

        <ScrollView horizontal showsHorizontalScrollIndicator={false} contentContainerStyle={styles.destinationRow}>
          {destinations.map((item) => {
            const active = item.key === destination.key;
            return (
              <Pressable key={item.key} style={[styles.destination, active && styles.destinationActive]} onPress={() => setDestination(item)}>
                <Text style={[styles.destinationText, active && styles.destinationTextActive]}>{item.label}</Text>
                <Text style={styles.destinationHelper}>{item.helper}</Text>
              </Pressable>
            );
          })}
        </ScrollView>

        <View style={styles.effectRow}>
          {["natural", "low_light", "cinematic"].map((effect) => (
            <Pressable key={effect} style={[styles.effectButton, selectedEffect === effect && styles.effectActive]} onPress={() => setSelectedEffect(effect)}>
              <Text style={styles.effectText}>{effect.replace("_", " ")}</Text>
            </Pressable>
          ))}
          <Pressable style={styles.effectButton} onPress={openWebFallback}>
            <Text style={styles.effectText}>Advanced FX</Text>
          </Pressable>
        </View>

        <TextInput
          style={styles.captionInput}
          value={caption}
          onChangeText={setCaption}
          placeholder="Add caption or context"
          placeholderTextColor={colors.muted}
          multiline
          textAlignVertical="top"
        />

        <View style={styles.visibilityRow}>
          {visibilityOptions.map((option) => (
            <Pressable key={option.value} style={[styles.visibility, privacy === option.value && styles.visibilityActive]} onPress={() => setPrivacy(option.value)}>
              <Text style={[styles.visibilityText, privacy === option.value && styles.visibilityTextActive]}>{option.label}</Text>
            </Pressable>
          ))}
        </View>

        <MediaUploadPreview
          asset={mediaUpload.asset}
          media={mediaUpload.result?.media}
          progress={mediaUpload.progress}
          error={mediaUpload.error}
          uploading={mediaUpload.uploading || publishing}
          onRetry={mediaUpload.retry}
          onCancel={mediaUpload.cancel}
        />

        <View style={styles.policyBox}>
          <Text style={styles.policyTitle}>Compression policy</Text>
          <Text style={styles.policyText}>{policy.key} · {captureMode === "video" ? policy.videoQuality : `${Math.round(policy.imageQuality * 100)}% image quality`}</Text>
          <Text style={styles.policyText}>Server validation, moderation, storage, and processing remain authoritative.</Text>
        </View>

        {error ? <Text style={styles.error}>{error}</Text> : null}
        {message ? <Text style={styles.message}>{message}</Text> : null}

        <View style={styles.actionRow}>
          <Pressable style={styles.secondaryButton} disabled={publishing || mediaUpload.uploading} onPress={mediaUpload.reset}>
            <Text style={styles.secondaryText}>Retake</Text>
          </Pressable>
          <Pressable style={[styles.primaryButton, (!mediaUpload.asset || publishing || mediaUpload.uploading) && styles.disabled]} disabled={!mediaUpload.asset || publishing || mediaUpload.uploading} onPress={publish}>
            {publishing || mediaUpload.uploading ? <ActivityIndicator color={colors.background} /> : <Text style={styles.primaryText}>Publish</Text>}
          </Pressable>
        </View>

        <Text style={styles.deviceNote}>Camera, microphone, recording, compression, and large upload behavior require real-device QA before release claims.</Text>
      </ScrollView>
    </View>
  );
}

function destinationFromParams(params?: RootStackParamList["CameraStudio"]): DestinationOption {
  const target = String(params?.target || params?.mode || "").toLowerCase();
  return destinations.find((item) => item.key === target || item.target === target || item.mode === target) || destinations[0];
}

function initialModeFromParams(params: RootStackParamList["CameraStudio"] | undefined, destination: DestinationOption): "photo" | "video" {
  const raw = String(params?.captureMode || params?.mode || destination.mode || "").toLowerCase();
  return raw === "video" || raw === "reel" ? "video" : "photo";
}

const styles = StyleSheet.create({
  actionRow: {
    flexDirection: "row",
    gap: 10
  },
  camera: {
    ...StyleSheet.absoluteFillObject
  },
  captionInput: {
    backgroundColor: colors.surface,
    borderColor: colors.border,
    borderRadius: 8,
    borderWidth: 1,
    color: colors.text,
    minHeight: 84,
    padding: 12
  },
  captureButton: {
    alignItems: "center",
    backgroundColor: colors.accent,
    borderColor: "rgba(255,255,255,0.8)",
    borderRadius: 44,
    borderWidth: 4,
    height: 88,
    justifyContent: "center",
    width: 88
  },
  captureDock: {
    alignItems: "center",
    bottom: 18,
    flexDirection: "row",
    justifyContent: "space-between",
    left: 18,
    position: "absolute",
    right: 18
  },
  captureText: {
    color: colors.background,
    fontSize: 12,
    fontWeight: "900"
  },
  destination: {
    backgroundColor: colors.surface,
    borderColor: colors.border,
    borderRadius: 8,
    borderWidth: 1,
    minWidth: 112,
    padding: 10
  },
  destinationActive: {
    borderColor: colors.accent
  },
  destinationHelper: {
    color: colors.muted,
    fontSize: 11,
    marginTop: 4
  },
  destinationRow: {
    gap: 10
  },
  destinationText: {
    color: colors.text,
    fontWeight: "900"
  },
  destinationTextActive: {
    color: colors.accent
  },
  deviceNote: {
    color: colors.muted,
    fontSize: 12,
    lineHeight: 18
  },
  disabled: {
    opacity: 0.5
  },
  dockButton: {
    alignItems: "center",
    backgroundColor: "rgba(23,26,31,0.78)",
    borderColor: "rgba(255,255,255,0.14)",
    borderRadius: 8,
    borderWidth: 1,
    justifyContent: "center",
    minHeight: 48,
    minWidth: 78,
    paddingHorizontal: 10
  },
  dockText: {
    color: colors.text,
    fontWeight: "900"
  },
  effectActive: {
    borderColor: colors.accent
  },
  effectButton: {
    borderColor: colors.border,
    borderRadius: 8,
    borderWidth: 1,
    paddingHorizontal: 10,
    paddingVertical: 9
  },
  effectRow: {
    flexDirection: "row",
    flexWrap: "wrap",
    gap: 8
  },
  effectText: {
    color: colors.text,
    fontSize: 12,
    fontWeight: "800",
    textTransform: "capitalize"
  },
  error: {
    color: colors.danger,
    fontWeight: "800"
  },
  iconButton: {
    backgroundColor: "rgba(23,26,31,0.78)",
    borderColor: "rgba(255,255,255,0.14)",
    borderRadius: 8,
    borderWidth: 1,
    minWidth: 66,
    paddingHorizontal: 10,
    paddingVertical: 10
  },
  iconText: {
    color: colors.text,
    fontWeight: "900",
    textAlign: "center"
  },
  message: {
    color: colors.accent,
    fontWeight: "800"
  },
  modeActive: {
    backgroundColor: colors.accent
  },
  modeButton: {
    borderRadius: 8,
    paddingHorizontal: 12,
    paddingVertical: 10
  },
  modeSwitch: {
    backgroundColor: "rgba(23,26,31,0.78)",
    borderColor: "rgba(255,255,255,0.14)",
    borderRadius: 8,
    borderWidth: 1,
    flexDirection: "row"
  },
  modeText: {
    color: colors.text,
    fontWeight: "900"
  },
  panel: {
    backgroundColor: colors.background,
    maxHeight: "48%"
  },
  panelContent: {
    gap: 12,
    padding: 16,
    paddingBottom: 30
  },
  policyBox: {
    backgroundColor: colors.surface,
    borderColor: colors.border,
    borderRadius: 8,
    borderWidth: 1,
    padding: 12
  },
  policyText: {
    color: colors.muted,
    fontSize: 12,
    lineHeight: 18
  },
  policyTitle: {
    color: colors.text,
    fontWeight: "900",
    marginBottom: 4
  },
  primaryButton: {
    alignItems: "center",
    backgroundColor: colors.accent,
    borderRadius: 8,
    flex: 1,
    justifyContent: "center",
    minHeight: 46,
    paddingHorizontal: 14
  },
  primaryText: {
    color: colors.background,
    fontWeight: "900"
  },
  recording: {
    backgroundColor: colors.danger
  },
  root: {
    backgroundColor: colors.background,
    flex: 1
  },
  secondaryButton: {
    alignItems: "center",
    borderColor: colors.border,
    borderRadius: 8,
    borderWidth: 1,
    flex: 1,
    justifyContent: "center",
    minHeight: 46,
    paddingHorizontal: 14
  },
  secondaryText: {
    color: colors.text,
    fontWeight: "900"
  },
  stage: {
    backgroundColor: "#02050b",
    flex: 1,
    minHeight: 320,
    overflow: "hidden"
  },
  subtitle: {
    color: colors.muted,
    fontSize: 13,
    lineHeight: 19
  },
  title: {
    color: colors.text,
    fontSize: 22,
    fontWeight: "900"
  },
  topBar: {
    alignItems: "center",
    flexDirection: "row",
    justifyContent: "space-between",
    left: 14,
    position: "absolute",
    right: 14,
    top: 14
  },
  visibility: {
    borderColor: colors.border,
    borderRadius: 8,
    borderWidth: 1,
    flex: 1,
    paddingVertical: 10
  },
  visibilityActive: {
    backgroundColor: colors.accent,
    borderColor: colors.accent
  },
  visibilityRow: {
    flexDirection: "row",
    gap: 8
  },
  visibilityText: {
    color: colors.text,
    fontWeight: "800",
    textAlign: "center"
  },
  visibilityTextActive: {
    color: colors.background
  },
  webFallback: {
    alignItems: "center",
    flex: 1,
    justifyContent: "center",
    padding: 24
  },
  webText: {
    color: colors.muted,
    lineHeight: 21,
    marginBottom: 14,
    textAlign: "center"
  },
  webTitle: {
    color: colors.text,
    fontSize: 18,
    fontWeight: "900",
    marginBottom: 8,
    textAlign: "center"
  }
});
