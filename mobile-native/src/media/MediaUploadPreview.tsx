import { ResizeMode, Video } from "expo-av";
import { Image, Pressable, StyleSheet, Text, View } from "react-native";
import { PulseMedia } from "../api/feed";
import { colors } from "../theme/colors";
import { formatFileSize } from "../utils/format";
import { NativeMediaAsset, nativeMediaKind, nativeMediaPreviewUrl, UploadProgress } from "./nativeMediaUpload";

type Props = {
  asset?: NativeMediaAsset | null;
  media?: PulseMedia | null;
  progress: UploadProgress;
  error?: string;
  uploading?: boolean;
  onRetry?: () => void;
  onCancel?: () => void;
};

export function MediaUploadPreview({ asset, media, progress, error, uploading, onRetry, onCancel }: Props) {
  const previewUrl = nativeMediaPreviewUrl(asset, media);
  const kind = nativeMediaKind(asset, media);
  return (
    <View style={styles.wrap}>
      <View style={styles.preview}>
        {kind === "image" && previewUrl ? <Image source={{ uri: previewUrl }} style={styles.media} resizeMode="cover" /> : null}
        {kind === "video" && previewUrl ? (
          <Video source={{ uri: previewUrl }} style={styles.media} resizeMode={ResizeMode.COVER} useNativeControls shouldPlay={false} />
        ) : null}
        {!previewUrl ? <Text style={styles.placeholder}>No media selected</Text> : null}
      </View>
      <View style={styles.meta}>
        <Text style={styles.title}>{asset?.name || media?.alt || "PulseSoc media"}</Text>
        <Text style={styles.detail}>{asset ? `${asset.mediaType} ${formatFileSize(asset.size)}` : kind}</Text>
        <View style={styles.progressTrack}>
          <View style={[styles.progressBar, { width: `${Math.max(0, Math.min(100, progress.percent))}%` }]} />
        </View>
        <Text style={[styles.message, error ? styles.error : undefined]}>{error || progress.message}</Text>
        <View style={styles.actions}>
          {uploading && onCancel ? (
            <Pressable style={styles.secondaryButton} onPress={onCancel}>
              <Text style={styles.secondaryText}>Cancel</Text>
            </Pressable>
          ) : null}
          {progress.stage === "failed" && onRetry ? (
            <Pressable style={styles.primaryButton} onPress={onRetry}>
              <Text style={styles.primaryText}>Retry</Text>
            </Pressable>
          ) : null}
        </View>
      </View>
    </View>
  );
}

const styles = StyleSheet.create({
  actions: {
    flexDirection: "row",
    gap: 10,
    marginTop: 10
  },
  detail: {
    color: colors.muted,
    fontSize: 12,
    marginTop: 3,
    textTransform: "capitalize"
  },
  error: {
    color: colors.danger
  },
  media: {
    ...StyleSheet.absoluteFillObject
  },
  message: {
    color: colors.muted,
    fontSize: 12,
    marginTop: 7
  },
  meta: {
    flex: 1,
    padding: 12
  },
  placeholder: {
    color: colors.muted,
    fontWeight: "800"
  },
  preview: {
    alignItems: "center",
    backgroundColor: colors.background,
    justifyContent: "center",
    minHeight: 126,
    overflow: "hidden",
    width: 112
  },
  primaryButton: {
    backgroundColor: colors.accent,
    borderRadius: 8,
    paddingHorizontal: 12,
    paddingVertical: 9
  },
  primaryText: {
    color: colors.background,
    fontWeight: "900"
  },
  progressBar: {
    backgroundColor: colors.accent,
    height: "100%"
  },
  progressTrack: {
    backgroundColor: colors.border,
    borderRadius: 999,
    height: 5,
    marginTop: 10,
    overflow: "hidden"
  },
  secondaryButton: {
    borderColor: colors.border,
    borderRadius: 8,
    borderWidth: 1,
    paddingHorizontal: 12,
    paddingVertical: 9
  },
  secondaryText: {
    color: colors.text,
    fontWeight: "900"
  },
  title: {
    color: colors.text,
    fontSize: 14,
    fontWeight: "900"
  },
  wrap: {
    backgroundColor: colors.surface,
    borderColor: colors.border,
    borderRadius: 8,
    borderWidth: 1,
    flexDirection: "row",
    overflow: "hidden"
  }
});
