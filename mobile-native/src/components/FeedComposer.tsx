import { useEffect, useMemo, useState } from "react";
import { ActivityIndicator, Modal, Pressable, ScrollView, StyleSheet, Text, TextInput, View } from "react-native";
import { createPost, PulsePost } from "../api/feed";
import { createPagePost, listPageIdentities, PageIdentity } from "../api/pages";
import { uploadResultMediaId } from "../media/nativeMediaUpload";
import { MediaUploadPreview } from "../media/MediaUploadPreview";
import { useNativeMediaUpload } from "../media/useNativeMediaUpload";
import { colors } from "../theme/colors";
import { createThemedStyles } from "../theme/themedStyles";

type Props = {
  visible: boolean;
  onClose: () => void;
  onCreated: (post?: PulsePost) => void;
};

const visibilityOptions = [
  { label: "Public", value: "public" },
  { label: "Followers", value: "followers" },
  { label: "Private", value: "private" }
] as const;

export function FeedComposer({ visible, onClose, onCreated }: Props) {
  const uploadOptions = useMemo(() => ({ contextType: "pulse", contextId: "draft" }), []);
  const mediaUpload = useNativeMediaUpload(uploadOptions);
  const [body, setBody] = useState("");
  const [title, setTitle] = useState("");
  const [visibility, setVisibility] = useState<(typeof visibilityOptions)[number]["value"]>("public");
  const [publishing, setPublishing] = useState(false);
  const [error, setError] = useState("");
  // Identity switching (Page OS): every identity the user can post as —
  // personal plus pages where their role allows content creation. The server
  // re-checks the role on publish; this list is a convenience, not authority.
  const [identities, setIdentities] = useState<PageIdentity[]>([]);
  const [postingAs, setPostingAs] = useState<PageIdentity | null>(null);
  const canPublish = Boolean(body.trim() || title.trim() || mediaUpload.asset);

  useEffect(() => {
    if (!visible) return;
    listPageIdentities()
      .then((result) => {
        setIdentities([result.personal, ...result.pages]);
        setPostingAs((current) => current || result.personal);
      })
      .catch(() => {
        // Pages unavailable → personal-only composer, unchanged behavior.
        setIdentities([]);
        setPostingAs(null);
      });
  }, [visible]);

  async function publish() {
    if (!canPublish || publishing) {
      setError("Write something or attach media before publishing.");
      return;
    }
    setPublishing(true);
    setError("");
    try {
      let mediaIds: number[] = [];
      if (mediaUpload.asset) {
        const uploadResult = await mediaUpload.upload({ contextType: "pulse", contextId: "draft" });
        const mediaId = uploadResult ? uploadResultMediaId(uploadResult) : 0;
        if (!mediaId) throw new Error("Upload completed but media did not attach. Please retry.");
        mediaIds = [mediaId];
      }
      const hasVideo = mediaUpload.asset?.mediaType === "video";
      const postType = hasVideo ? "video" : mediaIds.length ? "image" : "text";
      if (postingAs && postingAs.kind === "page") {
        // Page post: same canonical content system, page attribution resolved
        // server-side. The role check happens on the server.
        const pageResult = await createPagePost(postingAs.id, {
          body: body.trim(),
          title: title.trim(),
          post_type: postType,
          visibility,
          media_ids: mediaIds
        });
        if (!pageResult.ok) throw new Error(pageResult.message || "Page post could not publish.");
        resetComposer();
        onCreated(undefined);
        onClose();
        return;
      }
      const result = await createPost({
        body: body.trim(),
        title: title.trim(),
        post_type: postType,
        visibility,
        media_ids: mediaIds
      });
      resetComposer();
      onCreated(result.post);
      onClose();
    } catch (publishError) {
      setError(publishError instanceof Error ? publishError.message : "Post could not publish.");
    } finally {
      setPublishing(false);
    }
  }

  function resetComposer() {
    setBody("");
    setTitle("");
    setVisibility("public");
    setError("");
    mediaUpload.reset();
  }

  function closeComposer() {
    if (!publishing && !mediaUpload.uploading) onClose();
  }

  return (
    <Modal visible={visible} animationType="slide" onRequestClose={closeComposer}>
      <View style={styles.root}>
        <View style={styles.header}>
          <Pressable accessibilityRole="button" accessibilityState={{ disabled: publishing || mediaUpload.uploading }} style={styles.headerButton} disabled={publishing || mediaUpload.uploading} onPress={closeComposer}>
            <Text style={styles.headerButtonText}>Cancel</Text>
          </Pressable>
          <Text style={styles.headerTitle}>Create Post</Text>
          <Pressable accessibilityRole="button" accessibilityState={{ disabled: !canPublish || publishing }} style={[styles.publishButton, (!canPublish || publishing) && styles.disabled]} disabled={!canPublish || publishing} onPress={publish}>
            {publishing ? <ActivityIndicator color={colors.background} /> : <Text style={styles.publishText}>Publish</Text>}
          </Pressable>
        </View>

        <ScrollView contentContainerStyle={styles.content} keyboardShouldPersistTaps="handled">
          {identities.length > 1 ? (
            <View>
              <Text style={styles.postingAsLabel}>
                Posting as {postingAs?.kind === "page" ? postingAs.name : `${postingAs?.name || "yourself"} (personal)`}
              </Text>
              <ScrollView horizontal showsHorizontalScrollIndicator={false} contentContainerStyle={styles.identityRow}>
                {identities.map((identity) => {
                  const active = postingAs?.kind === identity.kind && postingAs?.id === identity.id;
                  return (
                    <Pressable
                      accessibilityRole="button"
                      accessibilityState={{ selected: active }}
                      key={`${identity.kind}-${identity.id}`}
                      style={[styles.identityChip, active && styles.identityChipActive]}
                      onPress={() => setPostingAs(identity)}
                    >
                      <Text style={[styles.identityChipText, active && styles.identityChipTextActive]}>
                        {identity.kind === "page" ? identity.name : `${identity.name} (personal)`}
                      </Text>
                    </Pressable>
                  );
                })}
              </ScrollView>
            </View>
          ) : null}
          <TextInput
            style={styles.titleInput}
            value={title}
            onChangeText={setTitle}
            placeholder="Title"
            placeholderTextColor={colors.muted}
          />
          <TextInput
            style={styles.bodyInput}
            value={body}
            onChangeText={setBody}
            placeholder="What do you want to share?"
            placeholderTextColor={colors.muted}
            multiline
            textAlignVertical="top"
          />

          <View style={styles.selector}>
            {visibilityOptions.map((option) => (
              <Pressable accessibilityRole="button"
                key={option.value}
                style={[styles.visibilityButton, visibility === option.value && styles.visibilityActive]}
                onPress={() => setVisibility(option.value)}
              >
                <Text style={[styles.visibilityText, visibility === option.value && styles.visibilityTextActive]}>{option.label}</Text>
              </Pressable>
            ))}
          </View>

          <View style={styles.mediaActions}>
            <Pressable accessibilityRole="button" accessibilityState={{ disabled: publishing || mediaUpload.uploading }} style={styles.secondaryButton} disabled={publishing || mediaUpload.uploading} onPress={() => mediaUpload.chooseImage()}>
              <Text style={styles.secondaryText}>Image</Text>
            </Pressable>
            <Pressable accessibilityRole="button" accessibilityState={{ disabled: publishing || mediaUpload.uploading }} style={styles.secondaryButton} disabled={publishing || mediaUpload.uploading} onPress={() => mediaUpload.chooseVideo()}>
              <Text style={styles.secondaryText}>Video</Text>
            </Pressable>
            <Pressable accessibilityRole="button" accessibilityState={{ disabled: publishing || mediaUpload.uploading }} style={styles.secondaryButton} disabled={publishing || mediaUpload.uploading} onPress={() => mediaUpload.openCamera("image")}>
              <Text style={styles.secondaryText}>Camera</Text>
            </Pressable>
          </View>

          <MediaUploadPreview
            asset={mediaUpload.asset}
            media={mediaUpload.result?.media}
            progress={mediaUpload.progress}
            error={mediaUpload.error}
            uploading={mediaUpload.uploading}
            onRetry={mediaUpload.retry}
            onCancel={mediaUpload.cancel}
          />

          {error ? <Text style={styles.error}>{error}</Text> : null}
          <Text style={styles.fallbackNote}>Some advanced posting options are only on the PulseSoc website for now.</Text>
        </ScrollView>
      </View>
    </Modal>
  );
}

const styles = createThemedStyles(() => ({
  bodyInput: {
    backgroundColor: colors.surface,
    borderColor: colors.border,
    borderRadius: 8,
    borderWidth: 1,
    color: colors.text,
    fontSize: 17,
    lineHeight: 24,
    minHeight: 150,
    padding: 14
  },
  content: {
    gap: 14,
    padding: 16,
    paddingBottom: 42
  },
  disabled: {
    opacity: 0.5
  },
  error: {
    color: colors.danger,
    fontWeight: "800"
  },
  fallbackNote: {
    color: colors.muted,
    fontSize: 12,
    lineHeight: 18
  },
  header: {
    alignItems: "center",
    backgroundColor: colors.surface,
    borderBottomColor: colors.border,
    borderBottomWidth: 1,
    flexDirection: "row",
    justifyContent: "space-between",
    paddingHorizontal: 14,
    paddingVertical: 12
  },
  headerButton: {
    minWidth: 76,
    paddingVertical: 9
  },
  headerButtonText: {
    color: colors.text,
    fontWeight: "800"
  },
  headerTitle: {
    color: colors.text,
    fontSize: 17,
    fontWeight: "900"
  },
  identityChip: {
    borderColor: colors.border,
    borderRadius: 16,
    borderWidth: 1,
    paddingHorizontal: 12,
    paddingVertical: 7
  },
  identityChipActive: {
    backgroundColor: colors.accent,
    borderColor: colors.accent
  },
  identityChipText: {
    color: colors.text,
    fontSize: 12,
    fontWeight: "800"
  },
  identityChipTextActive: {
    color: colors.background
  },
  identityRow: {
    gap: 8,
    paddingTop: 8
  },
  mediaActions: {
    flexDirection: "row",
    gap: 10
  },
  postingAsLabel: {
    color: colors.accent,
    fontSize: 13,
    fontWeight: "900"
  },
  publishButton: {
    alignItems: "center",
    backgroundColor: colors.accent,
    borderRadius: 8,
    justifyContent: "center",
    minHeight: 38,
    minWidth: 76,
    paddingHorizontal: 12
  },
  publishText: {
    color: colors.background,
    fontWeight: "900"
  },
  root: {
    backgroundColor: colors.background,
    flex: 1
  },
  secondaryButton: {
    borderColor: colors.border,
    borderRadius: 8,
    borderWidth: 1,
    flex: 1,
    paddingVertical: 11
  },
  secondaryText: {
    color: colors.text,
    fontWeight: "900",
    textAlign: "center"
  },
  selector: {
    flexDirection: "row",
    gap: 8
  },
  titleInput: {
    backgroundColor: colors.surface,
    borderColor: colors.border,
    borderRadius: 8,
    borderWidth: 1,
    color: colors.text,
    fontSize: 16,
    padding: 13
  },
  visibilityActive: {
    backgroundColor: colors.accent,
    borderColor: colors.accent
  },
  visibilityButton: {
    borderColor: colors.border,
    borderRadius: 8,
    borderWidth: 1,
    flex: 1,
    paddingVertical: 10
  },
  visibilityText: {
    color: colors.text,
    fontSize: 12,
    fontWeight: "900",
    textAlign: "center"
  },
  visibilityTextActive: {
    color: colors.background
  }
}));
