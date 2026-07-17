import { ResizeMode, Video } from "expo-av";
import { Image, Pressable, StyleSheet, Text, View } from "react-native";
import { colors } from "../theme/colors";
import { formatFileSize } from "../utils/format";
import { ComposerMediaItem } from "./useComposerMediaQueue";

type Props = {
  items: ComposerMediaItem[];
  onCancel: (id: string) => void;
  onRetry: (id: string) => void;
  onRemove: (id: string) => void;
  onMove: (id: string, direction: -1 | 1) => void;
};

export function ComposerMediaQueue({ items, onCancel, onRetry, onRemove, onMove }: Props) {
  if (!items.length) return null;
  return (
    <View testID="home-composer-media-queue" accessibilityLabel={`${items.length} selected attachments`} accessibilityLiveRegion="polite" style={styles.queue}>
      {items.map((item, index) => {
        const busy = ["validating", "uploading", "processing"].includes(item.progress.stage);
        return (
          <View key={item.id} style={styles.card} accessibilityLabel={`Attachment ${index + 1}, ${item.asset.name}, ${item.progress.message}`}>
            <View style={styles.preview}>
              {item.asset.mediaType === "video" ? (
                <Video source={{ uri: item.asset.uri }} style={styles.media} resizeMode={ResizeMode.COVER} useNativeControls={false} shouldPlay={false} />
              ) : (
                <Image source={{ uri: item.asset.uri }} style={styles.media} resizeMode="cover" />
              )}
              <Text style={styles.order}>{index + 1}</Text>
            </View>
            <View style={styles.copy}>
              <Text style={styles.title} numberOfLines={1}>{item.asset.name}</Text>
              <Text style={[styles.state, item.error ? styles.error : undefined]} numberOfLines={2}>{item.error || item.progress.message}</Text>
              <Text style={styles.meta}>{item.asset.mediaType} · {formatFileSize(item.asset.size)}</Text>
              <View style={styles.track}><View style={[styles.bar, { width: `${Math.max(0, Math.min(100, item.progress.percent))}%` }]} /></View>
              <View style={styles.actions}>
                <QueueAction label="←" accessibilityLabel="Move attachment earlier" disabled={index === 0 || busy} onPress={() => onMove(item.id, -1)} />
                <QueueAction label="→" accessibilityLabel="Move attachment later" disabled={index === items.length - 1 || busy} onPress={() => onMove(item.id, 1)} />
                {busy ? <QueueAction label="Cancel" onPress={() => onCancel(item.id)} /> : null}
                {item.progress.stage === "failed" ? <QueueAction label="Retry" onPress={() => onRetry(item.id)} /> : null}
                <QueueAction label="Remove" disabled={busy} onPress={() => onRemove(item.id)} />
              </View>
            </View>
          </View>
        );
      })}
    </View>
  );
}

function QueueAction({ label, onPress, disabled = false, accessibilityLabel }: { label: string; onPress: () => void; disabled?: boolean; accessibilityLabel?: string }) {
  return (
    <Pressable accessibilityRole="button" accessibilityLabel={accessibilityLabel || label} accessibilityState={{ disabled }} disabled={disabled} style={[styles.action, disabled && styles.disabled]} onPress={onPress}>
      <Text style={styles.actionText}>{label}</Text>
    </Pressable>
  );
}

const styles = StyleSheet.create({
  action: { borderColor: colors.border, borderRadius: 8, borderWidth: 1, minHeight: 44, justifyContent: "center", paddingHorizontal: 10 },
  actions: { flexDirection: "row", flexWrap: "wrap", gap: 6, marginTop: 8 },
  actionText: { color: colors.text, fontSize: 11, fontWeight: "900" },
  bar: { backgroundColor: colors.accent, height: "100%" },
  card: { backgroundColor: colors.surface, borderColor: colors.border, borderRadius: 12, borderWidth: 1, flexDirection: "row", overflow: "hidden" },
  copy: { flex: 1, padding: 10 },
  disabled: { opacity: 0.38 },
  error: { color: colors.danger },
  media: { ...StyleSheet.absoluteFillObject },
  meta: { color: colors.muted, fontSize: 11, marginTop: 3, textTransform: "capitalize" },
  order: { backgroundColor: "rgba(0,0,0,0.72)", borderRadius: 10, color: colors.text, fontSize: 11, fontWeight: "900", left: 6, minWidth: 20, paddingHorizontal: 5, paddingVertical: 3, position: "absolute", textAlign: "center", top: 6 },
  preview: { backgroundColor: colors.background, minHeight: 118, width: 96 },
  queue: { gap: 8, marginTop: 10 },
  state: { color: colors.muted, fontSize: 11, lineHeight: 15, marginTop: 3 },
  title: { color: colors.text, fontSize: 13, fontWeight: "900" },
  track: { backgroundColor: colors.border, borderRadius: 999, height: 5, marginTop: 8, overflow: "hidden" }
});
