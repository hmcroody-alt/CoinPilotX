import React, { useEffect, useState } from "react";
import { ActivityIndicator, Image, RefreshControl, ScrollView, Text, TouchableOpacity, View } from "react-native";
import { NativeStackScreenProps } from "@react-navigation/native-stack";
import { MaterialCommunityIcons } from "@expo/vector-icons";
import { colors, screenStyles } from "../../components/theme";
import { PulseHeroCard, PulseTopBar } from "../../components/PulseChrome";
import { ProfileStackParamList } from "../../navigation/types";
import { useAuthStore } from "../../store/authStore";
import { pulseApi } from "../../services/apiClient";
import { compactNumber, initials } from "../../utils/format";

type Props = NativeStackScreenProps<ProfileStackParamList, "Profile">;

type ProfileResponse = {
  ok?: boolean;
  user?: Record<string, unknown>;
  profile?: Record<string, unknown>;
  stats?: Record<string, unknown>;
};

export function ProfileScreen({ navigation }: Props) {
  const user = useAuthStore(state => state.user);
  const bootstrap = useAuthStore(state => state.bootstrap);
  const logout = useAuthStore(state => state.logout);
  const loadBootstrap = useAuthStore(state => state.loadBootstrap);
  const [profile, setProfile] = useState<ProfileResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState("");

  async function load() {
    setError("");
    const data = await pulseApi<ProfileResponse>("/api/pulse/profile/me");
    setProfile(data);
    await loadBootstrap().catch(() => undefined);
  }

  useEffect(() => {
    load()
      .catch(value => setError(value instanceof Error ? value.message : "Profile could not load."))
      .finally(() => setLoading(false));
  }, []);

  async function refresh() {
    setRefreshing(true);
    await load().catch(value => setError(value instanceof Error ? value.message : "Profile could not refresh.")).finally(() => setRefreshing(false));
  }

  const profileUser = (profile?.user || profile?.profile || {}) as Record<string, unknown>;
  const displayName = String(profileUser.display_name || profileUser.full_name || user?.display_name || user?.full_name || user?.username || "PulseSoc member");
  const username = String(profileUser.username || profileUser.public_player_id || user?.username || "");
  const email = String(profileUser.email || user?.email || "");
  const avatar = String(profileUser.avatar_url || profileUser.avatar_thumbnail_url || bootstrap?.avatarUrl || user?.avatar_url || user?.avatar_thumbnail_url || "");
  const bio = String(profileUser.bio || profileUser.about || "");
  const stats = profile?.stats || profileUser;

  if (loading) {
    return (
      <View style={screenStyles.centered}>
        <ActivityIndicator color={colors.accent} />
      </View>
    );
  }

  return (
    <ScrollView style={screenStyles.screen} refreshControl={<RefreshControl refreshing={refreshing} onRefresh={refresh} tintColor={colors.accent} />}>
      <PulseTopBar subtitle="Profile" />
      <PulseHeroCard
        eyebrow="PulseSoc Profile"
        title="Your PulseSoc identity"
        body="Your creator profile, membership state, stats, and account controls stay synced with the live website."
      />
      <View style={[screenStyles.card, { alignItems: "center", marginTop: 2, marginBottom: 12, borderRadius: 24 }]}>
        {avatar ? (
          <Image source={{ uri: avatar }} style={{ width: 104, height: 104, borderRadius: 52, backgroundColor: colors.surfaceSoft }} />
        ) : (
          <View style={{ width: 104, height: 104, borderRadius: 52, backgroundColor: colors.surfaceSoft, alignItems: "center", justifyContent: "center" }}>
            <Text style={{ color: colors.accent, fontSize: 34, fontWeight: "900" }}>{initials(displayName)}</Text>
          </View>
        )}
        <Text style={[screenStyles.title, { marginTop: 14, marginBottom: 2, textAlign: "center" }]}>{displayName}</Text>
        <Text style={screenStyles.muted}>{username ? `@${username}` : email || "PulseSoc.com"}</Text>
        {bio ? <Text style={[screenStyles.subtitle, { textAlign: "center", marginTop: 12 }]}>{bio}</Text> : null}
      </View>

      {error ? <Text style={screenStyles.error}>{error}</Text> : null}

      <View style={{ flexDirection: "row", gap: 8, marginBottom: 12 }}>
        <Stat label="Posts" value={stats.posts_count || stats.post_count} />
        <Stat label="Followers" value={stats.followers_count} />
        <Stat label="Following" value={stats.following_count} />
      </View>

      <View style={screenStyles.card}>
        <Text style={screenStyles.cardTitle}>Membership</Text>
        <Text style={screenStyles.muted}>Plan: {bootstrap?.premiumStatus || user?.premium_status || "free"}</Text>
        {bootstrap?.founderStatus ? <Text style={screenStyles.muted}>Founder: {bootstrap.founderStatus}</Text> : null}
        <TouchableOpacity style={screenStyles.secondaryButton} onPress={() => navigation.navigate("Premium")}>
          <Text style={screenStyles.secondaryButtonText}>View Premium</Text>
        </TouchableOpacity>
      </View>

      <View style={screenStyles.card}>
        <Text style={screenStyles.cardTitle}>Account</Text>
        {email ? <Text style={screenStyles.muted}>{email}</Text> : null}
        <TouchableOpacity style={screenStyles.secondaryButton} onPress={() => navigation.navigate("Settings")}>
          <Text style={screenStyles.secondaryButtonText}>Settings</Text>
        </TouchableOpacity>
        <TouchableOpacity style={screenStyles.secondaryButton} onPress={() => navigation.navigate("UNDX")}>
          <Text style={screenStyles.secondaryButtonText}>UNDX</Text>
        </TouchableOpacity>
        <TouchableOpacity style={[screenStyles.secondaryButton, { borderColor: colors.danger }]} onPress={logout}>
          <Text style={[screenStyles.secondaryButtonText, { color: colors.danger }]}>Log Out</Text>
        </TouchableOpacity>
      </View>
    </ScrollView>
  );
}

function Stat({ label, value }: { label: string; value: unknown }) {
  return (
    <View style={[screenStyles.card, { flex: 1, alignItems: "center", marginBottom: 0 }]}>
      <MaterialCommunityIcons name={label === "Posts" ? "post" : label === "Followers" ? "account-heart" : "account-multiple"} color={colors.accent} size={20} />
      <Text style={[screenStyles.cardTitle, { marginTop: 6 }]}>{compactNumber(String(value || 0))}</Text>
      <Text style={screenStyles.muted}>{label}</Text>
    </View>
  );
}
