import { Ionicons } from "@expo/vector-icons";
import { LinearGradient } from "expo-linear-gradient";
import * as Haptics from "expo-haptics";
import { useEffect, useRef } from "react";
import { Animated, Easing, Image, Pressable, StyleSheet, Text, View } from "react-native";
import { PulseProfile, profileWebUrl } from "../api/profile";
import { colors } from "../theme/colors";
import { createLogiNexusAmbientPulse, useLogiNexusReducedMotion } from "../theme/logiNexusMotion";
import { sharePulseObject } from "../sharing/nativeShare";
import { ContentTranslation } from "./ContentTranslation";
import { PulseIdBadge } from "./PulseIdBadge";

export const PROFILE_HERO_HEIGHT = 320;

export type ProfileStatKey = "posts" | "followers" | "following" | "media";
export type ProfileModuleKey =
  | "identity"
  | "media"
  | "music"
  | "trust"
  | "safety"
  | "pulse_dna"
  | "achievements"
  | "activity"
  | "collections"
  | "communities"
  | "marketplace"
  | "events"
  | "business"
  | "memories";

type ModuleDef = { key: ProfileModuleKey; label: string; icon: keyof typeof Ionicons.glyphMap };

const MODULES: ModuleDef[] = [
  { key: "identity", label: "Pulse Identity", icon: "person-circle-outline" },
  { key: "media", label: "Media", icon: "images-outline" },
  { key: "music", label: "Music", icon: "musical-notes-outline" },
  { key: "trust", label: "Trust", icon: "shield-checkmark-outline" },
  { key: "safety", label: "Safety", icon: "lock-closed-outline" },
  { key: "pulse_dna", label: "Pulse DNA", icon: "pulse-outline" },
  { key: "achievements", label: "Achievements", icon: "trophy-outline" },
  { key: "activity", label: "Activity", icon: "flash-outline" },
  { key: "collections", label: "Collections", icon: "albums-outline" },
  { key: "communities", label: "Communities", icon: "people-outline" },
  { key: "marketplace", label: "Marketplace", icon: "storefront-outline" },
  { key: "events", label: "Events", icon: "calendar-outline" },
  { key: "business", label: "Business", icon: "briefcase-outline" },
  { key: "memories", label: "Memories", icon: "time-outline" }
];

type ProfileHeaderProps = {
  profile: PulseProfile;
  publicKey?: string;
  owner?: boolean;
  followBusy?: boolean;
  scrollY?: Animated.Value;
  onEdit?: () => void;
  onCustomize?: () => void;
  onGrowth?: () => void;
  onRefresh?: () => void;
  onSafety?: () => void;
  onMessage?: () => void;
  onFollow?: () => void;
  onCall?: () => void;
  onVideoCall?: () => void;
  onStatPress?: (key: ProfileStatKey) => void;
  onModulePress?: (key: ProfileModuleKey) => void;
};

function haptic() {
  Haptics.impactAsync(Haptics.ImpactFeedbackStyle.Light).catch(() => undefined);
}

function createFloat(value: Animated.Value, duration: number) {
  return Animated.loop(
    Animated.sequence([
      Animated.timing(value, { toValue: 1, duration, easing: Easing.inOut(Easing.sin), useNativeDriver: true }),
      Animated.timing(value, { toValue: 0, duration, easing: Easing.inOut(Easing.sin), useNativeDriver: true })
    ])
  );
}

export function ProfileHeader({
  profile,
  publicKey,
  owner,
  followBusy,
  scrollY,
  onEdit,
  onCustomize,
  onGrowth,
  onRefresh,
  onSafety,
  onMessage,
  onFollow,
  onCall,
  onVideoCall,
  onStatPress,
  onModulePress
}: ProfileHeaderProps) {
  const handle = profile.username || profile.public_player_id || publicKey || "";
  const premium = ["active", "premium", "founder", "lifetime"].includes(String(profile.premium_status || "").toLowerCase());
  const verified = Boolean(profile.verified_badge || profile.verification_status === "verified");
  const accent = profile.theme?.accent_color || colors.accent;
  const tierLabel = premium ? String(profile.premium_status || "premium").replace(/_/g, " ") : "";
  const online = String(profile.account_status || "active").toLowerCase() === "active";

  const pulse = useRef(new Animated.Value(0)).current;
  const float1 = useRef(new Animated.Value(0)).current;
  const float2 = useRef(new Animated.Value(0)).current;
  const wave = useRef(new Animated.Value(0)).current;
  const reducedMotion = useLogiNexusReducedMotion() || profile.theme?.motion_level === "reduced";

  useEffect(() => {
    if (reducedMotion) {
      [pulse, float1, float2, wave].forEach((value) => {
        value.stopAnimation();
        value.setValue(0);
      });
      return;
    }
    const breathing = profile.theme?.motion_level === "subtle" ? 4200 : 3000;
    const animations = [
      createLogiNexusAmbientPulse(pulse, { duration: breathing }),
      createFloat(float1, 7200),
      createFloat(float2, 9400),
      createFloat(wave, 5200)
    ];
    animations.forEach((animation) => animation.start());
    return () => animations.forEach((animation) => animation.stop());
  }, [profile.theme?.motion_level, pulse, float1, float2, wave, reducedMotion]);

  const auraOpacity = pulse.interpolate({ inputRange: [0, 1], outputRange: [0.18, 0.5] });
  const auraScale = pulse.interpolate({ inputRange: [0, 1], outputRange: [1, 1.06] });
  const ringScale = pulse.interpolate({ inputRange: [0, 1], outputRange: [1, 1.12] });
  const float1Y = float1.interpolate({ inputRange: [0, 1], outputRange: [0, -26] });
  const float1X = float1.interpolate({ inputRange: [0, 1], outputRange: [0, 18] });
  const float2Y = float2.interpolate({ inputRange: [0, 1], outputRange: [0, 22] });
  const waveScale = wave.interpolate({ inputRange: [0, 1], outputRange: [0.9, 1.35] });
  const waveOpacity = wave.interpolate({ inputRange: [0, 0.6, 1], outputRange: [0.35, 0.12, 0] });

  // Scroll-driven compression (native driver friendly: transform + opacity only).
  const scroll = scrollY ?? new Animated.Value(0);
  const bgTranslateY = scroll.interpolate({ inputRange: [-160, 0, PROFILE_HERO_HEIGHT], outputRange: [-80, 0, PROFILE_HERO_HEIGHT * 0.55], extrapolateLeft: "extend", extrapolateRight: "clamp" });
  const bgScale = scroll.interpolate({ inputRange: [-160, 0], outputRange: [1.28, 1], extrapolateRight: "clamp" });
  const fieldOpacity = scroll.interpolate({ inputRange: [0, 220], outputRange: [1, 0.32], extrapolate: "clamp" });
  const avatarScale = scroll.interpolate({ inputRange: [0, 200], outputRange: [1, 0.78], extrapolate: "clamp" });
  const avatarLift = scroll.interpolate({ inputRange: [0, 200], outputRange: [0, 14], extrapolate: "clamp" });
  const identityOpacity = scroll.interpolate({ inputRange: [120, 240], outputRange: [1, 0.86], extrapolate: "clamp" });

  const shareTarget = owner ? profile.public_player_id || profile.username : publicKey;

  return (
    <View style={styles.root} testID="profile-v6-header">
      {/* Immersive energy field */}
      <View style={[styles.hero, { height: PROFILE_HERO_HEIGHT }]} pointerEvents="none">
        <Animated.View style={[StyleSheet.absoluteFill, { opacity: fieldOpacity, transform: [{ translateY: bgTranslateY }, { scale: bgScale }] }]}>
          {profile.cover_url ? <Image source={{ uri: profile.cover_url }} style={styles.coverImage} resizeMode="cover" /> : null}
          <LinearGradient colors={[`${accent}33`, "#050910f2", colors.background]} start={{ x: 0.1, y: 0 }} end={{ x: 0.9, y: 1 }} style={StyleSheet.absoluteFill} />
          <Animated.View style={[styles.nebula, { backgroundColor: `${accent}2e`, transform: [{ translateX: float1X }, { translateY: float1Y }] }]} />
          <Animated.View style={[styles.nebulaTwo, { backgroundColor: `${colors.intelligence}26`, transform: [{ translateY: float2Y }] }]} />
          <Animated.View style={[styles.pulseWave, { borderColor: `${accent}55`, opacity: waveOpacity, transform: [{ scale: waveScale }] }]} />
          <View style={styles.grain} />
        </Animated.View>
        <LinearGradient colors={["transparent", "transparent", colors.background]} style={StyleSheet.absoluteFill} pointerEvents="none" />
      </View>

      {/* Identity */}
      <View style={styles.body}>
        <View style={styles.avatarWrap}>
          <Animated.View style={[styles.ringOuter, { borderColor: `${accent}55`, opacity: auraOpacity, transform: [{ scale: ringScale }] }]} />
          <Animated.View style={[styles.ringGlow, { shadowColor: accent, borderColor: accent, opacity: reducedMotion ? 0.9 : auraOpacity, transform: [{ scale: auraScale }] }]} />
          <Animated.View style={{ transform: [{ scale: avatarScale }, { translateY: avatarLift }] }}>
            {profile.avatar_url ? (
              <Image source={{ uri: profile.avatar_url }} style={[styles.avatar, { borderColor: accent }]} />
            ) : (
              <View style={[styles.avatarFallback, { borderColor: accent }]}>
                <Text style={styles.avatarText}>{(profile.display_name || "?").slice(0, 1).toUpperCase()}</Text>
              </View>
            )}
            {verified ? (
              <View style={[styles.verifiedSeal, { backgroundColor: accent, borderColor: colors.background }]}>
                <Ionicons name="checkmark" size={14} color={colors.background} />
              </View>
            ) : null}
            <View style={[styles.presenceDot, { backgroundColor: online ? colors.safety : colors.muted, borderColor: colors.background }]} />
          </Animated.View>
        </View>

        <Animated.View style={{ opacity: identityOpacity }}>
          <View style={styles.nameRow}>
            <Text style={styles.name} numberOfLines={1}>{profile.display_name}</Text>
            {verified ? <Ionicons name="checkmark-circle" size={22} color={accent} style={styles.nameVerified} /> : null}
          </View>
          <Text style={styles.handle} numberOfLines={1}>{handle ? `@${handle}` : "PulseSoc identity"}</Text>
          <PulseIdBadge pulseId={profile.pulse_id} />

          <View style={styles.badges}>
            {verified ? <Badge label="Verified" icon="shield-checkmark" accent={accent} /> : null}
            {premium ? <Badge label={tierLabel || "Premium"} icon="sparkles" accent={colors.economy} /> : null}
            {profile.profile_visibility === "private" ? <Badge label="Private" icon="lock-closed" accent={colors.muted} /> : null}
            <Badge label={online ? "Active now" : "Away"} icon="ellipse" accent={online ? colors.safety : colors.muted} />
          </View>

          {profile.bio ? (
            <ContentTranslation
              contentType="profile"
              contentRef={profile.user_id || profile.public_player_id || handle}
              text={profile.bio}
              textStyle={styles.bio}
            />
          ) : (
            <Text style={styles.bioMuted}>{owner ? "Add a bio to shape your PulseSoc identity." : "This member has not added a bio yet."}</Text>
          )}
        </Animated.View>

        {/* Stats */}
        <View style={styles.stats} accessibilityLabel="Profile statistics">
          <Stat label="Posts" value={profile.post_count || 0} accent={accent} onPress={() => { haptic(); onStatPress?.("posts"); }} />
          <Stat label="Followers" value={profile.follower_count || 0} accent={accent} onPress={() => { haptic(); onStatPress?.("followers"); }} />
          <Stat label="Following" value={profile.following_count || 0} accent={accent} onPress={() => { haptic(); onStatPress?.("following"); }} />
          <Stat label="Media" value={profile.media_count || 0} accent={accent} onPress={() => { haptic(); onStatPress?.("media"); }} />
        </View>

        {/* Actions */}
        <View style={styles.actions}>
          {owner ? (
            <>
              <Action label="Edit Profile" icon="create-outline" primary accent={accent} onPress={() => { haptic(); onEdit?.(); }} />
              <Action label="Customize" icon="color-palette-outline" onPress={() => { haptic(); onCustomize?.(); }} />
              <Action label="Share" icon="share-outline" onPress={() => { haptic(); sharePulseObject({
                kind: "profile",
                url: profileWebUrl(shareTarget),
                title: profile.display_name || profile.username || "PulseSoc profile",
                description: profile.bio,
                author: profile.display_name || profile.username,
                previewImageUrl: profile.avatar_url
              }).catch(() => undefined); }} />
            </>
          ) : (
            <>
              <Action label="Message" icon="chatbubble-ellipses-outline" primary accent={accent} onPress={() => { haptic(); onMessage?.(); }} />
              <Action label={profile.viewer_follows ? "Following" : "Follow"} icon={profile.viewer_follows ? "checkmark-done-outline" : "person-add-outline"} selected={profile.viewer_follows} disabled={followBusy} onPress={() => { haptic(); onFollow?.(); }} />
              <Action label="Call" icon="call-outline" onPress={() => { haptic(); onCall?.(); }} />
              <Action label="Video" icon="videocam-outline" onPress={() => { haptic(); onVideoCall?.(); }} />
              <Action label="Share" icon="share-outline" onPress={() => { haptic(); sharePulseObject({
                kind: "profile",
                url: profileWebUrl(shareTarget),
                title: profile.display_name || profile.username || "PulseSoc profile",
                description: profile.bio,
                author: profile.display_name || profile.username,
                previewImageUrl: profile.avatar_url
              }).catch(() => undefined); }} />
            </>
          )}
        </View>

        {/* Module operating system */}
        <View style={styles.modulesHeader}>
          <Text style={styles.modulesTitle}>Profile OS</Text>
          <View style={styles.utilityRow}>
            {owner ? <Utility label="Growth" icon="trending-up-outline" onPress={() => { haptic(); onGrowth?.(); }} /> : null}
            <Utility label="Safety" icon="shield-outline" onPress={() => { haptic(); onSafety?.(); }} />
            <Utility label="Refresh" icon="refresh-outline" onPress={() => { haptic(); onRefresh?.(); }} />
          </View>
        </View>
        <View style={styles.moduleGrid} accessibilityLabel="Profile modules">
          {MODULES.map((module) => (
            <Module key={module.key} def={module} accent={accent} onPress={() => { haptic(); onModulePress?.(module.key); }} />
          ))}
        </View>
      </View>
    </View>
  );
}

function Badge({ label, icon, accent }: { label: string; icon: keyof typeof Ionicons.glyphMap; accent: string }) {
  return (
    <View style={[styles.badge, { borderColor: `${accent}88`, backgroundColor: `${accent}18` }]}>
      <Ionicons name={icon} size={11} color={accent} />
      <Text style={[styles.badgeText, { color: accent }]}>{label}</Text>
    </View>
  );
}

function Stat({ label, value, accent, onPress }: { label: string; value: number; accent: string; onPress?: () => void }) {
  return (
    <Pressable accessibilityRole="button" accessibilityLabel={`${formatCount(value)} ${label}`} style={({ pressed }) => [styles.stat, pressed && styles.pressed]} onPress={onPress}>
      <Text style={[styles.statValue, { color: accent }]}>{formatCount(value)}</Text>
      <Text style={styles.statLabel}>{label}</Text>
    </Pressable>
  );
}

function Action({ label, icon, primary, selected, disabled, accent, onPress }: { label: string; icon: keyof typeof Ionicons.glyphMap; primary?: boolean; selected?: boolean; disabled?: boolean; accent?: string; onPress?: () => void }) {
  const tint = primary ? colors.background : selected ? colors.accentStrong : colors.text;
  return (
    <Pressable
      accessibilityRole="button"
      accessibilityState={{ selected: Boolean(selected), disabled: Boolean(disabled) }}
      disabled={disabled}
      style={({ pressed }) => [styles.action, primary && { backgroundColor: accent || colors.accent, borderColor: accent || colors.accent }, selected && styles.actionSelected, disabled && styles.disabled, pressed && styles.pressed]}
      onPress={onPress}
    >
      <Ionicons name={icon} size={16} color={tint} />
      <Text style={[styles.actionText, { color: tint }]} numberOfLines={1}>{disabled ? "Working…" : label}</Text>
    </Pressable>
  );
}

function Module({ def, accent, onPress }: { def: ModuleDef; accent: string; onPress?: () => void }) {
  return (
    <Pressable accessibilityRole="button" accessibilityLabel={def.label} style={({ pressed }) => [styles.module, pressed && styles.pressed]} onPress={onPress}>
      <View style={[styles.moduleIcon, { borderColor: `${accent}55`, backgroundColor: `${accent}12` }]}>
        <Ionicons name={def.icon} size={22} color={accent} />
      </View>
      <Text style={styles.moduleLabel} numberOfLines={1}>{def.label}</Text>
    </Pressable>
  );
}

function Utility({ label, icon, onPress }: { label: string; icon: keyof typeof Ionicons.glyphMap; onPress?: () => void }) {
  return (
    <Pressable accessibilityRole="button" style={({ pressed }) => [styles.utility, pressed && styles.pressed]} onPress={onPress}>
      <Ionicons name={icon} size={13} color={colors.muted} />
      <Text style={styles.utilityText}>{label}</Text>
    </Pressable>
  );
}

function formatCount(value: number) {
  if (value >= 1_000_000) return `${(value / 1_000_000).toFixed(value >= 10_000_000 ? 0 : 1)}M`;
  if (value >= 1_000) return `${(value / 1_000).toFixed(value >= 10_000 ? 0 : 1)}K`;
  return String(value);
}

const styles = StyleSheet.create({
  root: { backgroundColor: colors.background },
  hero: { overflow: "hidden", width: "100%" },
  coverImage: { ...StyleSheet.absoluteFillObject, height: undefined, width: undefined },
  nebula: { borderRadius: 220, height: 300, position: "absolute", right: -90, top: -70, width: 300 },
  nebulaTwo: { borderRadius: 160, height: 220, left: -70, position: "absolute", top: 40, width: 220 },
  pulseWave: { borderRadius: 200, borderWidth: 1.5, height: 320, left: "50%", marginLeft: -160, marginTop: -160, position: "absolute", top: "50%", width: 320 },
  grain: { ...StyleSheet.absoluteFillObject, backgroundColor: "rgba(5,9,16,0.12)" },

  body: { marginTop: -96, paddingHorizontal: 18 },
  avatarWrap: { alignItems: "center", justifyContent: "center", height: 128, width: 128 },
  ringOuter: { borderRadius: 72, borderWidth: 1, height: 144, position: "absolute", width: 144 },
  ringGlow: { borderRadius: 66, borderWidth: 2, height: 132, position: "absolute", shadowOpacity: 0.9, shadowRadius: 22, width: 132 },
  avatar: { backgroundColor: colors.surfaceRaised, borderRadius: 56, borderWidth: 3, height: 112, width: 112 },
  avatarFallback: { alignItems: "center", backgroundColor: colors.surfaceRaised, borderRadius: 56, borderWidth: 3, height: 112, justifyContent: "center", width: 112 },
  avatarText: { color: colors.text, fontSize: 40, fontWeight: "900" },
  verifiedSeal: { alignItems: "center", borderRadius: 14, borderWidth: 2, bottom: 6, height: 28, justifyContent: "center", position: "absolute", right: 2, width: 28 },
  presenceDot: { borderRadius: 9, borderWidth: 3, bottom: 8, height: 18, left: 6, position: "absolute", width: 18 },

  nameRow: { alignItems: "center", flexDirection: "row", gap: 6, marginTop: 14 },
  name: { color: colors.text, flexShrink: 1, fontSize: 28, fontWeight: "900", letterSpacing: 0.2 },
  nameVerified: { marginTop: 2 },
  handle: { color: colors.muted, fontSize: 14, marginTop: 3 },
  badges: { flexDirection: "row", flexWrap: "wrap", gap: 6, marginTop: 10 },
  badge: { alignItems: "center", borderRadius: 999, borderWidth: 1, flexDirection: "row", gap: 4, paddingHorizontal: 9, paddingVertical: 5 },
  badgeText: { fontSize: 11, fontWeight: "900", textTransform: "capitalize" },
  bio: { color: colors.text, fontSize: 15, lineHeight: 22, marginTop: 12 },
  bioMuted: { color: colors.muted, fontSize: 15, lineHeight: 22, marginTop: 12 },

  stats: { backgroundColor: colors.glass, borderColor: colors.border, borderRadius: 18, borderWidth: 1, flexDirection: "row", marginTop: 18, overflow: "hidden" },
  stat: { alignItems: "center", borderRightColor: colors.border, borderRightWidth: StyleSheet.hairlineWidth, flex: 1, justifyContent: "center", minHeight: 66, paddingVertical: 10 },
  statValue: { fontSize: 20, fontWeight: "900" },
  statLabel: { color: colors.muted, fontSize: 11, marginTop: 4 },

  actions: { flexDirection: "row", flexWrap: "wrap", gap: 8, marginTop: 16 },
  action: { alignItems: "center", borderColor: colors.border, borderRadius: 14, borderWidth: 1, flexDirection: "row", flexGrow: 1, gap: 6, justifyContent: "center", minHeight: 48, minWidth: 92, paddingHorizontal: 12 },
  actionSelected: { backgroundColor: colors.signalSoft, borderColor: colors.accentStrong },
  actionText: { fontSize: 13, fontWeight: "900" },

  modulesHeader: { alignItems: "center", flexDirection: "row", justifyContent: "space-between", marginTop: 26 },
  modulesTitle: { color: colors.text, fontSize: 16, fontWeight: "900", letterSpacing: 0.3 },
  utilityRow: { flexDirection: "row", gap: 4 },
  utility: { alignItems: "center", flexDirection: "row", gap: 4, paddingHorizontal: 8, paddingVertical: 6 },
  utilityText: { color: colors.muted, fontSize: 11, fontWeight: "800" },
  moduleGrid: { flexDirection: "row", flexWrap: "wrap", marginTop: 14 },
  module: { alignItems: "center", gap: 7, marginBottom: 18, width: "25%" },
  moduleIcon: { alignItems: "center", borderRadius: 20, borderWidth: 1, height: 58, justifyContent: "center", width: 58 },
  moduleLabel: { color: colors.muted, fontSize: 11, fontWeight: "800" },

  disabled: { opacity: 0.55 },
  pressed: { opacity: 0.7 }
});
