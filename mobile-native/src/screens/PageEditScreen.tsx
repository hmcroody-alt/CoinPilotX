import { NativeStackScreenProps } from "@react-navigation/native-stack";
import * as ImagePicker from "expo-image-picker";
import { useCallback, useEffect, useRef, useState } from "react";
import {
  ActivityIndicator,
  Image,
  Pressable,
  ScrollView,
  Text,
  TextInput,
  View
} from "react-native";
import {
  checkPageHandle,
  getPageManageView,
  HandleCheck,
  PageManageView,
  updatePage
} from "../api/pages";
import { PulseApiError } from "../api/pulseApi";
import { uploadNativeMedia } from "../media/nativeMediaUpload";
import { RootStackParamList } from "../navigation/types";
import { colors } from "../theme/colors";
import { createThemedStyles } from "../theme/themedStyles";

type Props = NativeStackScreenProps<RootStackParamList, "PageEdit">;

/**
 * Identity editing for a Presence — the surface behind `updatePage`, which
 * until now had no caller anywhere in the app. Every field a page owner could
 * set at creation time was permanently frozen afterwards: a typo in the handle,
 * a placeholder bio, a missing avatar, all unfixable from the native app
 * despite `PATCH /api/pages/:id` having been implemented and audited the whole
 * time.
 *
 * The field list is not invented here. It mirrors exactly what
 * `pulsesoc_pages.update_page` accepts, including its length limits, so the
 * form cannot offer something the server will silently drop — the one failure
 * mode an edit screen must not have, because it looks like a successful save.
 */

/** Server-side limits from `update_page`, mirrored so nothing is typed past them. */
const LIMITS = {
  name: 120,
  category: 80,
  subcategory: 80,
  description: 1000,
  genre: 80,
  email: 200,
  phone: 40,
  website: 300,
  location: 240
};

type Draft = {
  name: string;
  handle: string;
  category: string;
  subcategory: string;
  description: string;
  genre: string;
  email: string;
  phone: string;
  website: string;
  location: string;
};

const EMPTY: Draft = {
  name: "",
  handle: "",
  category: "",
  subcategory: "",
  description: "",
  genre: "",
  email: "",
  phone: "",
  website: "",
  location: ""
};

export function PageEditScreen({ route, navigation }: Props) {
  const pageId = route.params.pageId;
  const [manage, setManage] = useState<PageManageView | null>(null);
  const [draft, setDraft] = useState<Draft>(EMPTY);
  const [initial, setInitial] = useState<Draft>(EMPTY);
  const [avatarUrl, setAvatarUrl] = useState("");
  const [coverUrl, setCoverUrl] = useState("");
  const [handleState, setHandleState] = useState<HandleCheck | null>(null);
  const [checkingHandle, setCheckingHandle] = useState(false);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [uploading, setUploading] = useState("");
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");
  const handleTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  const hydrate = useCallback((view: PageManageView) => {
    const page = view.page;
    const next: Draft = {
      name: page.name || "",
      handle: page.handle || "",
      category: page.category || "",
      subcategory: page.subcategory || "",
      description: page.description || "",
      genre: page.genre || "",
      email: page.email || "",
      // Phone is management-only — it is never in a public payload, so it can
      // only be read back from the manage view.
      phone: view.phone || "",
      website: page.website || "",
      location: page.location || ""
    };
    setManage(view);
    setDraft(next);
    setInitial(next);
    setAvatarUrl(page.avatar_url || "");
    setCoverUrl(page.cover_url || "");
  }, []);

  const load = useCallback(async () => {
    setError("");
    try {
      hydrate(await getPageManageView(pageId));
    } catch (loadError) {
      setError(loadError instanceof PulseApiError ? loadError.message : "This page could not be loaded.");
    } finally {
      setLoading(false);
    }
  }, [pageId, hydrate]);

  useEffect(() => {
    load();
  }, [load]);

  const canEdit = manage ? manage.capabilities.includes("edit_page") : false;
  const handleChanged = draft.handle.trim().toLowerCase() !== initial.handle.trim().toLowerCase();

  /**
   * Only checked when the handle actually changed. Re-checking an unchanged
   * handle is both pointless and — before the server learned to exclude the
   * page being edited — actively wrong.
   */
  useEffect(() => {
    if (handleTimer.current) clearTimeout(handleTimer.current);
    if (!handleChanged) {
      setHandleState(null);
      setCheckingHandle(false);
      return;
    }
    const candidate = draft.handle.trim();
    if (!candidate) {
      setHandleState(null);
      return;
    }
    setCheckingHandle(true);
    handleTimer.current = setTimeout(async () => {
      try {
        setHandleState(await checkPageHandle(candidate, pageId));
      } catch {
        // Fail closed: an unknown state is "not available", never "available".
        setHandleState({
          candidate,
          handle: candidate,
          available: false,
          reason: "That handle couldn't be checked right now. Try again."
        });
      } finally {
        setCheckingHandle(false);
      }
    }, 450);
    return () => {
      if (handleTimer.current) clearTimeout(handleTimer.current);
    };
  }, [draft.handle, handleChanged, pageId]);

  const dirty = (Object.keys(EMPTY) as Array<keyof Draft>).some(
    (key) => draft[key].trim() !== initial[key].trim()
  );
  const nameValid = draft.name.trim().length >= 2;
  const handleReady = !handleChanged || Boolean(handleState?.available);
  const canSave = canEdit && dirty && nameValid && handleReady && !saving && !checkingHandle;

  function set(key: keyof Draft, value: string) {
    setDraft((current) => ({ ...current, [key]: value }));
    setMessage("");
  }

  async function save() {
    if (!canSave) return;
    setSaving(true);
    setError("");
    setMessage("");
    try {
      // Only changed fields are sent. A full-object PATCH would rewrite — and
      // re-audit — values the member never touched.
      const patch: Record<string, string> = {};
      (Object.keys(EMPTY) as Array<keyof Draft>).forEach((key) => {
        if (draft[key].trim() !== initial[key].trim()) patch[key] = draft[key].trim();
      });
      if (patch.handle) patch.handle = patch.handle.replace(/^@+/, "");
      await updatePage(pageId, patch);
      await load();
      setMessage("Page details saved.");
    } catch (saveError) {
      setError(saveError instanceof PulseApiError ? saveError.message : "Those changes could not be saved.");
    } finally {
      setSaving(false);
    }
  }

  /**
   * Goes through the one canonical media pipeline — `uploadNativeMedia` to
   * `/api/pulse/media/upload` — and then PATCHes the URL it returns. No second
   * upload path for pages.
   */
  async function pickImage(kind: "avatar" | "cover") {
    if (!canEdit || uploading) return;
    setError("");
    setMessage("");
    const permission = await ImagePicker.requestMediaLibraryPermissionsAsync();
    if (!permission.granted) {
      setError("PulseSoc needs access to your photos to set this image.");
      return;
    }
    const picked = await ImagePicker.launchImageLibraryAsync({
      allowsEditing: true,
      mediaTypes: ImagePicker.MediaTypeOptions.Images,
      quality: 0.88,
      aspect: kind === "avatar" ? [1, 1] : [16, 6]
    });
    if (picked.canceled || !picked.assets?.[0]) return;
    const asset = picked.assets[0];
    const mimeType = asset.mimeType || "image/jpeg";
    setUploading(kind);
    try {
      const { promise } = uploadNativeMedia(
        {
          uri: asset.uri,
          name: asset.fileName || `page-${kind}.${mimeType.includes("png") ? "png" : "jpg"}`,
          mimeType,
          mediaType: "image",
          size: asset.fileSize,
          width: asset.width,
          height: asset.height
        },
        { contextType: kind === "avatar" ? "pulse_avatar" : "pulse_cover", contextId: `page-${pageId}` }
      );
      const result = await promise;
      const url = result.media_url || result.media?.media_url || "";
      if (!url) throw new Error("The upload finished but returned no image.");
      await updatePage(pageId, kind === "avatar" ? { avatar_url: url } : { cover_url: url });
      await load();
      setMessage(kind === "avatar" ? "Profile picture updated." : "Cover image updated.");
    } catch (uploadError) {
      setError(uploadError instanceof Error ? uploadError.message : "That image could not be uploaded.");
    } finally {
      setUploading("");
    }
  }

  if (loading) {
    return (
      <View style={[styles.root, styles.center]}>
        <ActivityIndicator color={colors.accent} size="large" />
      </View>
    );
  }

  if (!manage) {
    return (
      <View style={[styles.root, styles.center]}>
        <Text style={styles.error}>{error || "This page could not be loaded."}</Text>
      </View>
    );
  }

  /**
   * The server refuses the PATCH for a role without `edit_page`, so this is
   * about telling the member why rather than about enforcement — a form that
   * accepts input and then 403s on save is worse than one that never opened.
   */
  if (!canEdit) {
    return (
      <ScrollView style={styles.root} contentContainerStyle={styles.content}>
        <Text style={styles.sectionTitle}>You can view this page, not edit it</Text>
        <Text style={styles.help}>
          Your role on {manage.page.name} is {manage.role.replace(/_/g, " ").toLowerCase()}. Editing the
          name, handle, images and contact details is limited to owners, admins and managers. An owner can
          change your role from Team &amp; Access.
        </Text>
      </ScrollView>
    );
  }

  return (
    <ScrollView style={styles.root} contentContainerStyle={styles.content} keyboardShouldPersistTaps="handled">
      <Pressable
        accessibilityRole="button"
        accessibilityLabel="Change cover image"
        style={styles.cover}
        disabled={Boolean(uploading)}
        onPress={() => pickImage("cover")}
      >
        {coverUrl ? <Image source={{ uri: coverUrl }} style={styles.coverImage} resizeMode="cover" /> : null}
        <Text style={styles.coverHint}>{uploading === "cover" ? "Uploading…" : coverUrl ? "Change cover" : "Add a cover image"}</Text>
      </Pressable>

      <View style={styles.avatarRow}>
        <Pressable
          accessibilityRole="button"
          accessibilityLabel="Change profile picture"
          disabled={Boolean(uploading)}
          onPress={() => pickImage("avatar")}
        >
          {avatarUrl ? (
            <Image source={{ uri: avatarUrl }} style={styles.avatar} />
          ) : (
            <View style={[styles.avatar, styles.avatarEmpty]}>
              <Text style={styles.avatarEmptyText}>Add</Text>
            </View>
          )}
        </Pressable>
        <View style={styles.avatarCopy}>
          <Text style={styles.avatarTitle}>{uploading === "avatar" ? "Uploading…" : "Profile picture"}</Text>
          <Text style={styles.help}>Shown next to every post, comment and message this page sends.</Text>
        </View>
      </View>

      <Field label="Name" value={draft.name} limit={LIMITS.name} onChange={(value) => set("name", value)} />
      {!nameValid ? <Text style={styles.error}>A page name needs at least two characters.</Text> : null}

      <Text style={styles.label}>Handle</Text>
      <TextInput
        style={styles.input}
        value={draft.handle}
        onChangeText={(value) => set("handle", value.replace(/^@+/, ""))}
        autoCapitalize="none"
        autoCorrect={false}
        maxLength={40}
        placeholder="yourpagename"
        placeholderTextColor={colors.muted}
      />
      {handleChanged ? (
        <Text style={handleState?.available ? styles.handleOk : styles.handleBad}>
          {checkingHandle ? "Checking…" : handleState?.reason || ""}
        </Text>
      ) : (
        <Text style={styles.help}>People find this page at @{initial.handle}. Changing it breaks existing links.</Text>
      )}

      <Field label="Category" value={draft.category} limit={LIMITS.category} onChange={(value) => set("category", value)} />
      <Field label="Subcategory" value={draft.subcategory} limit={LIMITS.subcategory} onChange={(value) => set("subcategory", value)} />
      <Field
        label="Bio"
        value={draft.description}
        limit={LIMITS.description}
        multiline
        onChange={(value) => set("description", value)}
      />
      <Field label="Genre" value={draft.genre} limit={LIMITS.genre} onChange={(value) => set("genre", value)} />
      <Field label="Location" value={draft.location} limit={LIMITS.location} onChange={(value) => set("location", value)} />
      <Field
        label="Website"
        value={draft.website}
        limit={LIMITS.website}
        keyboardType="url"
        onChange={(value) => set("website", value)}
      />
      <Field
        label="Contact email"
        value={draft.email}
        limit={LIMITS.email}
        keyboardType="email-address"
        onChange={(value) => set("email", value)}
      />
      <Field
        label="Contact phone"
        value={draft.phone}
        limit={LIMITS.phone}
        keyboardType="phone-pad"
        onChange={(value) => set("phone", value)}
      />

      {error ? <Text style={styles.error}>{error}</Text> : null}
      {message ? <Text style={styles.message}>{message}</Text> : null}

      <Pressable
        accessibilityRole="button"
        accessibilityState={{ disabled: !canSave }}
        style={[styles.save, !canSave && styles.saveDisabled]}
        disabled={!canSave}
        onPress={save}
      >
        <Text style={styles.saveText}>{saving ? "Saving…" : "Save changes"}</Text>
      </Pressable>
      <Pressable accessibilityRole="button" style={styles.cancel} onPress={() => navigation.goBack()}>
        <Text style={styles.cancelText}>{dirty ? "Discard changes" : "Done"}</Text>
      </Pressable>
      <Text style={styles.help}>Every change to a page is recorded in its history, with who made it and when.</Text>
    </ScrollView>
  );
}

function Field(props: {
  label: string;
  value: string;
  limit: number;
  multiline?: boolean;
  keyboardType?: "url" | "email-address" | "phone-pad";
  onChange: (value: string) => void;
}) {
  return (
    <View>
      <Text style={styles.label}>{props.label}</Text>
      <TextInput
        style={[styles.input, props.multiline && styles.inputMultiline]}
        value={props.value}
        onChangeText={props.onChange}
        maxLength={props.limit}
        multiline={props.multiline}
        keyboardType={props.keyboardType}
        autoCapitalize={props.keyboardType ? "none" : "sentences"}
        autoCorrect={!props.keyboardType}
        placeholderTextColor={colors.muted}
      />
    </View>
  );
}

const styles = createThemedStyles(() => ({
  avatar: {
    borderRadius: 34,
    height: 68,
    width: 68
  },
  avatarCopy: {
    flex: 1,
    gap: 2
  },
  avatarEmpty: {
    alignItems: "center",
    backgroundColor: colors.surface,
    borderColor: colors.border,
    borderWidth: 1,
    justifyContent: "center"
  },
  avatarEmptyText: {
    color: colors.muted,
    fontSize: 12,
    fontWeight: "800"
  },
  avatarRow: {
    alignItems: "center",
    flexDirection: "row",
    gap: 12,
    marginTop: 12
  },
  avatarTitle: {
    color: colors.text,
    fontSize: 14,
    fontWeight: "800"
  },
  cancel: {
    alignItems: "center",
    paddingVertical: 12
  },
  cancelText: {
    color: colors.muted,
    fontSize: 13,
    fontWeight: "700"
  },
  center: {
    alignItems: "center",
    justifyContent: "center"
  },
  content: {
    gap: 6,
    padding: 16,
    paddingBottom: 56
  },
  cover: {
    alignItems: "center",
    backgroundColor: colors.surface,
    borderColor: colors.border,
    borderRadius: 12,
    borderWidth: 1,
    height: 120,
    justifyContent: "center",
    overflow: "hidden"
  },
  coverHint: {
    color: colors.text,
    fontSize: 12,
    fontWeight: "800"
  },
  coverImage: {
    height: "100%",
    position: "absolute",
    width: "100%"
  },
  error: {
    color: colors.danger,
    fontSize: 13,
    marginTop: 6
  },
  handleBad: {
    color: colors.danger,
    fontSize: 12,
    marginTop: 4
  },
  handleOk: {
    color: colors.accent,
    fontSize: 12,
    marginTop: 4
  },
  help: {
    color: colors.muted,
    fontSize: 12,
    marginTop: 4
  },
  input: {
    backgroundColor: colors.surface,
    borderColor: colors.border,
    borderRadius: 10,
    borderWidth: 1,
    color: colors.text,
    fontSize: 14,
    paddingHorizontal: 12,
    paddingVertical: 10
  },
  inputMultiline: {
    minHeight: 96,
    textAlignVertical: "top"
  },
  label: {
    color: colors.text,
    fontSize: 13,
    fontWeight: "800",
    marginTop: 14
  },
  message: {
    color: colors.accent,
    fontSize: 13,
    marginTop: 6
  },
  root: {
    backgroundColor: colors.background,
    flex: 1
  },
  save: {
    alignItems: "center",
    backgroundColor: colors.accent,
    borderRadius: 10,
    marginTop: 18,
    paddingVertical: 14
  },
  saveDisabled: {
    opacity: 0.45
  },
  saveText: {
    color: colors.background,
    fontSize: 14,
    fontWeight: "900"
  },
  sectionTitle: {
    color: colors.text,
    fontSize: 16,
    fontWeight: "900"
  }
}));
