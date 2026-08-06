import { useCallback, useMemo, useRef, useState } from "react";
import { ActivityIndicator, Pressable, StyleSheet, Text, View } from "react-native";
import { useSafeAreaInsets } from "react-native-safe-area-context";
import { ContentPreviewRenderer } from "../components/preview/ContentPreviewRenderer";
import { draftToContentModel, PreviewContent } from "../create/draftToContentModel";
import { clearPreviewHandoff, peekPreviewHandoff } from "../create/previewHandoff";
import { colors } from "../theme/colors";
import { createThemedStyles } from "../theme/themedStyles";

type Props = {
  route: { params?: { token?: string; title?: string } };
  navigation: { goBack: () => void; navigate: (name: string, params?: Record<string, unknown>) => void };
};

const CONTENT_LABEL: Record<PreviewContent["kind"], string> = {
  post: "Feed post",
  reel: "Reel",
  status: "Status"
};

export function ContentPreviewScreen({ route, navigation }: Props) {
  const insets = useSafeAreaInsets();
  const token = route.params?.token || "";
  const handoff = useMemo(() => (token ? peekPreviewHandoff(token) : null), [token]);
  const content = useMemo<PreviewContent | null>(() => (handoff ? draftToContentModel(handoff.draft) : null), [handoff]);

  const [muted, setMuted] = useState(false);
  const [cleanPreview, setCleanPreview] = useState(false);
  const [publishing, setPublishing] = useState(false);
  const [error, setError] = useState("");
  const publishingRef = useRef(false);

  const meta = useMemo(() => describeContent(content, handoff?.draft.visibility), [content, handoff]);

  const handleEdit = useCallback(() => {
    // Return to the composer WITHOUT publishing. Draft state is owned by the
    // composer and untouched, so nothing is lost. Handoff is left in place so
    // a re-open reuses it; it is cleared on successful publish.
    navigation.goBack();
  }, [navigation]);

  const handlePublish = useCallback(async () => {
    if (!handoff || !content) return;
    // Duplicate-submit guard: ignore taps while a publish is in flight.
    if (publishingRef.current) return;
    publishingRef.current = true;
    setPublishing(true);
    setError("");
    try {
      const result = await handoff.publish();
      if (result.ok) {
        // Only dismiss on a genuine, server-confirmed success. The handoff is
        // consumed so it cannot be replayed.
        clearPreviewHandoff(token);
        navigation.goBack();
        return;
      }
      // Failure: preserve the draft, stay on the preview, surface the reason.
      setError(result.message || "Publish failed. Your draft is preserved.");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Publish failed. Your draft is preserved.");
    } finally {
      publishingRef.current = false;
      setPublishing(false);
    }
  }, [content, handoff, navigation, token]);

  if (!handoff || !content) {
    return (
      <View style={[styles.container, styles.center]}>
        <Text style={styles.emptyTitle}>Preview unavailable</Text>
        <Text style={styles.emptyBody}>This draft is no longer available to preview. Return to the composer and try again.</Text>
        <Pressable
          style={[styles.secondaryButton, { marginTop: 20 }]}
          onPress={() => navigation.goBack()}
          accessibilityRole="button"
          accessibilityLabel="Back to composer"
        >
          <Text style={styles.secondaryLabel}>Back to composer</Text>
        </Pressable>
      </View>
    );
  }

  return (
    <View style={styles.container}>
      <View style={styles.stage}>
        <ContentPreviewRenderer content={content} active muted={muted} onToggleMuted={() => setMuted((current) => !current)} />
      </View>

      {!cleanPreview && (
        <View style={[styles.topBar, { paddingTop: insets.top + 8 }]} pointerEvents="box-none">
          <View style={styles.badge} accessibilityRole="header">
            <Text style={styles.badgeText}>{`Preview · ${CONTENT_LABEL[content.kind]}`}</Text>
          </View>
          <Text style={styles.metaText} accessibilityLabel={`Preview details: ${meta}`}>
            {meta}
          </Text>
        </View>
      )}

      <View style={[styles.bottomBar, { paddingBottom: insets.bottom + 16 }]}>
        {error ? (
          <Text style={styles.errorText} accessibilityLiveRegion="polite">
            {error}
          </Text>
        ) : (
          <Text style={styles.hintText}>What you see here is exactly how it publishes.</Text>
        )}
        <View style={styles.controlsRow}>
          <Pressable
            style={styles.toggle}
            onPress={() => setCleanPreview((current) => !current)}
            accessibilityRole="switch"
            accessibilityState={{ checked: cleanPreview }}
            accessibilityLabel={cleanPreview ? "Show preview details" : "Hide preview details for a clean preview"}
          >
            <Text style={styles.toggleText}>{cleanPreview ? "Show details" : "Clean preview"}</Text>
          </Pressable>
          <View style={styles.actionGroup}>
            <Pressable
              style={[styles.secondaryButton, publishing && styles.buttonDisabled]}
              onPress={handleEdit}
              disabled={publishing}
              accessibilityRole="button"
              accessibilityLabel="Edit — return to composer without publishing"
            >
              <Text style={styles.secondaryLabel}>Edit</Text>
            </Pressable>
            <Pressable
              style={[styles.primaryButton, publishing && styles.buttonDisabled]}
              onPress={handlePublish}
              disabled={publishing}
              accessibilityRole="button"
              accessibilityState={{ disabled: publishing, busy: publishing }}
              accessibilityLabel={publishing ? "Publishing" : `Publish ${CONTENT_LABEL[content.kind]}`}
            >
              {publishing ? (
                <View style={styles.busyRow}>
                  <ActivityIndicator size="small" color={colors.background} />
                  <Text style={styles.primaryLabel}>Publishing…</Text>
                </View>
              ) : (
                <Text style={styles.primaryLabel}>{content.kind === "post" ? "Post" : "Publish"}</Text>
              )}
            </Pressable>
          </View>
        </View>
      </View>
    </View>
  );
}

function describeContent(content: PreviewContent | null, visibility?: string): string {
  if (!content) return "";
  const parts: string[] = [];
  parts.push(`Audience: ${capitalize(visibility || "public")}`);
  if (content.kind === "reel") {
    const clips = content.reel.media?.length || 0;
    parts.push(clips ? `${clips} video ${clips === 1 ? "clip" : "clips"}` : "No media");
    if (content.reel.audio?.title) parts.push(`Music: ${content.reel.audio.title}`);
  } else if (content.kind === "status") {
    parts.push(`Type: ${capitalize(content.status.status_type || "text")}`);
    if (content.status.music?.title || content.status.music?.audio_title) {
      parts.push(`Music: ${content.status.music.title || content.status.music.audio_title}`);
    }
  } else {
    const mediaCount = content.post.media?.length || 0;
    parts.push(mediaCount ? `${mediaCount} ${mediaCount === 1 ? "attachment" : "attachments"}` : "Text only");
  }
  return parts.join("  ·  ");
}

function capitalize(value: string) {
  return value ? value.charAt(0).toUpperCase() + value.slice(1) : value;
}

const styles = createThemedStyles(() => ({
  container: { flex: 1, backgroundColor: colors.background },
  stage: { flex: 1 },
  center: { alignItems: "center", justifyContent: "center", padding: 24 },
  topBar: { position: "absolute", top: 0, left: 0, right: 0, paddingHorizontal: 16, gap: 6 },
  badge: { alignSelf: "flex-start", backgroundColor: "rgba(0,0,0,0.55)", borderRadius: 999, paddingHorizontal: 12, paddingVertical: 6 },
  badgeText: { color: "#fff", fontSize: 12, fontWeight: "700", letterSpacing: 0.4 },
  metaText: { color: "rgba(255,255,255,0.85)", fontSize: 12, backgroundColor: "rgba(0,0,0,0.45)", alignSelf: "flex-start", borderRadius: 8, paddingHorizontal: 8, paddingVertical: 4 },
  bottomBar: { paddingHorizontal: 16, paddingTop: 14, backgroundColor: "rgba(0,0,0,0.35)", gap: 10 },
  hintText: { color: "rgba(255,255,255,0.7)", fontSize: 12 },
  errorText: { color: "#ff8a8a", fontSize: 13, fontWeight: "600" },
  controlsRow: { flexDirection: "row", alignItems: "center", justifyContent: "space-between" },
  actionGroup: { flexDirection: "row", alignItems: "center", gap: 10 },
  toggle: { paddingVertical: 10, paddingHorizontal: 12 },
  toggleText: { color: "rgba(255,255,255,0.85)", fontSize: 13, fontWeight: "600" },
  primaryButton: { backgroundColor: colors.accent, borderRadius: 999, paddingHorizontal: 22, paddingVertical: 12, minWidth: 104, alignItems: "center" },
  primaryLabel: { color: colors.background, fontSize: 15, fontWeight: "800" },
  secondaryButton: { borderRadius: 999, paddingHorizontal: 18, paddingVertical: 12, borderWidth: 1, borderColor: "rgba(255,255,255,0.4)" },
  secondaryLabel: { color: "#fff", fontSize: 15, fontWeight: "700" },
  buttonDisabled: { opacity: 0.5 },
  busyRow: { flexDirection: "row", alignItems: "center", gap: 8 },
  emptyTitle: { color: "#fff", fontSize: 18, fontWeight: "800" },
  emptyBody: { color: "rgba(255,255,255,0.7)", fontSize: 14, textAlign: "center", marginTop: 8 }
}));
