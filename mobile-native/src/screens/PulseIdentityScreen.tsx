import { NativeStackScreenProps } from "@react-navigation/native-stack";
import { useEffect, useState } from "react";
import { ActivityIndicator, Pressable, ScrollView, StyleSheet, Text, View } from "react-native";
import QRCode from "react-native-qrcode-svg";
import { getMyProfile, PulseProfile } from "../api/profile";
import { PulseIdBadge } from "../components/PulseIdBadge";
import { RootStackParamList } from "../navigation/types";
import { colors } from "../theme/colors";

type Props = NativeStackScreenProps<RootStackParamList, "PulseIdentity">;

export function PulseIdentityScreen({ navigation }: Props) {
  const [profile, setProfile] = useState<PulseProfile | null>(null);
  const [error, setError] = useState("");
  useEffect(() => { getMyProfile().then(setProfile).catch(() => setError("Pulse Identity could not load. Try again.")); }, []);
  if (!profile && !error) return <View style={styles.loading}><ActivityIndicator color={colors.accent} /></View>;
  if (!profile) return <View style={styles.loading}><Text style={styles.error}>{error}</Text></View>;
  const identityUrl = `https://pulsesoc.com/pulse/id/${encodeURIComponent(profile.pulse_id || "")}`;
  const links: Array<[string, () => void]> = [
    ["Linked Businesses", () => navigation.navigate("BusinessOs", { title: "Business OS" })],
    ["Linked Stores", () => navigation.navigate("SellerStore", { title: "Seller / Store" })],
    ["Linked Communities", () => navigation.navigate("Tabs", { screen: "Groups" })],
    ["Linked Creator Pages", () => navigation.navigate("CreatorStudio")],
    ["Connected Wallets", () => navigation.navigate("BusinessOsPayments", { title: "Wallet" })]
  ];
  return (
    <ScrollView style={styles.root} contentContainerStyle={styles.content}>
      <Text style={styles.kicker}>PERMANENT PULSESOC IDENTITY</Text>
      <Text style={styles.title}>Pulse Identity</Text>
      <Text style={styles.name}>{profile.display_name}</Text>
      <Text style={styles.username}>{profile.username ? `@${profile.username}` : "Username not set"}</Text>
      <PulseIdBadge pulseId={profile.pulse_id} />
      <View style={styles.qr}><QRCode value={identityUrl} size={190} backgroundColor="#FFFFFF" color="#06101B" /></View>
      <Text style={styles.qrLabel}>QR Identity</Text>
      <View style={styles.panel}>
        <Row label="Pulse ID" value={profile.pulse_id || "Provisioning"} />
        <Row label="Username" value={profile.username ? `@${profile.username}` : "Not set"} />
        <Row label="Display Name" value={profile.display_name} />
        <Row label="Verification" value={profile.verified_badge ? "Verified" : profile.verification_status || "Not verified"} />
      </View>
      {links.map(([label, onPress]) => <Pressable key={label} accessibilityRole="button" style={styles.link} onPress={onPress}><Text style={styles.linkText}>{label}</Text><Text style={styles.arrow}>›</Text></Pressable>)}
    </ScrollView>
  );
}

function Row({ label, value }: { label: string; value: string }) { return <View style={styles.row}><Text style={styles.label}>{label}</Text><Text style={styles.value}>{value}</Text></View>; }

const styles = StyleSheet.create({ root: { flex: 1, backgroundColor: colors.background }, content: { padding: 22, paddingBottom: 60 }, loading: { flex: 1, alignItems: "center", justifyContent: "center", backgroundColor: colors.background }, error: { color: colors.danger }, kicker: { color: colors.accent, fontSize: 11, fontWeight: "900", letterSpacing: 1.8 }, title: { color: colors.text, fontSize: 32, fontWeight: "900", marginTop: 8 }, name: { color: colors.text, fontSize: 20, fontWeight: "800", marginTop: 22 }, username: { color: colors.muted, fontSize: 15, marginTop: 3 }, qr: { alignSelf: "center", padding: 16, borderRadius: 22, backgroundColor: "#FFFFFF", marginTop: 28 }, qrLabel: { color: colors.muted, textAlign: "center", marginTop: 8 }, panel: { marginTop: 24, borderWidth: 1, borderColor: colors.border, borderRadius: 16, backgroundColor: colors.surface, paddingHorizontal: 16 }, row: { paddingVertical: 14, borderBottomWidth: StyleSheet.hairlineWidth, borderBottomColor: colors.border }, label: { color: colors.muted, fontSize: 11, fontWeight: "800", letterSpacing: 0.8 }, value: { color: colors.text, fontSize: 15, fontWeight: "700", marginTop: 4 }, link: { minHeight: 54, marginTop: 10, borderRadius: 14, borderWidth: 1, borderColor: colors.border, backgroundColor: colors.surface, flexDirection: "row", alignItems: "center", justifyContent: "space-between", paddingHorizontal: 16 }, linkText: { color: colors.text, fontWeight: "800" }, arrow: { color: colors.accent, fontSize: 26 } });
