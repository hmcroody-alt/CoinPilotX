import * as ImagePicker from "expo-image-picker";
import { useEffect, useState } from "react";
import { ActivityIndicator, Image, Pressable, ScrollView, StyleSheet, Text, TextInput, View } from "react-native";
import {
  getMyProfile,
  PulseProfile,
  PulseProfileTheme,
  removeProfileAvatar,
  removeProfileCover,
  updateProfile,
  updateProfileTheme,
  uploadProfileAvatar,
  uploadProfileCover
} from "../api/profile";
import { colors } from "../theme/colors";
import { createThemedStyles } from "../theme/themedStyles";

const THEMES: Array<PulseProfileTheme & { label: string }> = [
  { theme_key: "deep_space", label: "Deep Space", accent_color: "#32e6b3" },
  { theme_key: "neon_galaxy", label: "Neon Galaxy", accent_color: "#d95cff" },
  { theme_key: "cyber_city", label: "Cyber City", accent_color: "#32c8ff" },
  { theme_key: "solar_pulse", label: "Solar Pulse", accent_color: "#ff9f43" },
  { theme_key: "aurora", label: "Aurora", accent_color: "#72f6a8" },
  { theme_key: "quantum", label: "Quantum", accent_color: "#8f7cff" },
  { theme_key: "crystal", label: "Crystal", accent_color: "#8df7ff" },
  { theme_key: "dark_matter", label: "Dark Matter", accent_color: "#9f7cff" },
  { theme_key: "nova", label: "Nova", accent_color: "#ff5f7e" },
  { theme_key: "minimal_black", label: "Minimal Black", accent_color: "#d7e2ea" }
];
const LAYOUTS = ["classic", "creator", "professional", "minimal", "artist", "music", "gaming", "developer", "business", "streamer"];

export function ProfileEditScreen() {
  const [profile, setProfile] = useState<PulseProfile | null>(null);
  const [displayName, setDisplayName] = useState("");
  const [username, setUsername] = useState("");
  const [bio, setBio] = useState("");
  const [links, setLinks] = useState("");
  const [expertise, setExpertise] = useState("");
  const [visibility, setVisibility] = useState<"public" | "private">("public");
  const [theme, setTheme] = useState<PulseProfileTheme>(THEMES[0]);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [uploading, setUploading] = useState("");
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");

  async function load() {
    setError("");
    setLoading(true);
    try {
      hydrate(await getMyProfile());
    } catch (loadError) {
      setError(loadError instanceof Error ? loadError.message : "Profile could not load.");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    load().catch(() => undefined);
  }, []);

  function hydrate(next: PulseProfile) {
    setProfile(next);
    setDisplayName(next.display_name || "");
    setUsername(next.username || "");
    setBio(next.bio || "");
    setLinks(next.social_links || next.social_links_json || "");
    setExpertise(next.expertise_tags || next.expertise_tags_json || "");
    setVisibility(next.profile_visibility || "public");
    setTheme(next.theme?.theme_key ? next.theme : THEMES[0]);
  }

  async function save() {
    const cleanName = displayName.trim();
    if (!cleanName) {
      setError("Display name is required.");
      return;
    }
    setSaving(true);
    setError("");
    setMessage("");
    try {
      const updated = await updateProfile({
        display_name: cleanName,
        username: username.trim().replace(/^@/, ""),
        bio: bio.trim(),
        social_links: links.trim(),
        expertise_tags: expertise.trim(),
        profile_visibility: visibility
      });
      let savedTheme = theme;
      try {
        savedTheme = await updateProfileTheme(theme);
      } catch (themeError) {
        setMessage(themeError instanceof Error ? themeError.message : "Profile saved. Theme was not updated.");
      }
      hydrate({ ...updated, theme: savedTheme });
      setMessage((current) => current || "Profile updated.");
    } catch (saveError) {
      setError(saveError instanceof Error ? saveError.message : "Profile update failed.");
    } finally {
      setSaving(false);
    }
  }

  async function pickImage(kind: "avatar" | "cover") {
    setError("");
    setMessage("");
    const permission = await ImagePicker.requestMediaLibraryPermissionsAsync();
    if (!permission.granted) {
      setError("Photo permission was not granted.");
      return;
    }
    const result = await ImagePicker.launchImageLibraryAsync({
      allowsEditing: true,
      mediaTypes: ImagePicker.MediaTypeOptions.Images,
      quality: 0.88,
      aspect: kind === "avatar" ? [1, 1] : [16, 6]
    });
    if (result.canceled || !result.assets?.[0]) return;
    const asset = result.assets[0];
    const mimeType = asset.mimeType || "image/jpeg";
    const name = asset.fileName || `${kind}.${mimeType.includes("png") ? "png" : "jpg"}`;
    setUploading(kind);
    try {
      const next = kind === "avatar"
        ? await uploadProfileAvatar({ uri: asset.uri, name, mimeType })
        : await uploadProfileCover({ uri: asset.uri, name, mimeType });
      hydrate(next);
      setMessage(kind === "avatar" ? "Profile picture updated." : "Cover photo updated.");
    } catch (uploadError) {
      setError(uploadError instanceof Error ? uploadError.message : "Upload failed.");
    } finally {
      setUploading("");
    }
  }

  async function removeImage(kind: "avatar" | "cover") {
    setUploading(kind);
    setError("");
    setMessage("");
    try {
      const next = kind === "avatar" ? await removeProfileAvatar() : await removeProfileCover();
      hydrate(next);
      setMessage(kind === "avatar" ? "Profile picture removed." : "Cover photo removed.");
    } catch (removeError) {
      setError(removeError instanceof Error ? removeError.message : "Remove failed.");
    } finally {
      setUploading("");
    }
  }

  if (loading) {
    return (
      <View style={styles.center}>
        <ActivityIndicator color={colors.accent} />
      </View>
    );
  }

  return (
    <ScrollView style={styles.root} contentContainerStyle={styles.content} keyboardShouldPersistTaps="handled">
      <View style={styles.mediaCard}>
        <View style={styles.cover}>
          {profile?.cover_url ? <Image source={{ uri: profile.cover_url }} style={styles.coverImage} resizeMode="cover" /> : null}
        </View>
        <View style={styles.avatarRow}>
          {profile?.avatar_url ? <Image source={{ uri: profile.avatar_url }} style={styles.avatar} /> : <View style={styles.avatarFallback} />}
          <View style={styles.mediaActions}>
            <Pressable accessibilityRole="button" accessibilityState={{ disabled: Boolean(uploading) }} style={styles.smallButton} disabled={Boolean(uploading)} onPress={() => pickImage("avatar")}>
              <Text style={styles.smallButtonText}>{uploading === "avatar" ? "Uploading" : "Avatar"}</Text>
            </Pressable>
            <Pressable accessibilityRole="button" accessibilityState={{ disabled: Boolean(uploading) }} style={styles.smallButton} disabled={Boolean(uploading)} onPress={() => pickImage("cover")}>
              <Text style={styles.smallButtonText}>{uploading === "cover" ? "Uploading" : "Cover"}</Text>
            </Pressable>
          </View>
        </View>
        <View style={styles.removeRow}>
          <Pressable accessibilityRole="button" accessibilityState={{ disabled: Boolean(uploading) }} style={styles.removeButton} disabled={Boolean(uploading)} onPress={() => removeImage("avatar")}>
            <Text style={styles.removeText}>Remove Avatar</Text>
          </Pressable>
          <Pressable accessibilityRole="button" accessibilityState={{ disabled: Boolean(uploading) }} style={styles.removeButton} disabled={Boolean(uploading)} onPress={() => removeImage("cover")}>
            <Text style={styles.removeText}>Remove Cover</Text>
          </Pressable>
        </View>
      </View>

      <View style={styles.formCard}>
        {error ? <Text style={styles.error}>{error}</Text> : null}
        {message ? <Text style={styles.message}>{message}</Text> : null}
        <Field label="Display name" value={displayName} onChangeText={setDisplayName} />
        <Field label="Username" value={username} onChangeText={setUsername} autoCapitalize="none" />
        <Field label="Links" value={links} onChangeText={setLinks} autoCapitalize="none" />
        <Field label="Expertise" value={expertise} onChangeText={setExpertise} />
        <Text style={styles.label}>Bio</Text>
        <TextInput
          style={[styles.input, styles.bio]}
          value={bio}
          onChangeText={setBio}
          multiline
          maxLength={500}
          placeholder="Bio"
          placeholderTextColor={colors.muted}
        />
        <Text style={styles.label}>Privacy</Text>
        <View style={styles.segment}>
          <Segment label="Public" active={visibility === "public"} onPress={() => setVisibility("public")} />
          <Segment label="Private" active={visibility === "private"} onPress={() => setVisibility("private")} />
        </View>
        <Text style={styles.sectionTitle}>Living identity</Text>
        <Text style={styles.sectionCopy}>Choose an atmospheric theme, layout, and motion level. Your canonical profile data never changes.</Text>
        <Text style={styles.label}>Profile theme</Text>
        <View style={styles.themeRow}>
          {THEMES.map((item) => (
            <Pressable accessibilityRole="button" accessibilityState={{ selected: theme.theme_key === item.theme_key }} key={item.theme_key} style={[styles.theme, theme.theme_key === item.theme_key ? styles.themeActive : undefined]} onPress={() => setTheme((current) => ({ ...current, theme_key: item.theme_key, accent_color: item.accent_color }))}>
              <View style={[styles.swatch, { backgroundColor: item.accent_color }]} />
              <Text style={styles.themeText}>{item.label}</Text>
            </Pressable>
          ))}
        </View>
        <Text style={styles.label}>Layout style</Text>
        <View style={styles.themeRow}>
          {LAYOUTS.map((item) => <Pressable accessibilityRole="button" accessibilityState={{ selected: theme.layout_key === item }} key={item} style={[styles.layoutChoice, theme.layout_key === item && styles.themeActive]} onPress={() => setTheme((current) => ({ ...current, layout_key: item }))}><Text style={styles.themeText}>{item.replace(/_/g, " ")}</Text></Pressable>)}
        </View>
        <Text style={styles.label}>Motion</Text>
        <View style={styles.segment}>
          {(["subtle", "balanced", "reduced"] as const).map((item) => <Segment key={item} label={item[0].toUpperCase() + item.slice(1)} active={(theme.motion_level || "balanced") === item} onPress={() => setTheme((current) => ({ ...current, motion_level: item }))} />)}
        </View>
        <View style={styles.saveRow}>
          <Pressable accessibilityRole="button" accessibilityState={{ disabled: saving }} style={[styles.saveButton, saving && styles.disabled]} disabled={saving} onPress={save}>
            <Text style={styles.saveText}>{saving ? "Saving" : "Save"}</Text>
          </Pressable>
          <Pressable accessibilityRole="button" accessibilityState={{ disabled: saving }} style={styles.cancelButton} disabled={saving} onPress={() => profile && hydrate(profile)}>
            <Text style={styles.cancelText}>Cancel</Text>
          </Pressable>
        </View>
      </View>
    </ScrollView>
  );
}

function Field(props: { label: string; value: string; onChangeText: (value: string) => void; autoCapitalize?: "none" | "sentences" | "words" | "characters" }) {
  return (
    <View>
      <Text style={styles.label}>{props.label}</Text>
      <TextInput
        style={styles.input}
        value={props.value}
        onChangeText={props.onChangeText}
        autoCapitalize={props.autoCapitalize}
        placeholder={props.label}
        placeholderTextColor={colors.muted}
      />
    </View>
  );
}

function Segment({ label, active, onPress }: { label: string; active: boolean; onPress: () => void }) {
  return (
    <Pressable accessibilityRole="button" style={[styles.segmentButton, active ? styles.segmentActive : undefined]} onPress={onPress}>
      <Text style={[styles.segmentText, active ? styles.segmentTextActive : undefined]}>{label}</Text>
    </Pressable>
  );
}

const styles = createThemedStyles(() => ({
  avatar: {
    backgroundColor: colors.surfaceRaised,
    borderColor: colors.background,
    borderRadius: 36,
    borderWidth: 3,
    height: 72,
    width: 72
  },
  avatarFallback: {
    backgroundColor: colors.accentStrong,
    borderColor: colors.background,
    borderRadius: 36,
    borderWidth: 3,
    height: 72,
    width: 72
  },
  avatarRow: {
    alignItems: "center",
    flexDirection: "row",
    gap: 12,
    marginTop: -30,
    paddingHorizontal: 14
  },
  bio: {
    minHeight: 110,
    paddingTop: 12,
    textAlignVertical: "top"
  },
  cancelButton: {
    alignItems: "center",
    borderColor: colors.border,
    borderRadius: 8,
    borderWidth: 1,
    minHeight: 44,
    justifyContent: "center",
    paddingHorizontal: 18
  },
  cancelText: {
    color: colors.text,
    fontWeight: "900"
  },
  center: {
    alignItems: "center",
    backgroundColor: colors.background,
    flex: 1,
    justifyContent: "center"
  },
  content: {
    gap: 14,
    padding: 16,
    paddingBottom: 32
  },
  cover: {
    backgroundColor: colors.surfaceRaised,
    height: 136
  },
  coverImage: {
    height: "100%",
    width: "100%"
  },
  disabled: {
    opacity: 0.55
  },
  error: {
    color: colors.danger,
    fontSize: 13
  },
  formCard: {
    backgroundColor: colors.surface,
    borderColor: colors.border,
    borderRadius: 8,
    borderWidth: 1,
    gap: 12,
    padding: 14
  },
  input: {
    backgroundColor: colors.surfaceRaised,
    borderColor: colors.border,
    borderRadius: 8,
    borderWidth: 1,
    color: colors.text,
    fontSize: 15,
    minHeight: 44,
    paddingHorizontal: 12
  },
  label: {
    color: colors.muted,
    fontSize: 12,
    fontWeight: "900",
    marginBottom: 6,
    textTransform: "uppercase"
  },
  layoutChoice: {
    alignItems: "center",
    borderColor: colors.border,
    borderRadius: 8,
    borderWidth: 1,
    minHeight: 42,
    justifyContent: "center",
    paddingHorizontal: 10
  },
  mediaActions: {
    flex: 1,
    flexDirection: "row",
    gap: 8
  },
  mediaCard: {
    backgroundColor: colors.surface,
    borderColor: colors.border,
    borderRadius: 8,
    borderWidth: 1,
    overflow: "hidden",
    paddingBottom: 14
  },
  message: {
    color: colors.accent,
    fontSize: 13
  },
  removeButton: {
    paddingVertical: 6
  },
  removeRow: {
    flexDirection: "row",
    gap: 18,
    paddingHorizontal: 14,
    paddingTop: 12
  },
  removeText: {
    color: colors.warning,
    fontSize: 12,
    fontWeight: "900"
  },
  root: {
    backgroundColor: colors.background,
    flex: 1
  },
  sectionCopy: {
    color: colors.muted,
    fontSize: 13,
    lineHeight: 19
  },
  sectionTitle: {
    color: colors.text,
    fontSize: 20,
    fontWeight: "900"
  },
  saveButton: {
    alignItems: "center",
    backgroundColor: colors.accent,
    borderRadius: 8,
    flex: 1,
    minHeight: 44,
    justifyContent: "center"
  },
  saveRow: {
    flexDirection: "row",
    gap: 10,
    marginTop: 4
  },
  saveText: {
    color: colors.background,
    fontWeight: "900"
  },
  segment: {
    flexDirection: "row",
    gap: 8
  },
  segmentActive: {
    backgroundColor: "rgba(37, 208, 167, 0.14)",
    borderColor: colors.accent
  },
  segmentButton: {
    alignItems: "center",
    borderColor: colors.border,
    borderRadius: 8,
    borderWidth: 1,
    flex: 1,
    minHeight: 40,
    justifyContent: "center"
  },
  segmentText: {
    color: colors.muted,
    fontWeight: "900"
  },
  segmentTextActive: {
    color: colors.accent
  },
  smallButton: {
    alignItems: "center",
    backgroundColor: colors.surfaceRaised,
    borderColor: colors.border,
    borderRadius: 8,
    borderWidth: 1,
    flex: 1,
    minHeight: 42,
    justifyContent: "center"
  },
  smallButtonText: {
    color: colors.text,
    fontSize: 13,
    fontWeight: "900"
  },
  swatch: {
    borderRadius: 8,
    height: 18,
    width: 18
  },
  theme: {
    alignItems: "center",
    borderColor: colors.border,
    borderRadius: 8,
    borderWidth: 1,
    flexDirection: "row",
    gap: 8,
    minHeight: 42,
    paddingHorizontal: 10
  },
  themeActive: {
    borderColor: colors.accent
  },
  themeRow: {
    gap: 8
  },
  themeText: {
    color: colors.text,
    fontSize: 13,
    fontWeight: "800",
    textTransform: "capitalize"
  }
}));
