/**
 * Pulse Identity — the first Profile OS tile, and the reference for the other
 * thirteen.
 *
 * The screen is about `profileOwnerId` from its route params, not about the
 * signed-in account. Previously it called `getMyProfile()` unconditionally, so
 * opening it from Maria's profile showed Roody's Pulse ID, Roody's QR code and
 * links into Roody's Business OS and wallet.
 *
 * Two rules make that unrepeatable here:
 *   1. The fetch is chosen by the resolved context. A visitor path calls
 *      `getPublicProfile(lookupKey)` and never touches `/api/pulse/profile/me`.
 *   2. Owner-only rows render behind `context.isOwnProfile`, and the identity
 *      state is keyed by owner id so a previous profile's payload cannot be
 *      shown under a new one while its fetch is still in flight.
 */

import { NativeStackScreenProps } from "@react-navigation/native-stack";
import { useEffect, useState } from "react";
import { ActivityIndicator, Pressable, ScrollView, StyleSheet, Text, View } from "react-native";
import { getMyProfile, getPublicProfile, PulseProfile } from "../api/profile";
import { RootStackParamList } from "../navigation/types";
import {
  destinationTitle,
  resolveRouteProfileContext,
  subjectName,
  withServerPermissions
} from "../profile/profileContext";
import { useAuth } from "../session/auth";
import { colors } from "../theme/colors";
import { createThemedStyles } from "../theme/themedStyles";

type Props = NativeStackScreenProps<RootStackParamList, "PulseIdentity">;

export function PulseIdentityScreen({ navigation, route }: Props) {
  const { authState } = useAuth();
  const routeContext = resolveRouteProfileContext(route?.params, authState.user?.user_id);
  const [profile, setProfile] = useState<PulseProfile | null>(null);
  const [error, setError] = useState("");
  // Which owner the profile in state belongs to. Rendering is gated on this
  // matching the owner we are currently meant to show, so a slow response for
  // the previous profile can never paint under the new one's header.
  const [loadedOwnerId, setLoadedOwnerId] = useState("");

  const ownerId = routeContext.profileOwnerId;
  const lookupKey = routeContext.lookupKey;
  const isOwnProfile = routeContext.isOwnProfile;

  useEffect(() => {
    let cancelled = false;
    setProfile(null);
    setLoadedOwnerId("");
    setError("");

    // The owner branch is the only one allowed to call the personal endpoint.
    const request = isOwnProfile ? getMyProfile() : getPublicProfile(lookupKey || ownerId);

    request
      .then((next) => {
        if (cancelled) return;
        setProfile(next);
        setLoadedOwnerId(ownerId);
      })
      .catch(() => {
        if (cancelled) return;
        setError("Pulse Identity could not load. Try again.");
      });

    // Cancelling on owner change is what stops a stale profile from landing in
    // state after the user has already moved to a different profile.
    return () => {
      cancelled = true;
    };
  }, [ownerId, lookupKey, isOwnProfile]);

  const context = withServerPermissions(routeContext, profile);
  const ready = Boolean(profile) && loadedOwnerId === ownerId;

  if (!ready && !error) {
    return (
      <View style={styles.loading}>
        <ActivityIndicator color={colors.accent} />
      </View>
    );
  }
  if (!ready || !profile) {
    return (
      <View style={styles.loading}>
        <Text style={styles.error}>{error || "Pulse Identity could not load. Try again."}</Text>
      </View>
    );
  }

  // Linked accounts are management entry points into the viewer's own Business
  // OS, store, wallet and creator tools. They are owner-only by definition — on
  // a visitor's screen they would be links into the wrong person's account.
  const links: Array<[string, () => void]> = isOwnProfile
    ? [
        ["Linked Businesses", () => navigation.navigate("BusinessOs", { title: "Business OS" })],
        ["Linked Stores", () => navigation.navigate("SellerStore", { title: "Seller / Store" })],
        ["Linked Communities", () => navigation.navigate("Tabs", { screen: "Groups" })],
        ["Linked Creator Pages", () => navigation.navigate("CreatorStudio")],
        ["Connected Wallets", () => navigation.navigate("BusinessOsPayments", { title: "Wallet" })]
      ]
    : [];

  return (
    <ScrollView style={styles.root} contentContainerStyle={styles.content}>
      <Text style={styles.kicker}>PULSESOC IDENTITY</Text>
      <Text style={styles.title}>{destinationTitle(context, "Pulse Identity")}</Text>
      <Text style={styles.name}>{profile.display_name}</Text>
      <Text style={styles.username}>{profile.username ? `@${profile.username}` : "Username not set"}</Text>
      {profile.bio ? <Text style={styles.bio}>{profile.bio}</Text> : null}
      <View style={styles.panel}>
        <Row label="Username" value={profile.username ? `@${profile.username}` : "Not set"} />
        <Row label="Display Name" value={profile.display_name} />
        <Row label="Verification" value={profile.verified_badge ? "Verified" : profile.verification_status || "Not verified"} />
      </View>
      {links.map(([label, onPress]) => (
        <Pressable key={label} accessibilityRole="button" style={styles.link} onPress={onPress}>
          <Text style={styles.linkText}>{label}</Text>
          <Text style={styles.arrow}>›</Text>
        </Pressable>
      ))}
      {isOwnProfile ? null : (
        <Text style={styles.visitorNote}>
          {`This is ${subjectName(context)}'s public Pulse Identity.`}
        </Text>
      )}
    </ScrollView>
  );
}

function Row({ label, value }: { label: string; value: string }) { return <View style={styles.row}><Text style={styles.label}>{label}</Text><Text style={styles.value}>{value}</Text></View>; }

const styles = createThemedStyles(() => ({ root: { flex: 1, backgroundColor: colors.background }, content: { padding: 22, paddingBottom: 60 }, loading: { flex: 1, alignItems: "center", justifyContent: "center", backgroundColor: colors.background }, error: { color: colors.danger }, kicker: { color: colors.accent, fontSize: 11, fontWeight: "900", letterSpacing: 1.8 }, title: { color: colors.text, fontSize: 32, fontWeight: "900", marginTop: 8 }, name: { color: colors.text, fontSize: 20, fontWeight: "800", marginTop: 22 }, username: { color: colors.muted, fontSize: 15, marginTop: 3 }, qr: { alignSelf: "center", padding: 16, borderRadius: 22, backgroundColor: "#FFFFFF", marginTop: 28 }, qrLabel: { color: colors.muted, textAlign: "center", marginTop: 8 }, bio: { color: colors.text, fontSize: 14, lineHeight: 20, marginTop: 18 }, panel: { marginTop: 24, borderWidth: 1, borderColor: colors.border, borderRadius: 16, backgroundColor: colors.surface, paddingHorizontal: 16 }, row: { paddingVertical: 14, borderBottomWidth: StyleSheet.hairlineWidth, borderBottomColor: colors.border }, label: { color: colors.muted, fontSize: 11, fontWeight: "800", letterSpacing: 0.8 }, value: { color: colors.text, fontSize: 15, fontWeight: "700", marginTop: 4 }, link: { minHeight: 54, marginTop: 10, borderRadius: 14, borderWidth: 1, borderColor: colors.border, backgroundColor: colors.surface, flexDirection: "row", alignItems: "center", justifyContent: "space-between", paddingHorizontal: 16 }, linkText: { color: colors.text, fontWeight: "800" }, arrow: { color: colors.accent, fontSize: 26 }, visitorNote: { color: colors.muted, fontSize: 12, marginTop: 20, textAlign: "center" } }));
