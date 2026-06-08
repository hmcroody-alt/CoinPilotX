import React, { useEffect, useState } from "react";
import { ActivityIndicator, Linking, RefreshControl, ScrollView, Text, TouchableOpacity, View } from "react-native";
import { MaterialCommunityIcons } from "@expo/vector-icons";
import { PulseApiError, pulseApi } from "../../services/apiClient";
import { colors, screenStyles } from "../../components/theme";
import { PulseHeroCard, PulseTopBar } from "../../components/PulseChrome";
import { useAuthStore } from "../../store/authStore";

type AccountStatus = {
  ok?: boolean;
  plan?: string;
  premium_status?: string;
  subscription_status?: string;
  founder_status?: string;
  founder_number?: number | string;
  has_pro_access?: boolean;
  pro_access_type?: string;
  billing_portal_url?: string;
  checkout_url?: string;
};

export function PremiumScreen() {
  const bootstrap = useAuthStore(state => state.bootstrap);
  const logout = useAuthStore(state => state.logout);
  const [status, setStatus] = useState<AccountStatus | null>(null);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState("");

  async function load() {
    setError("");
    const data = await pulseApi<AccountStatus>("/api/account/status");
    setStatus(data);
  }

  useEffect(() => {
    load()
      .catch(value => {
        if (value instanceof PulseApiError && value.status === 401) {
          logout().catch(() => undefined);
          return;
        }
        setError(value instanceof Error ? value.message : "Premium status could not load.");
      })
      .finally(() => setLoading(false));
  }, [logout]);

  async function refresh() {
    setRefreshing(true);
    await load().catch(value => setError(value instanceof Error ? value.message : "Premium status could not refresh.")).finally(() => setRefreshing(false));
  }

  async function openPremium() {
    const url = status?.billing_portal_url || status?.checkout_url || "https://pulsesoc.com/pulse/premium";
    await Linking.openURL(url);
  }

  const plan = status?.plan || status?.premium_status || status?.subscription_status || bootstrap?.premiumStatus || "Free";
  const active = Boolean(status?.has_pro_access || String(plan).toLowerCase().includes("premium") || String(plan).toLowerCase().includes("active"));

  if (loading) {
    return (
      <View style={screenStyles.centered}>
        <ActivityIndicator color={colors.accent} />
      </View>
    );
  }

  return (
    <ScrollView style={screenStyles.screen} refreshControl={<RefreshControl refreshing={refreshing} onRefresh={refresh} tintColor={colors.accent} />}>
      <PulseTopBar subtitle="Premium" />
      <PulseHeroCard
        eyebrow="PulseSoc Premium"
        title="Premium"
        body="Founder and Premium status, benefits, portfolio links, creator tools, and billing controls for your PulseSoc account."
      />
      {error ? <Text style={screenStyles.error}>{error}</Text> : null}

      <View style={[screenStyles.card, { borderColor: active ? colors.gold : colors.border, borderRadius: 24 }]}>
        <View style={{ flexDirection: "row", alignItems: "center", gap: 12 }}>
          <MaterialCommunityIcons name={active ? "crown" : "star-four-points"} color={active ? colors.gold : colors.accent} size={32} />
          <View style={{ flex: 1 }}>
            <Text style={screenStyles.cardTitle}>{active ? "Premium Active" : "Free Plan"}</Text>
            <Text style={screenStyles.muted}>Current plan: {String(plan)}</Text>
            {status?.founder_number ? <Text style={screenStyles.muted}>Founder #{status.founder_number}</Text> : null}
            {status?.founder_status || bootstrap?.founderStatus ? <Text style={screenStyles.muted}>Founder status: {status?.founder_status || bootstrap?.founderStatus}</Text> : null}
          </View>
        </View>
        <TouchableOpacity style={screenStyles.button} onPress={openPremium}>
          <Text style={screenStyles.buttonText}>{active ? "Manage Billing" : "Upgrade Premium"}</Text>
        </TouchableOpacity>
      </View>

      <Benefit icon="badge-account" title="Founder identity" body="Founder number, badge visibility, and profile recognition when active." />
      <PremiumLink icon="briefcase" title="Portfolio" body="Track holdings, watchlists, alerts, and AI context." url="https://pulsesoc.com/pulse/portfolio" />
      <PremiumLink icon="brain" title="AI Intelligence" body="Open market, safety, scam, and creator intelligence." url="https://pulsesoc.com/pulse/premium/intelligence" />
      <PremiumLink icon="bell-ring" title="Alerts" body="Monitor important market and PulseSoc signals." url="https://pulsesoc.com/alerts" />
      <PremiumLink icon="bookmark-multiple" title="Saved Vault" body="Saved posts, videos, scam reports, and AI insights." url="https://pulsesoc.com/pulse/saved" />
      <PremiumLink icon="view-dashboard" title="Creator Studio" body="Analytics, content planning, video performance, and monetization readiness." url="https://pulsesoc.com/pulse/creator/dashboard" />
      <PremiumLink icon="shield-check" title="Security Center" body="Security score, trusted devices, recovery, and login history." url="https://pulsesoc.com/pulse/settings/security" />
      <PremiumLink icon="account-group" title="Premium Room" body="Premium community access and member resources." url="https://pulsesoc.com/pulse/messages-v2" />
      <Benefit icon="palette" title="Creator cosmetics" body="Premium profile styling, identity effects, and creator-forward presentation." />
      <Benefit icon="chart-line" title="Creator tools" body="Advanced creator surfaces, analytics readiness, and premium content controls." />
      <Benefit icon="shield-check" title="Security-first billing" body="Premium access is granted through verified backend payment status, not a success page alone." />
    </ScrollView>
  );
}

function PremiumLink({ icon, title, body, url }: { icon: React.ComponentProps<typeof MaterialCommunityIcons>["name"]; title: string; body: string; url: string }) {
  return (
    <TouchableOpacity style={screenStyles.card} onPress={() => Linking.openURL(url)}>
      <View style={{ flexDirection: "row", gap: 12 }}>
        <MaterialCommunityIcons name={icon} color={colors.accent} size={24} />
        <View style={{ flex: 1 }}>
          <Text style={screenStyles.cardTitle}>{title}</Text>
          <Text style={screenStyles.muted}>{body}</Text>
        </View>
        <MaterialCommunityIcons name="chevron-right" color={colors.muted} size={22} />
      </View>
    </TouchableOpacity>
  );
}

function Benefit({ icon, title, body }: { icon: React.ComponentProps<typeof MaterialCommunityIcons>["name"]; title: string; body: string }) {
  return (
    <View style={screenStyles.card}>
      <View style={{ flexDirection: "row", gap: 12 }}>
        <MaterialCommunityIcons name={icon} color={colors.accent} size={24} />
        <View style={{ flex: 1 }}>
          <Text style={screenStyles.cardTitle}>{title}</Text>
          <Text style={screenStyles.muted}>{body}</Text>
        </View>
      </View>
    </View>
  );
}
