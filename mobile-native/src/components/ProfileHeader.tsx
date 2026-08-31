import { Ionicons } from "@expo/vector-icons";
import { LinearGradient } from "expo-linear-gradient";
import * as Haptics from "expo-haptics";
import { useEffect, useRef } from "react";
import { Animated, Easing, Image, Pressable, StyleSheet, Text, View } from "react-native";
import { PulseProfile, profileWebUrl } from "../api/profile";
import { colors } from "../theme/colors";
import { profileNeon } from "../theme/profileNeon";
import { premiumTheme } from "../theme/premiumTheme";
import { presenceTheme } from "../theme/presenceTheme";
import { progressTheme } from "../theme/progressTheme";
import { createLogiNexusAmbientPulse, useLogiNexusReducedMotion } from "../theme/logiNexusMotion";
import { sharePulseObject } from "../sharing/nativeShare";
import { ContentTranslation } from "./ContentTranslation";
import { createThemedStyles } from "../theme/themedStyles";

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
  | "briefings"
  | "collections"
  | "communities"
  | "marketplace"
  | "events"
  | "business"
  | "presence"
  | "memories"
  | "progress"
  | "premium";

type ModuleDef = {
  key: ProfileModuleKey;
  label: string;
  icon: keyof typeof Ionicons.glyphMap;
  /**
   * Fixed colour, overriding both `colors.accent` and the profile owner's
   * chosen `theme.accent_color`.
   *
   * Only Progress and Premium use this, and the exception is the point: every
   * other tile is an ambient panel, while these two are private to the owner
   * and carry money. Inheriting the profile accent would make them one card
   * among fifteen, and on a profile themed violet Progress would not even be
   * distinguishable. See `theme/progressTheme.ts` and `theme/premiumTheme.ts`.
   */
  accent?: string;
  /**
   * Soft halo behind the icon. Premium only.
   *
   * A shadow rather than a brighter fill, and a static one rather than an
   * animation: the tile has to read as the premium entry point without becoming
   * the brightest thing on a dark profile, and a pulsing tile would be both
   * cheap and a reduced-motion violation.
   */
  glow?: string;
};

/**
 * Live state a tile can carry, supplied by the screen that knows it.
 *
 * `status` is a short micro-label under the tile name. `undefined` means "say
 * nothing", which is deliberately different from an empty string: on a cold
 * start the Premium tile must render with no status word rather than asserting
 * a wrong one, so a paying member never watches their membership appear to
 * vanish and come back.
 */
export type ProfileModuleState = {
  status?: string;
  /** Replaces the tile's accent for this state — amber for a billing problem. */
  tint?: string;
  accessibilityLabel?: string;
  accessibilityHint?: string;
};

const MODULES: ModuleDef[] = [
  { key: "identity", label: "Pulse Identity", icon: "person-circle-outline" },
  { key: "media", label: "Media", icon: "images-outline" },
  { key: "music", label: "Music", icon: "musical-notes-outline" },
  { key: "trust", label: "Trust", icon: "shield-checkmark-outline" },
  { key: "safety", label: "Safety", icon: "lock-closed-outline" },
  { key: "pulse_dna", label: "Pulse DNA", icon: "pulse-outline" },
  { key: "achievements", label: "Achievements", icon: "trophy-outline" },
  { key: "activity", label: "Activity", icon: "flash-outline" },
  // Telescope, not a bell: this tile is the owner's intelligence digest
  // (network + market observation), not another notification inbox.
  { key: "briefings", label: "Briefings", icon: "telescope-outline" },
  { key: "collections", label: "Collections", icon: "albums-outline" },
  { key: "communities", label: "Communities", icon: "people-outline" },
  { key: "marketplace", label: "Marketplace", icon: "storefront-outline" },
  { key: "events", label: "Events", icon: "calendar-outline" },
  { key: "business", label: "Business", icon: "briefcase-outline" },
  // Fixed brand teal + static glow, like Progress/Premium survive the accent
  // override: Presence is the door to the member's professional identities and
  // must stay legible on any profile theme. Id-card icon: identity, not rank.
  { key: "presence", label: "Presence", icon: "id-card-outline", accent: presenceTheme.teal, glow: presenceTheme.glow },
  { key: "memories", label: "Memories", icon: "time-outline" },
  { key: "progress", label: "Progress", icon: "trending-up-outline", accent: progressTheme.violet },
  // Diamond, not a crown: a crown reads as rank over other members, which is
  // exactly what this tile must not imply. Premium is an account, not a status.
  { key: "premium", label: "Premium", icon: "diamond-outline", accent: premiumTheme.gold, glow: premiumTheme.glow }
];

const MODULE_BY_KEY = MODULES.reduce<Record<ProfileModuleKey, ModuleDef>>((map, module) => {
  map[module.key] = module;
  return map;
}, {} as Record<ProfileModuleKey, ModuleDef>);

/** "Maria" -> "Maria's"; "Chris" -> "Chris'". */
function possessiveName(name: string) {
  const trimmed = name.trim();
  if (!trimmed) return "";
  return /s$/i.test(trimmed) ? `${trimmed}'` : `${trimmed}'s`;
}

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
  /**
   * Tiles to render, in order. A visitor is given only the tiles that lead to a
   * destination about *this* profile owner; the rest are omitted rather than
   * shown as dead entries. Defaults to the full set so any caller that has not
   * been updated keeps its current grid.
   */
  moduleKeys?: ProfileModuleKey[];
  /**
   * Per-tile live state. Only tiles the screen has an answer for appear here;
   * everything else renders exactly as before.
   */
  moduleState?: Partial<Record<ProfileModuleKey, ProfileModuleState>>;
  /**
   * First name of the profile owner when someone else is viewing, used to label
   * the grid ("Maria's Profile OS"). Empty on your own profile.
   */
  moduleOwnerName?: string;
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
  onModulePress,
  moduleKeys,
  moduleState,
  moduleOwnerName
}: ProfileHeaderProps) {
  const modules = moduleKeys ? moduleKeys.map((key) => MODULE_BY_KEY[key]).filter(Boolean) : MODULES;
  const modulesTitle = moduleOwnerName ? `${possessiveName(moduleOwnerName)} Profile OS` : "Profile OS";
  // Public identifiers only. `publicKey` is the route's lookup key, and
  // `resolveProfileTarget` sets `profileKey = userId ? String(userId) : …`, so
  // it is the internal numeric user id whenever the profile was opened from a
  // feed author, search result or share link carrying a user_id. Rendering it
  // raw printed "@1234567" — a private database id — on any profile that has
  // no username yet. `username` and `public_player_id` are public handles; a
  // bare number is not, so it is dropped rather than displayed.
  const publicHandleKey = /^\d+$/.test(String(publicKey || "").trim()) ? "" : publicKey || "";
  const handle = profile.username || profile.public_player_id || publicHandleKey || "";
  const premium = ["active", "premium", "founder", "lifetime"].includes(String(profile.premium_status || "").toLowerCase());
  const verified = Boolean(profile.verified_badge || profile.verification_status === "verified");
  // Blue is the default identity colour of the profile surface; a profile
  // owner's chosen accent still overrides it, so customised profiles are
  // untouched. See theme/profileNeon.ts for why this is not a global change.
  const accent = profile.theme?.accent_color || profileNeon.electric;
  const tierLabel = premium ? String(profile.premium_status || "premium").replace(/_/g, " ") : "";
  const online = String(profile.account_status || "active").toLowerCase() === "active";
  const automated = profile.automated === true || profile.account_type === "PULSESOC_AUTOMATED";
  const galacticAccountCover = profile.public_player_id === "pulsesoc_insight"
    && Boolean(profile.cover_url?.includes("pulsesoc-insight-cover-20260825.png"));

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

  // Built here rather than inline so the automated-account omissions stay a
  // single decision, and so the divider logic can key off real position.
  const statEntries: { key: ProfileStatKey; label: string; icon: keyof typeof Ionicons.glyphMap; value: number }[] = [
    { key: "posts", label: "Posts", icon: "grid-outline", value: profile.post_count || 0 },
    ...(!automated
      ? ([
          { key: "followers", label: "Followers", icon: "people-outline", value: profile.follower_count || 0 },
          { key: "following", label: "Following", icon: "person-add-outline", value: profile.following_count || 0 }
        ] as const)
      : []),
    { key: "media", label: "Media", icon: "images-outline", value: profile.media_count || 0 }
  ];

  return (
    <View style={styles.root} testID="profile-v6-header">
      {/* Immersive energy field */}
      <View style={[styles.hero, { height: PROFILE_HERO_HEIGHT }]} pointerEvents="none">
        <Animated.View style={[StyleSheet.absoluteFill, { opacity: fieldOpacity, transform: [{ translateY: bgTranslateY }, { scale: bgScale }] }]}>
          {profile.cover_url && !galacticAccountCover ? <Image source={{ uri: profile.cover_url }} style={styles.coverImage} resizeMode="cover" /> : null}
          <LinearGradient colors={[`${accent}33`, "#050910f2", colors.background]} start={{ x: 0.1, y: 0 }} end={{ x: 0.9, y: 1 }} style={StyleSheet.absoluteFill} />
          <Animated.View style={[styles.nebula, { backgroundColor: `${accent}2e`, transform: [{ translateX: float1X }, { translateY: float1Y }] }]} />
          <Animated.View style={[styles.nebulaTwo, { backgroundColor: `${profileNeon.violet}22`, transform: [{ translateY: float2Y }] }]} />
          {/* Planetary curve. A single oversized circle clipped by the hero's
              own overflow:hidden — no SVG, no image payload, one static view.
              The border is the lit limb; the fill is barely there so the name
              above it never loses contrast. */}
          <View style={[styles.horizon, { borderColor: profileNeon.borderStrong, backgroundColor: profileNeon.fillSoft }]} pointerEvents="none" />
          <LinearGradient
            colors={profileNeon.horizon}
            start={{ x: 0.5, y: 1 }}
            end={{ x: 0.5, y: 0 }}
            style={styles.horizonGlow}
            pointerEvents="none"
          />
          {/* Light trails: two hairlines converging on the horizon. Static, so
              they cost one layout each and nothing per frame. */}
          <View style={[styles.trail, styles.trailLeft, { backgroundColor: profileNeon.hairline }]} pointerEvents="none" />
          <View style={[styles.trail, styles.trailRight, { backgroundColor: profileNeon.hairline }]} pointerEvents="none" />
          <Animated.View style={[styles.pulseWave, { borderColor: `${accent}55`, opacity: waveOpacity, transform: [{ scale: waveScale }] }]} />
          <View style={styles.grain} />
        </Animated.View>
        <LinearGradient colors={["transparent", "transparent", colors.background]} style={StyleSheet.absoluteFill} pointerEvents="none" />
        {galacticAccountCover ? (
          <Image testID="automated-account-brand-cover" source={{ uri: profile.cover_url }}
            style={styles.automatedBrandCover} resizeMode="contain" />
        ) : null}
      </View>

      {/* Identity */}
      <View style={styles.body}>
        <View style={styles.avatarWrap}>
          <Animated.View style={[styles.ringOuter, { borderColor: `${accent}55`, opacity: auraOpacity, transform: [{ scale: ringScale }] }]} />
          {/* Static orbit ring between the breathing aura and the lit ring. It
              is what makes the avatar read as engineered rather than merely
              glowing, and being static it survives reduced motion unchanged. */}
          <View style={[styles.ringOrbit, { borderColor: profileNeon.border, borderTopColor: profileNeon.cyan }]} pointerEvents="none" />
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
          <View style={styles.badges}>
            {automated ? <Badge label="AUTOMATED" icon="hardware-chip-outline" accent={colors.economy} /> : null}
            {verified ? <Badge label="Verified" icon="shield-checkmark" accent={accent} /> : null}
            {premium ? <Badge label={tierLabel || "Premium"} icon="sparkles" accent={colors.economy} /> : null}
            {profile.profile_visibility === "private" ? <Badge label="Private" icon="lock-closed" accent={colors.muted} /> : null}
            <Badge label={online ? "Active now" : "Away"} icon="ellipse" accent={online ? colors.safety : colors.muted} />
          </View>

          {automated ? (
            <View accessibilityLabel="Automated PulseSoc account disclosure" style={styles.automationDisclosure}>
              <Text style={styles.automationLabel}>{profile.system_account_label || "Official PulseSoc System Account"}</Text>
              <Text style={styles.automationTitle}>AUTOMATED PULSESOC ACCOUNT</Text>
              <Text style={styles.automationBody}>{profile.automation_disclosure || "This account is operated automatically by PulseSoc. It is not a human user."}</Text>
              <Text style={styles.automationTrustTitle}>Transparency &amp; Trust</Text>
              <Text style={styles.automationBody}>{profile.transparency_disclosure || "Automated posts remain subject to PulseSoc safety and quality controls."}</Text>
            </View>
          ) : null}

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

        {/* Stats. Same four counts from the same canonical fields — the panel
            around them is what changed, not the numbers. An automated account
            still hides follower/following, exactly as before. */}
        <View style={styles.stats} accessibilityLabel="Profile statistics">
          <LinearGradient
            colors={[profileNeon.borderStrong, "transparent"]}
            start={{ x: 0, y: 0 }}
            end={{ x: 1, y: 0 }}
            style={styles.statsRail}
            pointerEvents="none"
          />
          <View style={styles.statsRow}>
            {statEntries.map((entry, index) => (
              <View key={entry.key} style={styles.statCell}>
                {index > 0 ? <View style={[styles.statDivider, { backgroundColor: profileNeon.hairline }]} /> : null}
                <Stat
                  label={entry.label}
                  icon={entry.icon}
                  value={entry.value}
                  accent={accent}
                  onPress={() => { haptic(); onStatPress?.(entry.key); }}
                />
              </View>
            ))}
          </View>
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
          ) : automated ? (
            <Action label="Share" icon="share-outline" primary accent={accent} onPress={() => { haptic(); sharePulseObject({
              kind: "profile",
              url: profileWebUrl(shareTarget),
              title: profile.display_name || "PulseSoc Insight",
              description: profile.automation_disclosure || profile.bio,
              author: profile.display_name,
              previewImageUrl: profile.avatar_url
            }).catch(() => undefined); }} />
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
          <Text style={styles.modulesTitle}>{modulesTitle}</Text>
          <View style={styles.utilityRow}>
            {owner ? <Utility label="Growth" icon="trending-up-outline" onPress={() => { haptic(); onGrowth?.(); }} /> : null}
            <Utility label="Safety" icon="shield-outline" onPress={() => { haptic(); onSafety?.(); }} />
            <Utility label="Refresh" icon="refresh-outline" onPress={() => { haptic(); onRefresh?.(); }} />
          </View>
        </View>
        <View style={styles.moduleGrid} accessibilityLabel="Profile modules">
          {modules.map((module) => (
            <Module
              key={module.key}
              def={module}
              accent={moduleState?.[module.key]?.tint || module.accent || accent}
              state={moduleState?.[module.key]}
              onPress={() => { haptic(); onModulePress?.(module.key); }}
            />
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

function Stat({ label, value, icon, accent, onPress }: { label: string; value: number; icon: keyof typeof Ionicons.glyphMap; accent: string; onPress?: () => void }) {
  return (
    <Pressable
      accessibilityRole="button"
      // The exact count, not the abbreviation: "1.2K Followers" is a rounding
      // read aloud, and the icon carries no meaning for a screen reader.
      accessibilityLabel={`${value.toLocaleString()} ${label}`}
      style={({ pressed }) => [styles.stat, pressed && styles.pressed]}
      onPress={onPress}
    >
      <Ionicons name={icon} size={13} color={profileNeon.cyan} style={styles.statIcon} />
      <Text style={[styles.statValue, { color: colors.text }]} numberOfLines={1} adjustsFontSizeToFit minimumFontScale={0.6}>{formatCount(value)}</Text>
      <Text style={[styles.statLabel, { color: accent }]} numberOfLines={1}>{label}</Text>
    </Pressable>
  );
}

function Action({ label, icon, primary, selected, disabled, accent, onPress }: { label: string; icon: keyof typeof Ionicons.glyphMap; primary?: boolean; selected?: boolean; disabled?: boolean; accent?: string; onPress?: () => void }) {
  // Primary keeps dark-on-blue; secondary is text-on-glass with a neon edge.
  // Selected ("Following") stays cyan so the follow state is legible without
  // relying on the fill alone.
  const tint = primary ? colors.background : selected ? profileNeon.cyan : colors.text;
  return (
    <Pressable
      accessibilityRole="button"
      accessibilityState={{ selected: Boolean(selected), disabled: Boolean(disabled) }}
      accessibilityLabel={label}
      disabled={disabled}
      style={({ pressed }) => [
        styles.action,
        primary ? styles.actionPrimary : styles.actionSecondary,
        selected && styles.actionSelected,
        disabled && styles.disabled,
        pressed && styles.pressed
      ]}
      onPress={onPress}
    >
      {/* Gradient only on the primary action. A themed profile overrides it with
          a flat accent fill, since a two-stop ramp cannot be derived from one
          arbitrary colour without guessing at a second. */}
      {primary ? (
        accent && accent !== profileNeon.electric ? (
          <View style={[StyleSheet.absoluteFill, styles.actionFill, { backgroundColor: accent }]} pointerEvents="none" />
        ) : (
          <LinearGradient
            colors={profileNeon.primaryAction}
            start={{ x: 0, y: 0 }}
            end={{ x: 1, y: 1 }}
            style={[StyleSheet.absoluteFill, styles.actionFill]}
            pointerEvents="none"
          />
        )
      ) : null}
      <Ionicons name={icon} size={16} color={tint} />
      <Text style={[styles.actionText, { color: tint }]} numberOfLines={1}>{disabled ? "Working…" : label}</Text>
    </Pressable>
  );
}

function Module({ def, accent, state, onPress }: { def: ModuleDef; accent: string; state?: ProfileModuleState; onPress?: () => void }) {
  return (
    <Pressable
      accessibilityRole="button"
      // A tile that carries state must announce the state, not just the noun.
      // "Premium" alone tells a VoiceOver user nothing about whether they are
      // subscribed, which is the entire question the tile exists to answer.
      accessibilityLabel={state?.accessibilityLabel || def.label}
      accessibilityHint={state?.accessibilityHint}
      style={({ pressed }) => [styles.module, pressed && styles.pressed]}
      onPress={onPress}
    >
      <View
        style={[
          styles.moduleIcon,
          { borderColor: `${accent}55`, backgroundColor: `${accent}12` },
          def.glow ? [styles.moduleGlow, { shadowColor: def.glow }] : null
        ]}
      >
        <Ionicons name={def.icon} size={22} color={accent} />
      </View>
      <Text style={styles.moduleLabel} numberOfLines={1}>{def.label}</Text>
      {/* Absent, not empty: no badge says nothing, a wrong word says something. */}
      {state?.status ? (
        <Text style={[styles.moduleStatus, { color: accent }]} numberOfLines={1}>{state.status}</Text>
      ) : null}
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

const styles = createThemedStyles(() => ({
  root: { backgroundColor: colors.background },
  hero: { overflow: "hidden", width: "100%" },
  coverImage: { ...StyleSheet.absoluteFillObject, height: undefined, width: undefined },
  automatedBrandCover: { position: "absolute", top: 0, width: "100%", aspectRatio: 1600 / 640 },
  nebula: { borderRadius: 220, height: 300, position: "absolute", right: -90, top: -70, width: 300 },
  nebulaTwo: { borderRadius: 160, height: 220, left: -70, position: "absolute", top: 40, width: 220 },
  pulseWave: { borderRadius: 200, borderWidth: 1.5, height: 320, left: "50%", marginLeft: -160, marginTop: -160, position: "absolute", top: "50%", width: 320 },
  grain: { ...StyleSheet.absoluteFillObject, backgroundColor: "rgba(5,9,16,0.12)" },
  // Oversized circle: only the top arc falls inside the hero, so it reads as a
  // planet limb. Width is fixed rather than a percentage because a percentage
  // border-radius is not reliable across RN platforms.
  horizon: { borderRadius: 480, borderWidth: 1, height: 960, left: "50%", marginLeft: -480, position: "absolute", top: 196, width: 960 },
  horizonGlow: { bottom: 0, height: 132, left: 0, position: "absolute", right: 0 },
  trail: { position: "absolute", width: 1 },
  trailLeft: { height: 150, left: "22%", top: 40, transform: [{ rotate: "14deg" }] },
  trailRight: { height: 120, right: "18%", top: 62, transform: [{ rotate: "-11deg" }] },

  body: { marginTop: -96, paddingHorizontal: 18 },
  avatarWrap: { alignItems: "center", justifyContent: "center", height: 128, width: 128 },
  ringOuter: { borderRadius: 72, borderWidth: 1, height: 144, position: "absolute", width: 144 },
  // One lit segment (borderTopColor) on an otherwise dim ring — the cheapest
  // way to imply rotation without animating anything.
  ringOrbit: { borderRadius: 69, borderWidth: 1, height: 138, position: "absolute", transform: [{ rotate: "-38deg" }], width: 138 },
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
  automationDisclosure: { backgroundColor: "rgba(244, 183, 64, 0.08)", borderColor: "rgba(244, 183, 64, 0.45)", borderRadius: 14, borderWidth: 1, gap: 5, marginTop: 14, padding: 14 },
  automationLabel: { color: "#f4c96b", fontSize: 13, fontWeight: "900" },
  automationTitle: { color: colors.text, fontSize: 11, fontWeight: "900", letterSpacing: 0.6 },
  automationTrustTitle: { color: colors.text, fontSize: 12, fontWeight: "900", marginTop: 6 },
  automationBody: { color: colors.muted, fontSize: 13, lineHeight: 19 },
  bio: { color: colors.text, fontSize: 15, lineHeight: 22, marginTop: 12 },
  bioMuted: { color: colors.muted, fontSize: 15, lineHeight: 22, marginTop: 12 },

  stats: { backgroundColor: profileNeon.panel, borderColor: profileNeon.border, borderRadius: profileNeon.radius.panel, borderWidth: 1, marginTop: 18, overflow: "hidden" },
  // Lit top edge of the panel, fading left to right.
  statsRail: { height: 2, left: 0, position: "absolute", right: 0, top: 0 },
  statsRow: { flexDirection: "row" },
  statCell: { flex: 1, flexDirection: "row" },
  // Inset at both ends so the rule floats inside the panel. `marginVertical`,
  // not top/bottom: the divider is a relative-positioned flex child, so Yoga
  // reads those as offsets, applies only `top`, and pushes a full-height line
  // past the bottom edge.
  statDivider: { marginVertical: 14, width: StyleSheet.hairlineWidth },
  stat: { alignItems: "center", flex: 1, justifyContent: "center", minHeight: 74, paddingHorizontal: 4, paddingVertical: 12 },
  statIcon: { marginBottom: 3, opacity: 0.85 },
  statValue: { fontSize: 21, fontWeight: "900", letterSpacing: 0.2 },
  statLabel: { fontSize: 10, fontWeight: "800", letterSpacing: 0.7, marginTop: 3, textTransform: "uppercase" },

  actions: { flexDirection: "row", flexWrap: "wrap", gap: 8, marginTop: 16 },
  action: { alignItems: "center", borderRadius: profileNeon.radius.action, borderWidth: 1, flexDirection: "row", flexGrow: 1, gap: 6, justifyContent: "center", minHeight: 48, minWidth: 92, overflow: "hidden", paddingHorizontal: 12 },
  actionPrimary: { borderColor: profileNeon.borderStrong },
  actionFill: { borderRadius: profileNeon.radius.action },
  actionSecondary: { backgroundColor: profileNeon.panel, borderColor: profileNeon.border },
  actionSelected: { backgroundColor: profileNeon.fillMedium, borderColor: profileNeon.cyan },
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
  moduleStatus: { fontSize: 9, fontWeight: "900", letterSpacing: 0.5, marginTop: -3 },
  // Elevation 0 on Android on purpose: the Material shadow is a drop shadow, so
  // it would render as a grey smear under the tile rather than a gold halo.
  moduleGlow: { elevation: 0, shadowOffset: { height: 0, width: 0 }, shadowOpacity: 0.55, shadowRadius: 10 },

  disabled: { opacity: 0.55 },
  pressed: { opacity: 0.7 }
}));
