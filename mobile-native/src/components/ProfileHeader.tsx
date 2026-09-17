import { Ionicons } from "@expo/vector-icons";
import { LinearGradient } from "expo-linear-gradient";
import * as Haptics from "expo-haptics";
import { useEffect, useMemo, useRef, useState } from "react";
import { ActivityIndicator, Animated, Image, Pressable, StyleSheet, Text, View } from "react-native";
import { PulseProfile, profileWebUrl } from "../api/profile";
import { hasMembershipMark } from "../entitlements/membershipMark";
import { colors } from "../theme/colors";
import { profileSurface } from "../theme/profileGraphite";
import { profileNeon, resolveProfileAccent, usesNeonRamp } from "../theme/profileNeon";
import { premiumTheme } from "../theme/premiumTheme";
import { presenceTheme } from "../theme/presenceTheme";
import { progressTheme } from "../theme/progressTheme";
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
  // Fixed brand teal, like Progress/Premium survive the accent override:
  // Presence is the door to the member's professional identities and must stay
  // legible on any profile theme. Id-card icon: identity, not rank.
  { key: "presence", label: "Presence", icon: "id-card-outline", accent: presenceTheme.teal },
  { key: "memories", label: "Memories", icon: "time-outline" },
  { key: "progress", label: "Progress", icon: "trending-up-outline", accent: progressTheme.violet },
  // Diamond, not a crown: a crown reads as rank over other members, which is
  // exactly what this tile must not imply. Premium is an account, not a status.
  { key: "premium", label: "Premium", icon: "diamond-outline", accent: premiumTheme.gold }
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
  /**
   * Whether this viewer may change the identity media on this profile.
   *
   * Separate from `owner` on purpose. `owner` is "the Profile tab opened with no
   * route target", which is false when you reach your own profile by tapping
   * your own name in the feed — and on that screen the edit controls still
   * belong to you. The screen passes the server-settled answer instead. The
   * backend re-checks ownership on every upload regardless; this only decides
   * what is drawn.
   */
  canEditMedia?: boolean;
  onEditCover?: () => void;
  onEditAvatar?: () => void;
  /** An upload is in flight. The control stays put and reports itself busy. */
  avatarBusy?: boolean;
  coverBusy?: boolean;
};

function haptic() {
  Haptics.impactAsync(Haptics.ImpactFeedbackStyle.Light).catch(() => undefined);
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
  moduleOwnerName,
  canEditMedia,
  onEditCover,
  onEditAvatar,
  avatarBusy,
  coverBusy
}: ProfileHeaderProps) {
  // `profileSurface`, not `resolveProfileSurface` wrapped in a `useMemo`: the
  // cache lives in the token module and is keyed on the palette values the
  // resolver reads, so this component, its four sub-components and
  // `ProfileCanvas` all share one object per theme. A `useMemo` here would have
  // had to declare six palette fields as dependencies to be honest about what it
  // reads, and would still have allocated a second object for each of the small
  // components that cannot see this scope.
  const surface = profileSurface(colors);
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
  // Display mark only, and deliberately so: this header also draws *other
  // people's* profiles, where the canonical endpoint cannot answer because it
  // takes no user parameter. Nothing on this surface gates a capability off it.
  const premium = hasMembershipMark(profile.premium_status);
  const verified = Boolean(profile.verified_badge || profile.verification_status === "verified");
  // Blue is the default identity colour of the profile surface; a profile
  // owner's chosen accent still overrides it, so customised profiles are
  // untouched. See theme/profileNeon.ts for why this is not a global change,
  // and why the server's default accent has to be resolved rather than trusted.
  const accent = resolveProfileAccent(profile.theme?.accent_color);
  const neonRamp = usesNeonRamp(accent);
  const tierLabel = premium ? String(profile.premium_status || "premium").replace(/_/g, " ") : "";
  const online = String(profile.account_status || "active").toLowerCase() === "active";
  const automated = profile.automated === true || profile.account_type === "PULSESOC_AUTOMATED";
  const galacticAccountCover = profile.public_player_id === "pulsesoc_insight"
    && Boolean(profile.cover_url?.includes("pulsesoc-insight-cover-20260825.png"));
  // The energy field below is a *generated cover* for accounts that never set
  // one, so its normal strength is calibrated to be the image rather than to
  // sit over one. When the user has supplied a photo the same geometry drops to
  // framing intensity: the cover is the user's content and has to win.
  //
  // Stepping down on `cover_url` alone dims the field over a photo that never
  // arrives, which is strictly worse than before — the decoration is the only
  // thing lighting the hero in that case. Keying on "has not errored" is not
  // enough either: a cover request that is cancelled rather than failed (a
  // remount mid-flight reports NSURLError -999) never reaches `onError`, so the
  // field stays dim forever behind nothing. The step-down therefore waits for
  // the picture to actually arrive, and the field is the placeholder until it
  // does.
  const coverUrl = galacticAccountCover ? "" : profile.cover_url || "";
  const [coverLoaded, setCoverLoaded] = useState(false);
  useEffect(() => { setCoverLoaded(false); }, [coverUrl]);
  const hasCover = Boolean(coverUrl) && coverLoaded;

  // Four ambient loops used to run here for the life of the screen: a breathing
  // avatar aura, two drifting nebulae and an expanding wave. All four are gone.
  //
  // They were the performance cost of this surface and they were also the reason
  // reduced motion needed a branch at all — a permanently animating decoration
  // has to be special-cased forever, and the only users who ever saw the
  // "correct" static version were the ones who had asked for less motion. With
  // the loops removed the hero is static for everyone, so the reduced-motion
  // behaviour is now the *only* behaviour rather than an exception path, and
  // there is no per-frame work on the profile at rest.
  //
  // What remains animated is scroll-driven only: the parallax and compression
  // below are gestures the user is performing, they run on the native driver,
  // and they stop when the finger does.

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
      <View testID="profile-hero" style={[styles.hero, { height: PROFILE_HERO_HEIGHT }]} pointerEvents="none">
        <Animated.View style={[StyleSheet.absoluteFill, { opacity: fieldOpacity, transform: [{ translateY: bgTranslateY }, { scale: bgScale }] }]}>
          {coverUrl ? (
            <Image
              testID="profile-cover-image"
              source={{ uri: coverUrl }}
              style={styles.coverImage}
              resizeMode="cover"
              onLoad={() => setCoverLoaded(true)}
            />
          ) : null}
          {/* Over a real cover this ramp becomes a vignette, not a tint: the
              centre — where a face sits — is left clear, and only the bottom
              darkens, because the avatar and the page blend need that contrast.
              The diagonal is kept for the generated field, where the gradient
              IS the artwork. */}
          <LinearGradient
            colors={hasCover
              ? [`${accent}1a`, "transparent", surface.coverScrim]
              : [`${accent}33`, surface.coverFieldMid, surface.canvasTop]}
            start={hasCover ? { x: 0.5, y: 0 } : { x: 0.1, y: 0 }}
            end={hasCover ? { x: 0.5, y: 1 } : { x: 0.9, y: 1 }}
            style={StyleSheet.absoluteFill}
          />
          <View style={[styles.nebula, { backgroundColor: `${accent}${hasCover ? "14" : "2e"}` }]} />
          <View style={[styles.nebulaTwo, { backgroundColor: `${profileNeon.violet}${hasCover ? "0f" : "22"}` }]} />
          {/* Planetary curve. A single oversized circle clipped by the hero's
              own overflow:hidden — no SVG, no image payload, one static view.
              The border is the lit limb; the fill is barely there so the name
              above it never loses contrast. Over a cover the limb also drops
              lower, so the brightest geometry on the screen stops crossing the
              middle of the photo where the subject usually is. */}
          <View testID="profile-generated-cover" style={[styles.horizon, hasCover && styles.horizonFramed, { borderColor: hasCover ? profileNeon.borderFramed : profileNeon.borderStrong, backgroundColor: hasCover ? profileNeon.fillFramed : profileNeon.fillSoft }]} pointerEvents="none" />
          <LinearGradient
            colors={hasCover ? profileNeon.horizonFramed : profileNeon.horizon}
            start={{ x: 0.5, y: 1 }}
            end={{ x: 0.5, y: 0 }}
            style={styles.horizonGlow}
            pointerEvents="none"
          />
          {/* Light trails: two hairlines converging on the horizon. Static, so
              they cost one layout each and nothing per frame. */}
          <View style={[styles.trail, styles.trailLeft, { backgroundColor: surface.divider }]} pointerEvents="none" />
          <View style={[styles.trail, styles.trailRight, { backgroundColor: surface.divider }]} pointerEvents="none" />
          {/* The expanding wave that used to sit here has been removed, not
              dimmed. It ran continuously from the dead centre of the hero —
              exactly where a face lands — and a permanently animating ring is
              both the "no continuous animation" rule and a reduced-motion
              exclusion that has to be special-cased forever. The field reads as
              engineered without it; the horizon limb already does that job.

              The full-bleed "grain" tint is gone for the same reason: over an
              uploaded cover it was a translucent layer across the whole
              photograph, which is the definition of the fog this surface is not
              allowed to have, and 0.04 alpha is still fog. Under the generated
              field it was doing nothing the gradient's own last stop does not
              already do. */}
        </Animated.View>
        {/* Blend into the page body. Evenly spaced stops put the ramp's start at
            half the hero's height, which over a photo crushes everything below
            the subject. Over a cover the same blend is confined to the bottom
            quarter, where the avatar needs it and the picture is already gone. */}
        <LinearGradient
          colors={["transparent", "transparent", surface.canvasTop]}
          locations={hasCover ? [0, 0.74, 1] : undefined}
          style={StyleSheet.absoluteFill}
          pointerEvents="none"
        />
        {galacticAccountCover ? (
          <Image testID="automated-account-brand-cover" source={{ uri: profile.cover_url }}
            style={styles.automatedBrandCover} resizeMode="contain" />
        ) : null}
      </View>

      {/* Identity */}
      <View style={styles.body}>
        <View style={styles.avatarWrap}>
          {/* Three concentric decorative rings became one.
              The breathing outer aura and the static "orbit" ring were pure
              decoration: they carried no state, and read as three haloes around
              a photograph. What is kept is the ring that identifies — the
              gradient identity ring — and every functional indicator around it
              (verification seal, presence dot, camera control) is untouched.
              Removing the aura also removes a permanent opacity+scale loop.

              A gradient cannot be a `borderColor`, so on the neon ramp the ramp
              fills a circle and a core the colour of the canvas is laid back over
              the middle. The core is opaque canvas rather than transparent
              because at this height the hero's blend is only ~62% of the way to
              the page, so a transparent hole would show the cover through it. A
              themed profile keeps the plain 2pt border, since one arbitrary
              accent gives nothing to interpolate towards. */}
          <View
            style={[
              styles.ringGlow,
              neonRamp ? styles.ringGlowRamp : { borderColor: accent, borderWidth: 2 }
            ]}
            pointerEvents="none"
          >
            {neonRamp ? (
              <>
                <LinearGradient testID="profile-identity-ring" colors={profileNeon.identityRing} start={{ x: 0.15, y: 0 }} end={{ x: 0.85, y: 1 }} style={styles.ringRamp} />
                <View style={[styles.ringCore, { backgroundColor: surface.canvasTop }]} />
              </>
            ) : null}
          </View>
          <Animated.View style={{ transform: [{ scale: avatarScale }, { translateY: avatarLift }] }}>
            {canEditMedia ? (
              <Pressable
                accessibilityRole="button"
                accessibilityLabel="Change profile photo"
                accessibilityState={{ busy: Boolean(avatarBusy), disabled: Boolean(avatarBusy) }}
                testID="profile-edit-avatar"
                disabled={Boolean(avatarBusy)}
                style={({ pressed }) => [pressed && styles.pressed]}
                onPress={onEditAvatar}
              >
                <AvatarFace profile={profile} accent={accent} />
                {/* Top-right: the two lower corners are already spoken for by the
                    verification seal and the presence dot, and neither may move
                    to make room for a control only the owner ever sees. */}
                <View style={[styles.avatarCamera, { backgroundColor: accent, borderColor: colors.background }]}>
                  {avatarBusy ? (
                    <ActivityIndicator size="small" color={colors.background} />
                  ) : (
                    <Ionicons name="camera" size={15} color={colors.background} />
                  )}
                </View>
              </Pressable>
            ) : (
              <AvatarFace profile={profile} accent={accent} />
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
        <View style={[styles.stats, { backgroundColor: surface.raised, borderColor: surface.border }]} accessibilityLabel="Profile statistics">
          {/* Lit top edge. A solid 1pt rule, not the left-to-right gradient that
              was here: the requirement is a subtle *solid* highlight, and a
              gradient rail on an elevated card is a second light source. One
              fewer LinearGradient on the screen, too. */}
          <View style={[styles.statsRail, { backgroundColor: surface.divider }]} pointerEvents="none" />
          <View style={styles.statsRow}>
            {statEntries.map((entry, index) => (
              <View key={entry.key} style={styles.statCell}>
                {index > 0 ? <View style={[styles.statDivider, { backgroundColor: surface.divider }]} /> : null}
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
          {modules.map((module, index) => (
            <Module
              key={module.key}
              def={module}
              // Precedence, highest first: a live state tint (a billing problem
              // turns Business amber and must win), the tile's own brand colour,
              // then the palette. On the neon ramp the palette is a rotation by
              // grid position rather than one accent twelve times; a themed
              // profile still gets its single chosen colour throughout.
              accent={moduleState?.[module.key]?.tint || module.accent
                || (neonRamp ? profileNeon.tileCycle[index % profileNeon.tileCycle.length] : accent)}
              state={moduleState?.[module.key]}
              onPress={() => { haptic(); onModulePress?.(module.key); }}
            />
          ))}
        </View>
      </View>

      {/* Last child of the header on purpose. The hero above is
          `pointerEvents="none"` so nothing inside it can be tapped, and `body`
          is a full-width transparent view that would otherwise swallow a touch
          landing in the hero's lower-right corner. Rendering the control here
          puts it above both for hit-testing without moving it visually. */}
      {canEditMedia ? (
        <Pressable
          accessibilityRole="button"
          accessibilityLabel="Edit cover photo"
          accessibilityState={{ busy: Boolean(coverBusy), disabled: Boolean(coverBusy) }}
          testID="profile-edit-cover"
          disabled={Boolean(coverBusy)}
          style={({ pressed }) => [styles.coverEdit, pressed && styles.pressed]}
          onPress={onEditCover}
        >
          {coverBusy ? (
            <ActivityIndicator size="small" color={colors.text} />
          ) : (
            <Ionicons name="camera-outline" size={15} color={colors.text} />
          )}
          <Text style={styles.coverEditText}>{coverBusy ? "Uploading…" : "Edit cover"}</Text>
        </Pressable>
      ) : null}
    </View>
  );
}

/**
 * The avatar itself — image, or the member's initial when there is none.
 *
 * Extracted so the owner's tappable version and a visitor's static version are
 * literally the same face, rather than two copies that can drift apart.
 */
function AvatarFace({ profile, accent }: { profile: PulseProfile; accent: string }) {
  if (profile.avatar_url) {
    return <Image source={{ uri: profile.avatar_url }} style={[styles.avatar, { borderColor: accent }]} />;
  }
  return (
    <View style={[styles.avatarFallback, { borderColor: accent }]}>
      <Text style={styles.avatarText}>{(profile.display_name || "?").slice(0, 1).toUpperCase()}</Text>
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
      {/* Colour comes from the stylesheet now rather than an inline
          `colors.text`: the count is the loudest thing in the panel and has to
          be the graphite ramp's primary weight (8.16:1 on `raised`), which is
          not the same value as the global palette's text role. */}
      <Text style={styles.statValue} numberOfLines={1} adjustsFontSizeToFit minimumFontScale={0.6}>{formatCount(value)}</Text>
      <Text style={[styles.statLabel, { color: accent }]} numberOfLines={1}>{label}</Text>
    </Pressable>
  );
}

function Action({ label, icon, primary, selected, disabled, accent, onPress }: { label: string; icon: keyof typeof Ionicons.glyphMap; primary?: boolean; selected?: boolean; disabled?: boolean; accent?: string; onPress?: () => void }) {
  // Primary keeps dark-on-blue; secondary is text on an opaque elevated step
  // with a steel edge. Selected ("Following") stays cyan so the follow state is
  // legible without relying on the fill alone.
  const tint = primary ? colors.background : selected ? profileNeon.cyan : profileSurface(colors).primaryText;
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
      {/* Opaque graphite fill from the stylesheet; the accent is carried by the
          border and the glyph only. The fill used to be `${accent}12` — a 7%
          accent wash over the canvas — which is both a translucent layer over
          the page and, at that alpha, indistinguishable from the page. An
          elevated step plus an accent edge keeps the tile's identity (teal,
          violet, gold, and the four-hue cycle) while making it read as a tile.
          No `shadowColor`/`shadowRadius`: the two halo tiles are now identified
          by their fixed accent, not by a permanent glow. */}
      <View style={[styles.moduleIcon, { borderColor: `${accent}55` }]}>
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
      <Ionicons name={icon} size={13} color={profileSurface(colors).secondaryText} />
      <Text style={styles.utilityText}>{label}</Text>
    </Pressable>
  );
}

function formatCount(value: number) {
  if (value >= 1_000_000) return `${(value / 1_000_000).toFixed(value >= 10_000_000 ? 0 : 1)}M`;
  if (value >= 1_000) return `${(value / 1_000).toFixed(value >= 10_000 ? 0 : 1)}K`;
  return String(value);
}

const styles = createThemedStyles(() => {
  // Resolved inside the factory, which is the memoised path: `createThemedStyles`
  // rebuilds only when `applyPaletteToLegacyColors` bumps the palette epoch, so
  // this runs once per theme change rather than once per render — and the small
  // sub-components below (Stat, Action, Module, Utility) get the resolved surface
  // through the stylesheet without each needing to resolve it themselves.
  const surface = profileSurface(colors);
  return {
  // Transparent, not the canvas colour. `ProfileCanvas` draws one gradient behind
  // the whole scroll view, and painting an opaque `canvasTop` here would end it
  // at the bottom of the header — a visible seam against a gradient that is by
  // then already partway to `canvasBottom`. One canvas, one run, no joins.
  root: { backgroundColor: "transparent" },
  hero: { overflow: "hidden", width: "100%" },
  coverImage: { ...StyleSheet.absoluteFillObject, height: undefined, width: undefined },
  automatedBrandCover: { position: "absolute", top: 0, width: "100%", aspectRatio: 1600 / 640 },
  nebula: { borderRadius: 220, height: 300, position: "absolute", right: -90, top: -70, width: 300 },
  nebulaTwo: { borderRadius: 160, height: 220, left: -70, position: "absolute", top: 40, width: 220 },
  // Oversized circle: only the top arc falls inside the hero, so it reads as a
  // planet limb. Width is fixed rather than a percentage because a percentage
  // border-radius is not reliable across RN platforms.
  horizon: { borderRadius: 480, borderWidth: 1, height: 960, left: "50%", marginLeft: -480, position: "absolute", top: 196, width: 960 },
  // 56px lower, which puts the lit limb in the bottom quarter of the hero
  // instead of across its middle. Subject safety, not decoration.
  horizonFramed: { top: 252 },
  horizonGlow: { bottom: 0, height: 132, left: 0, position: "absolute", right: 0 },
  trail: { position: "absolute", width: 1 },
  trailLeft: { height: 150, left: "22%", top: 40, transform: [{ rotate: "14deg" }] },
  trailRight: { height: 120, right: "18%", top: 62, transform: [{ rotate: "-11deg" }] },

  body: { marginTop: -96, paddingHorizontal: 18 },
  avatarWrap: { alignItems: "center", justifyContent: "center", height: 128, width: 128 },
  // No `shadowOpacity`/`shadowRadius` any more: a 22pt halo around the avatar was
  // a large soft shadow on the brightest element of the screen, which the brief
  // rules out, and the gradient ring already identifies the profile.
  ringGlow: { borderRadius: 66, height: 132, position: "absolute", width: 132 },
  ringGlowRamp: { overflow: "hidden" },
  ringRamp: { ...StyleSheet.absoluteFillObject, borderRadius: 66 },
  ringCore: { ...StyleSheet.absoluteFillObject, borderRadius: 64, bottom: 2, left: 2, right: 2, top: 2 },
  avatar: { backgroundColor: surface.raised, borderRadius: 56, borderWidth: 3, height: 112, width: 112 },
  avatarFallback: { alignItems: "center", backgroundColor: surface.raised, borderRadius: 56, borderWidth: 3, height: 112, justifyContent: "center", width: 112 },
  avatarText: { color: surface.primaryText, fontSize: 40, fontWeight: "900" },
  verifiedSeal: { alignItems: "center", borderRadius: 14, borderWidth: 2, bottom: 6, height: 28, justifyContent: "center", position: "absolute", right: 2, width: 28 },
  presenceDot: { borderRadius: 9, borderWidth: 3, bottom: 8, height: 18, left: 6, position: "absolute", width: 18 },
  avatarCamera: { alignItems: "center", borderRadius: 16, borderWidth: 2, height: 32, justifyContent: "center", position: "absolute", right: 0, top: 0, width: 32 },
  // Sits in the hero's lower-right, clear of the avatar on the left.
  //
  // `chrome`, the darkest step, not `raised`: this is the one Profile control that
  // sits on top of the member's own photograph rather than on the page, and chrome
  // is the role for a surface that frames content. It was translucent navy glass
  // before, which over an arbitrary photo is a different colour on every profile —
  // an opaque step is the only version whose contrast is knowable.
  coverEdit: {
    alignItems: "center",
    backgroundColor: surface.chrome,
    borderColor: surface.border,
    borderRadius: 999,
    borderWidth: 1,
    flexDirection: "row",
    gap: 6,
    justifyContent: "center",
    minHeight: profileNeon.tapTarget,
    paddingHorizontal: 14,
    position: "absolute",
    right: 18,
    top: PROFILE_HERO_HEIGHT - 84
  },
  coverEditText: { color: surface.primaryText, fontSize: 12, fontWeight: "900" },

  nameRow: { alignItems: "center", flexDirection: "row", gap: 6, marginTop: 14 },
  name: { color: surface.primaryText, flexShrink: 1, fontSize: 28, fontWeight: "900", letterSpacing: 0.2 },
  nameVerified: { marginTop: 2 },
  handle: { color: surface.secondaryText, fontSize: 14, marginTop: 3 },
  badges: { flexDirection: "row", flexWrap: "wrap", gap: 6, marginTop: 10 },
  badge: { alignItems: "center", borderRadius: 999, borderWidth: 1, flexDirection: "row", gap: 4, paddingHorizontal: 9, paddingVertical: 5 },
  badgeText: { fontSize: 11, fontWeight: "900", textTransform: "capitalize" },
  automationDisclosure: { backgroundColor: "rgba(244, 183, 64, 0.08)", borderColor: "rgba(244, 183, 64, 0.45)", borderRadius: 14, borderWidth: 1, gap: 5, marginTop: 14, padding: 14 },
  automationLabel: { color: "#f4c96b", fontSize: 13, fontWeight: "900" },
  automationTitle: { color: surface.primaryText, fontSize: 11, fontWeight: "900", letterSpacing: 0.6 },
  automationTrustTitle: { color: surface.primaryText, fontSize: 12, fontWeight: "900", marginTop: 6 },
  automationBody: { color: surface.secondaryText, fontSize: 13, lineHeight: 19 },
  bio: { color: surface.primaryText, fontSize: 15, lineHeight: 22, marginTop: 12 },
  bioMuted: { color: surface.secondaryText, fontSize: 15, lineHeight: 22, marginTop: 12 },

  // The elevated step, opaque, with the steel edge and no shadow at all. The
  // fill is +4.71pp HSL lightness over the canvas — the middle of the brief's
  // 4–6% band — which is what separates the panel; the border finishes it, and
  // is the only thing carrying the edge on the light themes where the page and
  // the panel are the same colour.
  stats: { backgroundColor: surface.raised, borderColor: surface.border, borderRadius: profileNeon.radius.panel, borderWidth: 1, marginTop: 18, overflow: "hidden" },
  // Lit top edge of the panel. Solid, per the brief: it was a three-stop
  // `LinearGradient`, which is a composited layer and a second gradient on the
  // screen to buy a 2pt line.
  statsRail: { height: 2, left: 0, position: "absolute", right: 0, top: 0 },
  statsRow: { flexDirection: "row" },
  statCell: { flex: 1, flexDirection: "row" },
  // Inset at both ends so the rule floats inside the panel. `marginVertical`,
  // not top/bottom: the divider is a relative-positioned flex child, so Yoga
  // reads those as offsets, applies only `top`, and pushes a full-height line
  // past the bottom edge.
  //
  // 1pt, not `StyleSheet.hairlineWidth`. On a 3x device hairline is 0.33pt, and
  // the brief asks for *clear* column separation: a third of a point of a 16%
  // divider over a mid graphite is not a line anyone can see.
  statDivider: { marginVertical: 14, width: 1 },
  stat: { alignItems: "center", flex: 1, justifyContent: "center", minHeight: 74, paddingHorizontal: 4, paddingVertical: 12 },
  statIcon: { marginBottom: 3, opacity: 0.85 },
  statValue: { color: surface.primaryText, fontSize: 21, fontWeight: "900", letterSpacing: 0.2 },
  statLabel: { fontSize: 10, fontWeight: "800", letterSpacing: 0.7, marginTop: 3, textTransform: "uppercase" },

  actions: { flexDirection: "row", flexWrap: "wrap", gap: 8, marginTop: 16 },
  action: { alignItems: "center", borderRadius: profileNeon.radius.action, borderWidth: 1, flexDirection: "row", flexGrow: 1, gap: 6, justifyContent: "center", minHeight: 48, minWidth: 92, overflow: "hidden", paddingHorizontal: 12 },
  actionPrimary: { borderColor: profileNeon.borderStrong },
  actionFill: { borderRadius: profileNeon.radius.action },
  actionSecondary: { backgroundColor: surface.raised, borderColor: surface.border },
  actionSelected: { backgroundColor: profileNeon.fillMedium, borderColor: profileNeon.cyan },
  actionText: { fontSize: 13, fontWeight: "900" },

  modulesHeader: { alignItems: "center", flexDirection: "row", justifyContent: "space-between", marginTop: 26 },
  modulesTitle: { color: surface.primaryText, fontSize: 16, fontWeight: "900", letterSpacing: 0.3 },
  utilityRow: { flexDirection: "row", gap: 4 },
  utility: { alignItems: "center", flexDirection: "row", gap: 4, paddingHorizontal: 8, paddingVertical: 6 },
  utilityText: { color: surface.secondaryText, fontSize: 11, fontWeight: "800" },
  moduleGrid: { flexDirection: "row", flexWrap: "wrap", marginTop: 14 },
  module: { alignItems: "center", gap: 7, marginBottom: 18, width: "25%" },
  // `raisedStrong`, the tile step, and it is why tiles sit on the canvas and
  // never inside a card: it is 1.243:1 against the canvas but only 1.032:1
  // against `raised`, so a tile drawn on the statistics panel would be invisible.
  moduleIcon: { alignItems: "center", backgroundColor: surface.raisedStrong, borderRadius: 20, borderWidth: 1, height: 58, justifyContent: "center", width: 58 },
  moduleLabel: { color: surface.secondaryText, fontSize: 11, fontWeight: "800" },
  moduleStatus: { fontSize: 9, fontWeight: "900", letterSpacing: 0.5, marginTop: -3 },

  disabled: { opacity: 0.55 },
  pressed: { opacity: 0.7 }
  };
});
