import { useEffect, useRef } from "react";
import { Animated, Image, Pressable, Share, StyleSheet, Text, View } from "react-native";
import { PulseProfile, profileWebUrl } from "../api/profile";
import { colors } from "../theme/colors";
import { createLogiNexusAmbientPulse, useLogiNexusReducedMotion } from "../theme/logiNexusMotion";

type ProfileHeaderProps = {
  profile: PulseProfile;
  publicKey?: string;
  owner?: boolean;
  followBusy?: boolean;
  onEdit?: () => void;
  onCustomize?: () => void;
  onGrowth?: () => void;
  onRefresh?: () => void;
  onSafety?: () => void;
  onMessage?: () => void;
  onFollow?: () => void;
};

export function ProfileHeader({ profile, publicKey, owner, followBusy, onEdit, onCustomize, onGrowth, onRefresh, onSafety, onMessage, onFollow }: ProfileHeaderProps) {
  const handle = profile.username || profile.public_player_id || publicKey || "";
  const premium = ["active", "premium", "founder", "lifetime"].includes(String(profile.premium_status || "").toLowerCase());
  const verified = Boolean(profile.verified_badge || profile.verification_status === "verified");
  const accent = profile.theme?.accent_color || colors.accent;
  const themeLabel = String(profile.theme?.theme_key || "deep_space").replace(/_/g, " ");
  const layoutLabel = String(profile.theme?.layout_key || "classic").replace(/_/g, " ");
  const pulse = useRef(new Animated.Value(0)).current;
  const reducedMotion = useLogiNexusReducedMotion() || profile.theme?.motion_level === "reduced";

  useEffect(() => {
    if (reducedMotion) {
      pulse.stopAnimation();
      pulse.setValue(0);
      return;
    }
    const animation = createLogiNexusAmbientPulse(pulse, { duration: profile.theme?.motion_level === "subtle" ? 4200 : 3000 });
    animation.start();
    return () => animation.stop();
  }, [profile.theme?.motion_level, pulse, reducedMotion]);

  const auraOpacity = pulse.interpolate({ inputRange: [0, 1], outputRange: [0.16, 0.44] });
  const auraScale = pulse.interpolate({ inputRange: [0, 1], outputRange: [1, 1.04] });

  return (
    <View style={[styles.card, { borderColor: `${accent}55` }]} testID="profile-v2-header">
      <View style={styles.cover}>
        {profile.cover_url ? <Image source={{ uri: profile.cover_url }} style={styles.coverImage} resizeMode="cover" /> : null}
        <View style={[styles.coverTint, { backgroundColor: `${accent}18` }]} />
        <Animated.View style={[styles.orbitOne, { borderColor: `${accent}66`, opacity: auraOpacity, transform: [{ scale: auraScale }] }]} />
        <Animated.View style={[styles.orbitTwo, { backgroundColor: `${accent}28`, opacity: auraOpacity }]} />
        <View style={styles.themePill}><View style={[styles.signalDot, { backgroundColor: accent }]} /><Text style={styles.themePillText}>{themeLabel}</Text></View>
      </View>

      <View style={styles.identity}>
        <Animated.View style={[styles.avatarAura, { borderColor: accent, shadowColor: accent, opacity: reducedMotion ? 1 : auraOpacity, transform: [{ scale: auraScale }] }]} />
        {profile.avatar_url ? <Image source={{ uri: profile.avatar_url }} style={[styles.avatar, { borderColor: accent }]} /> : (
          <View style={[styles.avatarFallback, { borderColor: accent }]}><Text style={styles.avatarText}>{profile.display_name.slice(0, 1).toUpperCase()}</Text></View>
        )}
        <View style={styles.copy}>
          <View style={styles.nameRow}><Text style={styles.name} numberOfLines={1}>{profile.display_name}</Text>{verified ? <Text accessibilityLabel="Verified profile" style={[styles.verified, { color: accent }]}>◆</Text> : null}</View>
          <Text style={styles.handle} numberOfLines={1}>{handle ? `@${handle}` : "PulseSoc profile"}</Text>
          <View style={styles.badges}>
            {verified ? <Badge label="Verified" accent={accent} /> : null}
            {premium ? <Badge label="Premium" accent={accent} /> : null}
            {profile.profile_visibility === "private" ? <Badge label="Private" accent={accent} /> : null}
            <Badge label={layoutLabel} accent={accent} />
          </View>
        </View>
      </View>

      <Text style={profile.bio ? styles.bio : styles.bioMuted}>{profile.bio || (owner ? "Add a bio to shape your PulseSoc identity." : "This member has not added a bio yet.")}</Text>
      <View style={styles.stats} accessibilityLabel="Profile statistics">
        <Stat label="Posts" value={profile.post_count || 0} accent={accent} />
        <Stat label="Followers" value={profile.follower_count || 0} accent={accent} />
        <Stat label="Following" value={profile.following_count || 0} accent={accent} />
        <Stat label="Media" value={profile.media_count || 0} accent={accent} />
      </View>

      <View style={styles.actions}>
        {owner ? <Action label="Edit Profile" primary accent={accent} onPress={onEdit} /> : <Action label="Message" primary accent={accent} onPress={onMessage} />}
        {owner ? <Action label="Customize" onPress={onCustomize} /> : <Action label={profile.viewer_follows ? "Following" : "Follow"} selected={profile.viewer_follows} disabled={followBusy} onPress={onFollow} />}
        <Action label="Share" onPress={() => Share.share({ message: profileWebUrl(owner ? profile.public_player_id || profile.username : publicKey) }).catch(() => undefined)} />
      </View>

      <View style={styles.moduleRail} accessibilityLabel="Profile modules">
        <Module icon="◎" label="Identity" accent={accent} />
        <Module icon="▣" label="Media" accent={accent} />
        <Module icon="♫" label="Music" accent={accent} />
        <Module icon="◇" label="Trust" accent={accent} />
      </View>
      <View style={styles.utilityRow}>
        {owner ? <Utility label="Growth" onPress={onGrowth} /> : null}
        <Utility label="Safety" onPress={onSafety} />
        <Utility label="Refresh" onPress={onRefresh} />
      </View>
    </View>
  );
}

function Badge({ label, accent }: { label: string; accent: string }) { return <Text style={[styles.badge, { borderColor: `${accent}88`, color: accent, backgroundColor: `${accent}16` }]}>{label}</Text>; }
function Stat({ label, value, accent }: { label: string; value: number; accent: string }) { return <View style={styles.stat}><Text style={[styles.statValue, { color: accent }]}>{formatCount(value)}</Text><Text style={styles.statLabel}>{label}</Text></View>; }
function Module({ icon, label, accent }: { icon: string; label: string; accent: string }) { return <View style={styles.module}><View style={[styles.moduleIcon, { borderColor: `${accent}77` }]}><Text style={[styles.moduleGlyph, { color: accent }]}>{icon}</Text></View><Text style={styles.moduleLabel}>{label}</Text></View>; }
function Utility({ label, onPress }: { label: string; onPress?: () => void }) { return <Pressable accessibilityRole="button" style={styles.utility} onPress={onPress}><Text style={styles.utilityText}>{label}</Text></Pressable>; }
function Action({ label, primary, selected, disabled, accent, onPress }: { label: string; primary?: boolean; selected?: boolean; disabled?: boolean; accent?: string; onPress?: () => void }) { return <Pressable accessibilityRole="button" accessibilityState={{ selected: Boolean(selected), disabled: Boolean(disabled) }} disabled={disabled} style={[styles.action, primary && { backgroundColor: accent || colors.accent, borderColor: accent || colors.accent }, selected && styles.actionSelected, disabled && styles.disabled]} onPress={onPress}><Text style={[styles.actionText, primary && styles.actionTextPrimary, selected && styles.actionTextSelected]}>{disabled ? "Working…" : label}</Text></Pressable>; }
function formatCount(value: number) { if (value >= 1_000_000) return `${(value / 1_000_000).toFixed(value >= 10_000_000 ? 0 : 1)}M`; if (value >= 1_000) return `${(value / 1_000).toFixed(value >= 10_000 ? 0 : 1)}K`; return String(value); }

const styles = StyleSheet.create({
  action: { alignItems: "center", borderColor: colors.border, borderRadius: 12, borderWidth: 1, flex: 1, justifyContent: "center", minHeight: 46, paddingHorizontal: 8 },
  actionSelected: { backgroundColor: colors.signalSoft, borderColor: colors.accentStrong }, actionText: { color: colors.text, fontSize: 13, fontWeight: "900" }, actionTextPrimary: { color: colors.background }, actionTextSelected: { color: colors.accentStrong },
  actions: { flexDirection: "row", gap: 8, marginTop: 16, paddingHorizontal: 14 }, avatar: { backgroundColor: colors.surfaceRaised, borderRadius: 48, borderWidth: 3, height: 96, width: 96 },
  avatarAura: { borderRadius: 54, borderWidth: 2, height: 106, left: 9, position: "absolute", shadowOpacity: 0.8, shadowRadius: 18, top: -48, width: 106 },
  avatarFallback: { alignItems: "center", backgroundColor: colors.surfaceRaised, borderRadius: 48, borderWidth: 3, height: 96, justifyContent: "center", width: 96 }, avatarText: { color: colors.text, fontSize: 34, fontWeight: "900" },
  badge: { borderRadius: 999, borderWidth: 1, fontSize: 10, fontWeight: "900", paddingHorizontal: 8, paddingVertical: 4, textTransform: "capitalize" }, badges: { flexDirection: "row", flexWrap: "wrap", gap: 5, marginTop: 7 },
  bio: { color: colors.text, fontSize: 15, lineHeight: 22, marginTop: 12, paddingHorizontal: 14 }, bioMuted: { color: colors.muted, fontSize: 15, lineHeight: 22, marginTop: 12, paddingHorizontal: 14 },
  card: { backgroundColor: colors.surface, borderRadius: 18, borderWidth: 1, overflow: "hidden", paddingBottom: 14 }, copy: { flex: 1, minWidth: 0, paddingTop: 10 },
  cover: { backgroundColor: "#07111f", height: 190, overflow: "hidden" }, coverImage: { height: "100%", width: "100%" }, coverTint: { ...StyleSheet.absoluteFillObject },
  disabled: { opacity: 0.55 }, handle: { color: colors.muted, fontSize: 13, marginTop: 3 }, identity: { flexDirection: "row", gap: 12, marginTop: -42, paddingHorizontal: 14 },
  module: { alignItems: "center", flex: 1, gap: 6 }, moduleGlyph: { fontSize: 21, fontWeight: "900" }, moduleIcon: { alignItems: "center", backgroundColor: colors.background, borderRadius: 28, borderWidth: 1, height: 52, justifyContent: "center", width: 52 }, moduleLabel: { color: colors.muted, fontSize: 11, fontWeight: "800" }, moduleRail: { borderTopColor: colors.border, borderTopWidth: 1, flexDirection: "row", marginHorizontal: 14, marginTop: 16, paddingTop: 14 },
  name: { color: colors.text, flexShrink: 1, fontSize: 25, fontWeight: "900" }, nameRow: { alignItems: "center", flexDirection: "row", gap: 6 }, orbitOne: { borderRadius: 130, borderWidth: 1, height: 240, position: "absolute", right: -70, top: -65, width: 240 }, orbitTwo: { borderRadius: 80, height: 150, left: -44, position: "absolute", top: 30, width: 150 },
  signalDot: { borderRadius: 5, height: 8, width: 8 }, stat: { alignItems: "center", borderRightColor: colors.border, borderRightWidth: 1, flex: 1, justifyContent: "center", minHeight: 58 }, statLabel: { color: colors.muted, fontSize: 10, marginTop: 3 }, stats: { backgroundColor: colors.background, borderColor: colors.border, borderRadius: 14, borderWidth: 1, flexDirection: "row", marginHorizontal: 14, marginTop: 14, overflow: "hidden" }, statValue: { fontSize: 18, fontWeight: "900" },
  themePill: { alignItems: "center", backgroundColor: "rgba(3,9,18,.78)", borderColor: colors.border, borderRadius: 999, borderWidth: 1, flexDirection: "row", gap: 7, paddingHorizontal: 11, paddingVertical: 7, position: "absolute", right: 12, top: 12 }, themePillText: { color: colors.text, fontSize: 11, fontWeight: "900", textTransform: "capitalize" },
  utility: { paddingHorizontal: 8, paddingVertical: 8 }, utilityRow: { flexDirection: "row", justifyContent: "center", marginTop: 7 }, utilityText: { color: colors.muted, fontSize: 11, fontWeight: "800" }, verified: { fontSize: 16 },
});
