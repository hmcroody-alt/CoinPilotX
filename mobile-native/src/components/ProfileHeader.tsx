import { Image, Pressable, Share, StyleSheet, Text, View } from "react-native";
import { PulseProfile, profileWebUrl } from "../api/profile";
import { colors } from "../theme/colors";

type ProfileHeaderProps = {
  profile: PulseProfile;
  publicKey?: string;
  owner?: boolean;
  onEdit?: () => void;
  onRefresh?: () => void;
};

export function ProfileHeader({ profile, publicKey, owner, onEdit, onRefresh }: ProfileHeaderProps) {
  const handle = profile.username || profile.public_player_id || publicKey || "";
  const premium = Boolean(profile.premium_status && profile.premium_status !== "inactive");
  const verified = Boolean(profile.verified_badge || profile.verification_status === "verified");

  return (
    <View style={styles.card}>
      <View style={[styles.cover, profile.theme?.accent_color ? { borderBottomColor: profile.theme.accent_color } : undefined]}>
        {profile.cover_url ? <Image source={{ uri: profile.cover_url }} style={styles.coverImage} resizeMode="cover" /> : null}
      </View>
      <View style={styles.identity}>
        {profile.avatar_url ? (
          <Image source={{ uri: profile.avatar_url }} style={styles.avatar} />
        ) : (
          <View style={styles.avatarFallback}>
            <Text style={styles.avatarText}>{profile.display_name.slice(0, 1).toUpperCase()}</Text>
          </View>
        )}
        <View style={styles.copy}>
          <Text style={styles.name} numberOfLines={1}>{profile.display_name}</Text>
          <Text style={styles.handle} numberOfLines={1}>{handle ? `@${handle}` : "PulseSoc profile"}</Text>
          <View style={styles.badges}>
            {verified ? <Text style={styles.badge}>Verified</Text> : null}
            {premium ? <Text style={styles.badge}>Premium</Text> : null}
            {profile.profile_visibility === "private" ? <Text style={styles.badge}>Private</Text> : null}
            {profile.theme?.theme_key ? <Text style={styles.badge}>{profile.theme.theme_key.replace(/_/g, " ")}</Text> : null}
          </View>
        </View>
      </View>
      {profile.bio ? <Text style={styles.bio}>{profile.bio}</Text> : <Text style={styles.bioMuted}>No bio yet.</Text>}
      <View style={styles.stats}>
        <Stat label="Posts" value={profile.post_count || 0} />
        <Stat label="Followers" value={profile.follower_count || 0} />
        <Stat label="Following" value={profile.following_count || 0} />
        <Stat label="Media" value={profile.media_count || 0} />
      </View>
      <View style={styles.actions}>
        {owner ? (
          <Pressable style={styles.primaryButton} onPress={onEdit}>
            <Text style={styles.primaryText}>Edit Profile</Text>
          </Pressable>
        ) : (
          <Pressable style={styles.primaryButton} onPress={() => Share.share({ message: profileWebUrl(publicKey) }).catch(() => undefined)}>
            <Text style={styles.primaryText}>Share Profile</Text>
          </Pressable>
        )}
        <Pressable style={styles.secondaryButton} onPress={onRefresh}>
          <Text style={styles.secondaryText}>Refresh</Text>
        </Pressable>
      </View>
    </View>
  );
}

function Stat({ label, value }: { label: string; value: number }) {
  return (
    <View style={styles.stat}>
      <Text style={styles.statValue}>{value}</Text>
      <Text style={styles.statLabel}>{label}</Text>
    </View>
  );
}

const styles = StyleSheet.create({
  actions: {
    flexDirection: "row",
    gap: 10,
    marginTop: 14
  },
  avatar: {
    backgroundColor: colors.surfaceRaised,
    borderColor: colors.background,
    borderRadius: 36,
    borderWidth: 3,
    height: 72,
    width: 72
  },
  avatarFallback: {
    alignItems: "center",
    backgroundColor: colors.accentStrong,
    borderColor: colors.background,
    borderRadius: 36,
    borderWidth: 3,
    height: 72,
    justifyContent: "center",
    width: 72
  },
  avatarText: {
    color: colors.text,
    fontSize: 28,
    fontWeight: "900"
  },
  badge: {
    backgroundColor: "rgba(37, 208, 167, 0.14)",
    borderColor: colors.accent,
    borderRadius: 8,
    borderWidth: 1,
    color: colors.accent,
    fontSize: 11,
    fontWeight: "900",
    paddingHorizontal: 8,
    paddingVertical: 4,
    textTransform: "capitalize"
  },
  badges: {
    flexDirection: "row",
    flexWrap: "wrap",
    gap: 6,
    marginTop: 7
  },
  bio: {
    color: colors.text,
    fontSize: 15,
    lineHeight: 22,
    marginTop: 12
  },
  bioMuted: {
    color: colors.muted,
    fontSize: 15,
    lineHeight: 22,
    marginTop: 12
  },
  card: {
    backgroundColor: colors.surface,
    borderColor: colors.border,
    borderRadius: 8,
    borderWidth: 1,
    overflow: "hidden",
    paddingBottom: 14
  },
  copy: {
    flex: 1,
    paddingTop: 12
  },
  cover: {
    backgroundColor: colors.surfaceRaised,
    borderBottomColor: colors.accent,
    borderBottomWidth: 3,
    height: 128
  },
  coverImage: {
    height: "100%",
    width: "100%"
  },
  handle: {
    color: colors.muted,
    fontSize: 13,
    marginTop: 3
  },
  identity: {
    flexDirection: "row",
    gap: 12,
    marginTop: -30,
    paddingHorizontal: 14
  },
  name: {
    color: colors.text,
    fontSize: 24,
    fontWeight: "900"
  },
  primaryButton: {
    alignItems: "center",
    backgroundColor: colors.accent,
    borderRadius: 8,
    flex: 1,
    minHeight: 42,
    justifyContent: "center",
    paddingHorizontal: 12
  },
  primaryText: {
    color: colors.background,
    fontSize: 13,
    fontWeight: "900"
  },
  secondaryButton: {
    alignItems: "center",
    borderColor: colors.border,
    borderRadius: 8,
    borderWidth: 1,
    minHeight: 42,
    justifyContent: "center",
    paddingHorizontal: 14
  },
  secondaryText: {
    color: colors.text,
    fontSize: 13,
    fontWeight: "900"
  },
  stat: {
    alignItems: "center",
    backgroundColor: colors.surfaceRaised,
    borderRadius: 8,
    flex: 1,
    minHeight: 58,
    justifyContent: "center",
    padding: 7
  },
  statLabel: {
    color: colors.muted,
    fontSize: 11,
    marginTop: 3
  },
  statValue: {
    color: colors.text,
    fontSize: 18,
    fontWeight: "900"
  },
  stats: {
    flexDirection: "row",
    gap: 8,
    marginTop: 14,
    paddingHorizontal: 14
  }
});
