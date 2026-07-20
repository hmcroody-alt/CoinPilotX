import { CameraType, CameraView, useCameraPermissions, useMicrophonePermissions } from "expo-camera";
import { useEffect, useMemo, useRef, useState } from "react";
import { ActivityIndicator, Linking, Platform, Pressable, ScrollView, StyleSheet, Text, TextInput, View } from "react-native";
import {
  CameraMode,
  CameraTarget,
  createCameraPreview,
  getCameraConfig,
  markCameraPreviewPublished,
  PulseCameraConfig
} from "../api/camera";
import { createPost, listFeed, PulsePost } from "../api/feed";
import { createReel, listReels, PulseReel } from "../api/reels";
import { PULSE_API_BASE_URL } from "../api/config";
import { sendConversationMessage, uploadMessengerMedia } from "../api/messenger";
import { uploadProfileAvatar, uploadProfileCover } from "../api/profile";
import { createStatus, StatusVisibility } from "../api/status";
import { createComposerModeFromCameraTarget, saveCreateCameraCaptureResult } from "../create/createComposerHandoff";
import { MediaUploadPreview } from "../media/MediaUploadPreview";
import {
  cameraCompressionPolicy,
  nativeMediaAssetFromUri,
  NativeMediaAsset,
  uploadResultMediaId
} from "../media/nativeMediaUpload";
import { createQaCameraImageAsset, shouldEnableQaCameraMediaAutomation } from "../media/qaCameraMedia";
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
type NativeCaptureMode = "photo" | "video" | "live";

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
  const [captureMode, setCaptureMode] = useState<NativeCaptureMode>(initialModeFromParams(route.params, initialDestination));
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
  const [publishStage, setPublishStage] = useState<"idle" | "validating" | "uploading" | "processing" | "publishing" | "published" | "failed">("idle");
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");
  const cameraRef = useRef<CameraView | null>(null);
  const qaMediaSeedRef = useRef("");
  const qaPublishRef = useRef("");
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
  const composerReturnMode = Boolean(route.params?.returnToComposer);
  const composerMode = route.params?.composerMode || createComposerModeFromCameraTarget(destination.key, destination.mode);
  const liveStudioUrl = `${PULSE_API_BASE_URL}/pulse/live/studio?context_type=native_camera`;

  useEffect(() => {
    getCameraConfig({ target: destination.target, mode: destination.mode })
      .then(setConfig)
      .catch((configLoadError) => setConfigError(configLoadError instanceof Error ? configLoadError.message : "Camera config unavailable."));
  }, [destination.mode, destination.target]);

  useEffect(() => {
    if (route.params?.captureMode || route.params?.mode === "live") return;
    setCaptureMode(destination.mode === "reel" || destination.mode === "video" ? "video" : "photo");
  }, [destination.mode, route.params?.captureMode, route.params?.mode]);

  useEffect(() => {
    const nextDestination = destinationFromParams(route.params);
    setDestination((current) => (current.key === nextDestination.key ? current : nextDestination));
  }, [route.params?.mode, route.params?.target]);

  useEffect(() => {
    const qaMedia = route.params?.qaMedia;
    if (!qaMedia || !shouldEnableQaCameraMediaAutomation()) return;
    const seedKey = `${destination.key}:${qaMedia}:${route.params?.qaCaption || ""}`;
    if (qaMediaSeedRef.current === seedKey) return;
    qaMediaSeedRef.current = seedKey;
    createQaCameraImageAsset()
      .then((asset) => {
        mediaUpload.setAsset(asset);
        setCaption(route.params?.qaCaption || caption || "PulseSoc Camera simulator QA");
        setMessage("QA simulator media selected.");
      })
      .catch((qaError) => {
        setError(qaError instanceof Error ? qaError.message : "QA simulator media could not be prepared.");
      });
  }, [caption, destination.key, mediaUpload, route.params?.qaCaption, route.params?.qaMedia]);

  useEffect(() => {
    if (!route.params?.qaAutoPublish || !shouldEnableQaCameraMediaAutomation() || !mediaUpload.asset || publishing || mediaUpload.uploading) return;
    const publishKey = `${destination.key}:${route.params?.qaCaption || ""}:${mediaUpload.asset.uri}`;
    if (qaPublishRef.current === publishKey) return;
    qaPublishRef.current = publishKey;
    publish().catch(() => undefined);
  }, [destination.key, mediaUpload.asset, mediaUpload.uploading, publishing, route.params?.qaAutoPublish]);

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
    if (captureMode === "live") {
      openLiveStudio();
      return;
    }
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
      await handleCapturedAsset(asset);
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
      await handleCapturedAsset(asset);
    } catch (captureError) {
      setError(captureError instanceof Error ? captureError.message : "Video capture failed.");
    } finally {
      setRecording(false);
    }
  }

  async function chooseFromGallery() {
    setError("");
    if (captureMode === "live") {
      openLiveStudio();
      return;
    }
    const asset = captureMode === "video" ? await mediaUpload.chooseVideo() : await mediaUpload.chooseImage();
    if (asset) await handleCapturedAsset(asset);
  }

  async function handleCapturedAsset(asset: NativeMediaAsset) {
    if (!composerReturnMode) {
      mediaUpload.setAsset(asset);
      setMessage(`${asset.mediaType === "video" ? "Video" : "Photo"} captured. Review and publish when ready.`);
      return;
    }
    const saved = await saveCreateCameraCaptureResult({
      asset,
      composerMode,
      captureMode: asset.mediaType === "video" ? "video" : "photo"
    });
    setMessage("Capture saved. Returning to the Create composer.");
    navigation.navigate("Tabs", {
      screen: "Home",
      params: {
        openComposer: true,
        composerMode: saved.composerMode,
        composerReturnNonce: saved.id
      }
    });
  }

  function openLiveStudio() {
    setMessage("Native Live host publishing is not promoted from Camera yet. Opening the existing production Live Studio.");
    Linking.openURL(liveStudioUrl).catch(() => setError("Production Live Studio could not open."));
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
    setPublishStage("validating");
    setError("");
    setMessage("");
    try {
      const result = await publishForDestination(mediaUpload.asset);
      setPublishStage("published");
      setMessage(result.message || "Camera media published.");
      mediaUpload.reset();
      navigateAfterPublish(result);
    } catch (publishError) {
      setPublishStage("failed");
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
        file_size: uploaded.file_size || asset.size || 0,
        media_ids: uploaded.media_id ? [uploaded.media_id] : []
      });
      return { ok: true, message: "Media sent to Messenger.", conversationId };
    }

    setPublishStage(mediaUpload.result && uploadResultMediaId(mediaUpload.result) ? "processing" : "uploading");
    // A publish retry must reuse the canonical media record that already
    // received the bytes. Re-uploading here creates orphaned duplicates.
    const uploaded = mediaUpload.result && uploadResultMediaId(mediaUpload.result)
      ? mediaUpload.result
      : await mediaUpload.upload(uploadOptions);
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
    setPublishStage("publishing");

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
      const existing = await findExistingReelByMediaId(mediaId);
      if (existing) {
        await markCameraPreviewPublished({ preview_token: previewToken, entity_type: "reel", entity_id: existing.reel_id }).catch(() => undefined);
        return { ...existing, message: "Server-confirmed Reel restored without republishing." };
      }
      const reel = await createReel({
        media_ids: [mediaId],
        caption: caption.trim(),
        title: caption.trim() || "Camera Reel",
        visibility: privacy,
        share_to_feed: false
      });
      if (!reel.reel_id || !reel.post_id) throw new Error("Reel publication did not return canonical identifiers. Your uploaded video is preserved for retry.");
      await markCameraPreviewPublished({ preview_token: previewToken, entity_type: "reel", entity_id: reel.reel_id }).catch(() => undefined);
      return { ...reel, message: reel.message || "Reel created." };
    }

    const existing = await findExistingPostByMediaId(mediaId);
    if (existing) {
      await markCameraPreviewPublished({ preview_token: previewToken, entity_type: "post", entity_id: existing.post_id }).catch(() => undefined);
      return { ok: true, post: existing, post_id: existing.post_id, message: "Server-confirmed post restored without republishing." };
    }
    const post = await createPost({
      media_ids: [mediaId],
      body: caption.trim() || "Created with PulseSoc Camera",
      title: caption.trim() ? "PulseSoc Camera" : "",
      post_type: asset.mediaType,
      visibility: privacy
    });
    if (!post.post_id || !post.post) throw new Error("Post publication did not return a canonical post. Your uploaded media is preserved for retry.");
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
        {captureMode === "live" ? (
          <View style={styles.webFallback}>
            <Text style={styles.webTitle}>Live uses the production Studio</Text>
            <Text style={styles.webText}>Browser Live, LiveKit, Mux, co-hosting, moderation, and stream health remain on the existing verified Studio flow. Native Camera will not fake a broadcast.</Text>
            <Pressable style={styles.primaryButton} onPress={openLiveStudio}>
              <Text style={styles.primaryText}>Open Live Studio</Text>
            </Pressable>
          </View>
        ) : nativeCameraUnavailable ? (
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
            <Pressable style={[styles.modeButton, captureMode === "live" && styles.modeActive]} onPress={() => setCaptureMode("live")}>
              <Text style={styles.modeText}>Live</Text>
            </Pressable>
          </View>
          <Pressable style={styles.iconButton} onPress={() => setCameraFacing((current) => (current === "back" ? "front" : "back"))}>
            <Text style={styles.iconText}>Flip</Text>
          </Pressable>
        </View>

        <View style={styles.captureDock}>
          <Pressable style={styles.dockButton} onPress={chooseFromGallery}>
            <Text style={styles.dockText}>{captureMode === "live" ? "Studio" : "Gallery"}</Text>
          </Pressable>
          <Pressable style={[styles.captureButton, recording && styles.recording]} disabled={publishing} onPress={capture}>
            <Text style={styles.captureText}>{recording ? "Stop" : captureMode === "live" ? "Live" : captureMode === "video" ? "Record" : "Snap"}</Text>
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
        {composerReturnMode ? (
          <View style={styles.returnStatusPanel}>
            <Text style={styles.returnTitle}>{composerMode === "status" ? "Status Camera" : composerMode === "reel" ? "Reel Camera" : "Feed Camera"}</Text>
            <Text style={[styles.returnText, error ? styles.error : undefined]}>{error || message || "Capture media here. Publishing stays in the Create composer."}</Text>
          </View>
        ) : null}
      </View>

      {composerReturnMode ? null : (
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
          <Text style={styles.policyTitle}>Capture & upload policy</Text>
          <Text style={styles.policyText}>{policy.key} · {captureMode === "video" ? `${policy.videoQuality} capture target` : `${Math.round(policy.imageQuality * 100)}% image capture quality`}</Text>
          <Text style={styles.policyText}>Server validation, moderation, storage, and processing remain authoritative.</Text>
        </View>

        {error ? <Text style={styles.error}>{error}</Text> : null}
        {message ? <Text style={styles.message}>{message}</Text> : publishStage !== "idle" ? <Text style={styles.message}>{publishStageLabel(publishStage)}</Text> : null}

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
      )}
    </View>
  );
}

async function findExistingPostByMediaId(mediaId: number): Promise<PulsePost | null> {
  if (!mediaId) return null;
  const response = await listFeed({ feed: "my_posts", limit: 30 }).catch(() => null);
  return (response?.posts || []).find((post) => postMediaIds(post).includes(mediaId)) || null;
}

async function findExistingReelByMediaId(mediaId: number): Promise<PulseReel | null> {
  if (!mediaId) return null;
  const response = await listReels({ lane: "for_you", limit: 40 }).catch(() => null);
  return (response?.reels || []).find((reel) => (reel.media || []).some((item) => Number(item.id || item.media_id || 0) === mediaId)) || null;
}

function postMediaIds(post: PulsePost) {
  return (post.media || []).map((item) => Number(item.id || item.media_id || 0)).filter(Boolean);
}

function publishStageLabel(stage: "idle" | "validating" | "uploading" | "processing" | "publishing" | "published" | "failed") {
  if (stage === "validating") return "Validating selected media.";
  if (stage === "uploading") return "Uploading media bytes.";
  if (stage === "processing") return "Using the uploaded media record.";
  if (stage === "publishing") return "Creating the canonical publication.";
  if (stage === "published") return "Publication confirmed.";
  if (stage === "failed") return "Publication stopped. Your draft and uploaded media are preserved.";
  return "";
}

function destinationFromParams(params?: RootStackParamList["CameraStudio"]): DestinationOption {
  const target = String(params?.target || params?.mode || "").toLowerCase();
  return destinations.find((item) => item.key === target || item.target === target || item.mode === target) || destinations[0];
}

function initialModeFromParams(params: RootStackParamList["CameraStudio"] | undefined, destination: DestinationOption): NativeCaptureMode {
  const raw = String(params?.captureMode || params?.mode || destination.mode || "").toLowerCase();
  if (raw === "live") return "live";
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
  returnStatusPanel: {
    backgroundColor: "rgba(2, 7, 16, 0.74)",
    borderColor: "rgba(47, 225, 180, 0.26)",
    borderRadius: 18,
    borderWidth: 1,
    bottom: 122,
    left: 18,
    paddingHorizontal: 14,
    paddingVertical: 12,
    position: "absolute",
    right: 18
  },
  returnText: {
    color: colors.muted,
    fontSize: 12,
    fontWeight: "700",
    lineHeight: 17,
    marginTop: 3
  },
  returnTitle: {
    color: colors.text,
    fontSize: 14,
    fontWeight: "900"
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
    top: Platform.OS === "ios" ? 58 : 18
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
    padding: 24,
    paddingTop: Platform.OS === "ios" ? 104 : 24
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
