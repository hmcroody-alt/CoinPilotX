import React, { useMemo, useState } from "react";
import { Alert, Image, KeyboardAvoidingView, Platform, Text, TextInput, TouchableOpacity, View } from "react-native";
import * as ImagePicker from "expo-image-picker";
import { NativeStackScreenProps } from "@react-navigation/native-stack";
import { ScreenScaffold } from "../../../components/ScreenScaffold";
import { colors, screenStyles } from "../../../components/theme";
import { MainStackParamList } from "../../../navigation/types";
import { createPost } from "../../../services/feed/feedApi";
import { mediaIdFromUpload, uploadFeedMedia, UploadAsset } from "../../../services/feed/mediaUpload";
import { trackMobileEvent } from "../../../services/analytics";

type Props = NativeStackScreenProps<MainStackParamList, "CreatePulse">;

const MAX_CHARS = 5000;

export function CreatePulseScreen({ navigation }: Props) {
  const [body, setBody] = useState("");
  const [asset, setAsset] = useState<UploadAsset | null>(null);
  const [uploadProgress, setUploadProgress] = useState(0);
  const [status, setStatus] = useState("");
  const [publishing, setPublishing] = useState(false);
  const tags = useMemo(() => Array.from(new Set((body.match(/#[A-Za-z0-9_]+/g) || []).map(tag => tag.slice(1).toLowerCase()))), [body]);
  const mentions = useMemo(() => Array.from(new Set((body.match(/@[A-Za-z0-9_.-]+/g) || []).map(tag => tag.slice(1)))), [body]);

  async function pick(source: "camera" | "library", media: "image" | "video" | "all") {
    const permission = source === "camera" ? await ImagePicker.requestCameraPermissionsAsync() : await ImagePicker.requestMediaLibraryPermissionsAsync();
    if (!permission.granted) {
      setStatus("Media permission is required.");
      return;
    }
    const options: ImagePicker.ImagePickerOptions = {
      mediaTypes: media === "image" ? ImagePicker.MediaTypeOptions.Images : media === "video" ? ImagePicker.MediaTypeOptions.Videos : ImagePicker.MediaTypeOptions.All,
      quality: 0.86,
      allowsEditing: false,
      videoMaxDuration: 180
    };
    const result = source === "camera" ? await ImagePicker.launchCameraAsync(options) : await ImagePicker.launchImageLibraryAsync(options);
    if (result.canceled || !result.assets[0]) return;
    const selected = result.assets[0];
    const mimeType = selected.mimeType || (selected.type === "video" ? "video/mp4" : "image/jpeg");
    setAsset({
      uri: selected.uri,
      name: selected.fileName || `pulse-${Date.now()}.${mimeType.includes("video") ? "mp4" : "jpg"}`,
      mimeType
    });
    setStatus("");
    setUploadProgress(0);
  }

  async function publish() {
    if (!body.trim() && !asset) {
      setStatus("Write something or attach media before publishing.");
      return;
    }
    setPublishing(true);
    setStatus(asset ? "Uploading media..." : "Publishing...");
    try {
      let mediaIds: number[] = [];
      if (asset) {
        const uploaded = await uploadFeedMedia(asset, setUploadProgress);
        const mediaId = mediaIdFromUpload(uploaded);
        if (!mediaId) throw new Error("Upload completed but no media id was returned.");
        mediaIds = [mediaId];
      }
      setStatus("Publishing...");
      const postType = asset?.mimeType.includes("video") ? "video" : asset ? "image" : "text";
      const result = await createPost({ body, mediaIds, postType, tags });
      trackMobileEvent("mobile_post_create", { post_id: result.post_id || result.post?.id, post_type: postType, media: !!asset, hashtags: tags.length, mentions: mentions.length });
      setStatus("Posted.");
      navigation.replace("HomeFeed");
    } catch (error) {
      setStatus(error instanceof Error ? error.message : "Could not publish.");
    } finally {
      setPublishing(false);
    }
  }

  return (
    <KeyboardAvoidingView style={{ flex: 1, backgroundColor: colors.background }} behavior={Platform.OS === "ios" ? "padding" : undefined}>
      <ScreenScaffold title="Create PulseSoc" subtitle="Text, photos, videos, hashtags, and mentions.">
        <TextInput
          value={body}
          onChangeText={text => setBody(text.slice(0, MAX_CHARS))}
          multiline
          placeholder="Share a status, market thought, scam warning, question, or update..."
          placeholderTextColor="#7890a8"
          style={[screenStyles.input, { minHeight: 160, textAlignVertical: "top" }]}
        />
        <Text style={screenStyles.muted}>{body.length}/{MAX_CHARS} · {tags.length} hashtags · {mentions.length} mentions</Text>
        {asset ? (
          <View style={screenStyles.card}>
            {asset.mimeType.includes("image") ? <Image source={{ uri: asset.uri }} style={{ width: "100%", aspectRatio: 1.3, borderRadius: 8 }} /> : <Text style={screenStyles.cardTitle}>Video selected: {asset.name}</Text>}
            <TouchableOpacity style={screenStyles.secondaryButton} onPress={() => setAsset(null)}>
              <Text style={screenStyles.secondaryButtonText}>Remove media</Text>
            </TouchableOpacity>
          </View>
        ) : null}
        <View style={{ flexDirection: "row", flexWrap: "wrap", gap: 8, marginBottom: 12 }}>
          <Tool label="Camera" onPress={() => pick("camera", "all")} />
          <Tool label="Photo Library" onPress={() => pick("library", "image")} />
          <Tool label="Video Library" onPress={() => pick("library", "video")} />
          <Tool label="Emoji" onPress={() => setBody(text => `${text} 🔥`)} />
        </View>
        {uploadProgress > 0 ? <Text style={screenStyles.muted}>Upload progress: {uploadProgress}%</Text> : null}
        <TouchableOpacity style={[screenStyles.button, publishing && { opacity: 0.7 }]} onPress={publish} disabled={publishing}>
          <Text style={screenStyles.buttonText}>{publishing ? "Publishing..." : "Post PulseSoc"}</Text>
        </TouchableOpacity>
        <TouchableOpacity style={screenStyles.secondaryButton} onPress={() => navigation.goBack()}>
          <Text style={screenStyles.secondaryButtonText}>Cancel</Text>
        </TouchableOpacity>
        {status ? <Text style={[screenStyles.muted, { marginTop: 12 }]}>{status}</Text> : null}
        {status.includes("failed") || status.includes("Could not") ? (
          <TouchableOpacity style={screenStyles.secondaryButton} onPress={() => asset ? publish() : Alert.alert("Retry", "Add media or text, then publish again.")}>
            <Text style={screenStyles.secondaryButtonText}>Retry</Text>
          </TouchableOpacity>
        ) : null}
      </ScreenScaffold>
    </KeyboardAvoidingView>
  );
}

function Tool({ label, onPress }: { label: string; onPress: () => void }) {
  return (
    <TouchableOpacity style={[screenStyles.secondaryButton, { marginTop: 0, minHeight: 40 }]} onPress={onPress}>
      <Text style={screenStyles.secondaryButtonText}>{label}</Text>
    </TouchableOpacity>
  );
}
