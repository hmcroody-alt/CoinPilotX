import { CameraType, CameraView, useCameraPermissions, useMicrophonePermissions } from "expo-camera";
import { Audio } from "expo-av";
import { useEffect, useMemo, useRef, useState } from "react";
import { ActivityIndicator, Platform, Pressable, ScrollView, StyleSheet, Text, TextInput, View } from "react-native";
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
import { sendConversationMessage, uploadMessengerMedia } from "../api/messenger";
import { uploadProfileAvatar, uploadProfileCover } from "../api/profile";
import { createStatus, StatusVisibility } from "../api/status";
import { createComposerModeFromCameraTarget, saveCreateCameraCaptureResult } from "../create/createComposerHandoff";
import { startLive } from "../api/live";
import { PreLiveConfigurationSheet } from "../live/PreLiveConfigurationSheet";
import { LiveStudioDraft, saveLiveStudioDraft } from "../live/liveStudioReadiness";
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
import { createThemedStyles } from "../theme/themedStyles";
import { getPulseRadioState, setPulseRadioVideoMonitorVolume, subscribePulseRadio } from "../core/pulseRadio";
import { recordPulseMusicEvent } from "../api/music";
import { VideoMusicPicker } from "../video/VideoMusicPicker";
import {
  configureVideoMusicMonitoring,
  createVideoMusicMonitor,
  DEFAULT_VIDEO_MIX_SETTINGS,
  exportVideoMusicMix,
  VideoMixSettings,
  VideoMusicSource
} from "../video/videoMusicMix";

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
  providerRoute: string;
};

const destinations: DestinationOption[] = [
  { key: "feed", label: "Feed", target: "feed", mode: "photo", helper: "Post to PulseSoc feed", providerRoute: "/pulse/camera/post" },
  { key: "status", label: "Status", target: "status", mode: "status", helper: "Publish a Status", providerRoute: "/pulse/camera/status" },
  { key: "reel", label: "Reel", target: "reel", mode: "reel", helper: "Create a Reel", providerRoute: "/pulse/camera/reel" },
  { key: "avatar", label: "Avatar", target: "avatar", mode: "photo", helper: "Update profile photo", providerRoute: "/pulse/camera/photo?target=avatar" },
  { key: "cover", label: "Cover", target: "cover", mode: "photo", helper: "Update profile cover", providerRoute: "/pulse/camera/photo?target=cover" },
  { key: "message", label: "Message", target: "message", mode: "photo", helper: "Send to Messenger", providerRoute: "/pulse/camera/photo?target=message" },
  { key: "creator", label: "Creator", target: "creator", mode: "photo", helper: "Creator tools", providerRoute: "/pulse/camera" },
  { key: "marketplace", label: "Market", target: "marketplace", mode: "photo", helper: "Marketplace tools", providerRoute: "/pulse/camera" }
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
  const [showMusicPicker, setShowMusicPicker] = useState(false);
  const [videoMusic, setVideoMusic] = useState<VideoMusicSource | null>(null);
  const [capturedMusic, setCapturedMusic] = useState<VideoMusicSource | null>(null);
  const [mixSettings, setMixSettings] = useState<VideoMixSettings>(DEFAULT_VIDEO_MIX_SETTINGS);
  const [radioState, setRadioState] = useState(getPulseRadioState());
  const [cameraPermission, requestCameraPermission] = useCameraPermissions();
  const [microphonePermission, requestMicrophonePermission] = useMicrophonePermissions();
  const [config, setConfig] = useState<PulseCameraConfig | null>(null);
  const [configError, setConfigError] = useState("");
  const [caption, setCaption] = useState("");
  const [privacy, setPrivacy] = useState<StatusVisibility>("public");
  const [selectedEffect, setSelectedEffect] = useState("natural");
  const [recording, setRecording] = useState(false);
  const [showPreLive, setShowPreLive] = useState(false);
  const [goingLive, setGoingLive] = useState(false);
  const [liveError, setLiveError] = useState("");
  const [publishing, setPublishing] = useState(false);
  const [publishStage, setPublishStage] = useState<"idle" | "validating" | "uploading" | "processing" | "publishing" | "published" | "failed">("idle");
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");
  const cameraRef = useRef<CameraView | null>(null);
  const musicMonitorRef = useRef<Audio.Sound | null>(null);
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

  useEffect(() => subscribePulseRadio(setRadioState), []);

  useEffect(() => () => {
    musicMonitorRef.current?.unloadAsync().catch(() => undefined);
    musicMonitorRef.current = null;
  }, []);

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
        setError("Camera permission is required. Gallery upload is still available.");
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
      const ready = await ensureLiveReady();
      if (ready) setShowPreLive(true);
      return;
    }
    if (nativeUnsupportedDestinations.has(destination.key)) {
      openProviderBoundary();
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
    setMessage(videoMusic ? `Recording with ${videoMusic.title}.` : "Recording video.");
    try {
      let musicStartSeconds = videoMusic?.startOffsetSeconds || 0;
      if (videoMusic) {
        await configureVideoMusicMonitoring();
        if (videoMusic.kind === "pulse_radio") {
          musicStartSeconds = getPulseRadioState().positionMillis / 1000;
          await setPulseRadioVideoMonitorVolume(mixSettings.musicVolume);
        } else if (musicMonitorRef.current) {
          const monitorStatus = await musicMonitorRef.current.getStatusAsync();
          if (monitorStatus.isLoaded) musicStartSeconds = monitorStatus.positionMillis / 1000;
          await musicMonitorRef.current.setVolumeAsync(mixSettings.musicVolume * 0.72);
        }
      }
      const video = await cameraRef.current?.recordAsync({
        maxDuration: policy.maxVideoDurationSeconds,
        maxFileSize: policy.maxVideoBytes
      });
      if (!video?.uri) throw new Error("Camera did not return a video.");
      let finalUri = video.uri;
      if (videoMusic) {
        setMessage("Creating the final music and microphone mix.");
        try {
          const mixed = await exportVideoMusicMix(video.uri, videoMusic, mixSettings, musicStartSeconds);
          finalUri = mixed.uri;
          setCapturedMusic({ ...videoMusic, startOffsetSeconds: musicStartSeconds });
          recordPulseMusicEvent(videoMusic.trackId, "use_video", "native_video_camera").catch(() => undefined);
        } catch (mixError) {
          setCapturedMusic(null);
          setError(`${mixError instanceof Error ? mixError.message : "Music mixing failed."} The original video and microphone recording were preserved.`);
        }
      } else {
        setCapturedMusic(null);
      }
      const asset = await nativeMediaAssetFromUri(finalUri, "video", { mimeType: "video/mp4" });
      await handleCapturedAsset(asset);
    } catch (captureError) {
      setError(captureError instanceof Error ? captureError.message : "Video capture failed.");
    } finally {
      setRecording(false);
    }
  }

  async function selectVideoMusic(source: VideoMusicSource) {
    setError("");
    if (musicMonitorRef.current) await musicMonitorRef.current.unloadAsync().catch(() => undefined);
    musicMonitorRef.current = null;
    setVideoMusic(source);
    try {
      if (source.kind === "pulse_radio") {
        await configureVideoMusicMonitoring();
        await setPulseRadioVideoMonitorVolume(mixSettings.musicVolume);
      } else {
        musicMonitorRef.current = await createVideoMusicMonitor(source, mixSettings.musicVolume);
      }
      setMessage(`${source.title} is ready for this video.`);
    } catch (musicError) {
      setVideoMusic(null);
      setError(musicError instanceof Error ? musicError.message : "That track could not be previewed.");
    }
  }

  async function removeVideoMusic() {
    if (musicMonitorRef.current) await musicMonitorRef.current.unloadAsync().catch(() => undefined);
    musicMonitorRef.current = null;
    setVideoMusic(null);
    setCapturedMusic(null);
    setMessage("Music removed. Video will record with microphone audio only.");
  }

  function updateMixSettings(next: VideoMixSettings) {
    setMixSettings(next);
    if (videoMusic?.kind === "pulse_radio") setPulseRadioVideoMonitorVolume(next.musicVolume).catch(() => undefined);
    else musicMonitorRef.current?.setVolumeAsync(next.musicVolume * 0.72).catch(() => undefined);
  }

  async function chooseFromGallery() {
    setError("");
    const asset = captureMode === "video" ? await mediaUpload.chooseVideo() : await mediaUpload.chooseImage();
    if (asset) await handleCapturedAsset(asset);
  }

  async function ensureLiveReady() {
    setError("");
    setLiveError("");
    if (!cameraPermission?.granted) {
      const next = await requestCameraPermission();
      if (!next.granted) {
        setError("Camera permission is required to go live.");
        return false;
      }
    }
    if (!microphonePermission?.granted) {
      const next = await requestMicrophonePermission();
      setMicEnabled(next.granted);
    }
    return true;
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

  async function handleGoLive(draft: LiveStudioDraft) {
    setGoingLive(true);
    setLiveError("");
    try {
      const saved = await saveLiveStudioDraft(draft);
      const result = await startLive(saved);
      setShowPreLive(false);
      navigation.navigate("NativeLiveHost", {
        liveId: result.liveId,
        room: result.room,
        tokenUrl: result.tokenUrl,
        title: saved.title.trim() || "PulseSoc Live"
      });
    } catch (err) {
      setLiveError(err instanceof Error && err.message ? err.message : "PulseSoc could not start your broadcast.");
    } finally {
      setGoingLive(false);
    }
  }

  async function publish() {
    if (!mediaUpload.asset) {
      setError("Capture or choose media before publishing.");
      return;
    }
    if (nativeUnsupportedDestinations.has(destination.key)) {
      openProviderBoundary();
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
        share_to_feed: false,
        music_track_id: capturedMusic?.trackId,
        original_audio_muted: false,
        audio_start_time: capturedMusic?.startOffsetSeconds,
        sound_start_seconds: capturedMusic?.startOffsetSeconds,
        audio_volume: mixSettings.musicVolume,
        audio_baked_in: Boolean(capturedMusic)
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
      visibility: privacy,
      music_track_id: capturedMusic?.trackId,
      original_audio_muted: false,
      audio_start_time: capturedMusic?.startOffsetSeconds,
      audio_volume: mixSettings.musicVolume,
      audio_baked_in: Boolean(capturedMusic)
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

  function openProviderBoundary() {
    if (destination.key === "creator") {
      navigation.navigate("CreatorStudio");
      return;
    }
    if (destination.key === "marketplace") {
      navigation.navigate("MarketplaceCreateGateway", { title: "Create Listing" });
      return;
    }
    setError("This camera mode is not available in the app yet.");
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
            mode={captureMode === "photo" ? "picture" : "video"}
            mute={captureMode === "photo" || !micEnabled}
            videoQuality={policy.videoQuality}
            mirror={cameraFacing === "front"}
          />
        ) : (
          <View style={styles.webFallback}>
            <Text style={styles.webTitle}>Camera permission needed</Text>
            <Text style={styles.webText}>Gallery fallback is available if camera access is denied.</Text>
            <Pressable accessibilityRole="button" style={styles.primaryButton} onPress={() => requestCameraPermission()}>
              <Text style={styles.primaryText}>Allow Camera</Text>
            </Pressable>
          </View>
        )}

        <View style={styles.topBar}>
          <Pressable accessibilityRole="button" style={styles.iconButton} onPress={() => navigation.goBack()}>
            <Text style={styles.iconText}>Close</Text>
          </Pressable>
          <View style={styles.modeSwitch}>
            <Pressable accessibilityRole="button" style={[styles.modeButton, captureMode === "photo" && styles.modeActive]} onPress={() => setCaptureMode("photo")}>
              <Text style={styles.modeText}>Photo</Text>
            </Pressable>
            <Pressable accessibilityRole="button" style={[styles.modeButton, captureMode === "video" && styles.modeActive]} onPress={() => setCaptureMode("video")}>
              <Text style={styles.modeText}>Video</Text>
            </Pressable>
            <Pressable accessibilityRole="button" style={[styles.modeButton, captureMode === "live" && styles.modeActive]} onPress={() => setCaptureMode("live")}>
              <Text style={styles.modeText}>Live</Text>
            </Pressable>
          </View>
          <Pressable accessibilityRole="button" style={styles.iconButton} onPress={() => setCameraFacing((current) => (current === "back" ? "front" : "back"))}>
            <Text style={styles.iconText}>Flip</Text>
          </Pressable>
        </View>

        {composerReturnMode ? null : (
          <View style={styles.contextCard} pointerEvents="none">
            <Text style={styles.contextText}>{modeHint(captureMode)}</Text>
          </View>
        )}

        <View style={styles.captureDock}>
          <Pressable accessibilityRole="button"
            style={styles.dockButton}
            onPress={captureMode === "live" ? () => setShowPreLive(true) : chooseFromGallery}
          >
            <Text style={styles.dockText}>{captureMode === "live" ? "Live tools" : "Gallery"}</Text>
          </Pressable>
          <Pressable accessibilityRole="button" accessibilityState={{ disabled: publishing }} style={[styles.captureButton, recording && styles.recording]} disabled={publishing} onPress={capture}>
            <Text style={styles.captureText}>{recording ? "Stop" : captureMode === "live" ? "Live" : captureMode === "video" ? "Record" : "Snap"}</Text>
          </Pressable>
          <Pressable accessibilityRole="button" accessibilityState={{ disabled: captureMode === "photo" }}
            style={[styles.dockButton, captureMode === "photo" && styles.disabled]}
            disabled={captureMode === "photo"}
            onPress={captureMode === "video" ? () => setShowMusicPicker(true) : async () => {
              if (!microphonePermission?.granted) {
                const next = await requestMicrophonePermission();
                setMicEnabled(next.granted);
              } else {
                setMicEnabled((current) => !current);
              }
            }}
          >
            <Text style={styles.dockText}>{captureMode === "video" ? (videoMusic ? "Music ✓" : "Music") : (micEnabled ? "Mic" : "Muted")}</Text>
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
              <Pressable accessibilityRole="button" key={item.key} style={[styles.destination, active && styles.destinationActive]} onPress={() => setDestination(item)}>
                <Text style={[styles.destinationText, active && styles.destinationTextActive]}>{item.label}</Text>
                <Text style={styles.destinationHelper}>{item.helper}</Text>
              </Pressable>
            );
          })}
        </ScrollView>

        <View style={styles.effectRow}>
          {["natural", "low_light", "cinematic"].map((effect) => (
            <Pressable accessibilityRole="button" key={effect} style={[styles.effectButton, selectedEffect === effect && styles.effectActive]} onPress={() => setSelectedEffect(effect)}>
              <Text style={styles.effectText}>{effect.replace("_", " ")}</Text>
            </Pressable>
          ))}
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
            <Pressable accessibilityRole="button" key={option.value} style={[styles.visibility, privacy === option.value && styles.visibilityActive]} onPress={() => setPrivacy(option.value)}>
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
          <Pressable accessibilityRole="button" accessibilityState={{ disabled: publishing || mediaUpload.uploading }} style={styles.secondaryButton} disabled={publishing || mediaUpload.uploading} onPress={mediaUpload.reset}>
            <Text style={styles.secondaryText}>Retake</Text>
          </Pressable>
          <Pressable accessibilityRole="button" accessibilityState={{ disabled: !mediaUpload.asset || publishing || mediaUpload.uploading }} style={[styles.primaryButton, (!mediaUpload.asset || publishing || mediaUpload.uploading) && styles.disabled]} disabled={!mediaUpload.asset || publishing || mediaUpload.uploading} onPress={publish}>
            {publishing || mediaUpload.uploading ? <ActivityIndicator color={colors.background} /> : <Text style={styles.primaryText}>Publish</Text>}
          </Pressable>
        </View>

        <Text style={styles.deviceNote}>Camera, microphone, recording, compression, and large upload behavior require real-device QA before release claims.</Text>
      </ScrollView>
      )}

      <PreLiveConfigurationSheet
        visible={showPreLive}
        busy={goingLive}
        error={liveError}
        onClose={() => setShowPreLive(false)}
        onGoLive={handleGoLive}
      />
      <VideoMusicPicker
        visible={showMusicPicker && captureMode === "video"}
        radio={radioState}
        selected={videoMusic}
        settings={mixSettings}
        onClose={() => setShowMusicPicker(false)}
        onSelect={(source) => selectVideoMusic(source).catch(() => undefined)}
        onRemove={() => removeVideoMusic().catch(() => undefined)}
        onSettings={updateMixSettings}
      />
    </View>
  );
}

function modeHint(mode: NativeCaptureMode): string {
  if (mode === "video") return "Tap Record to start and stop. Review before you post.";
  if (mode === "live") return "Tap Live to set up and broadcast. Your camera stays on.";
  return "Tap Snap to capture. Review before you post.";
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

const styles = createThemedStyles(() => ({
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
  contextCard: {
    alignItems: "center",
    backgroundColor: "rgba(2, 7, 16, 0.72)",
    borderColor: "rgba(255,255,255,0.14)",
    borderRadius: 12,
    borderWidth: 1,
    bottom: 118,
    left: 18,
    paddingHorizontal: 14,
    paddingVertical: 10,
    position: "absolute",
    right: 18
  },
  contextText: {
    color: colors.text,
    fontSize: 12,
    fontWeight: "700",
    textAlign: "center"
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
}));
