import React, { useEffect, useState } from "react";
import { Text, TouchableOpacity, View } from "react-native";
import { ScreenScaffold } from "../../components/ScreenScaffold";
import { screenStyles } from "../../components/theme";
import { useAuthStore } from "../../store/authStore";

export function HomeFeedScreen() {
  const user = useAuthStore(state => state.user);
  const bootstrap = useAuthStore(state => state.bootstrap);
  const offline = useAuthStore(state => state.offline);
  const loadBootstrap = useAuthStore(state => state.loadBootstrap);
  const logout = useAuthStore(state => state.logout);
  const [message, setMessage] = useState("");

  useEffect(() => {
    if (!bootstrap) {
      loadBootstrap().catch(() => setMessage("Profile bootstrap is temporarily unavailable."));
    }
  }, [bootstrap, loadBootstrap]);

  return (
    <ScreenScaffold title="Home Feed" subtitle="Authentication is ready. Product feeds begin in Phase 3.">
      {offline ? (
        <View style={screenStyles.card}>
          <Text style={screenStyles.cardTitle}>Offline</Text>
          <Text style={screenStyles.muted}>Your saved session is protected locally. Reconnect to refresh your profile.</Text>
        </View>
      ) : null}
      <View style={screenStyles.card}>
        <Text style={screenStyles.cardTitle}>{user?.display_name || user?.full_name || user?.username || "Pulse member"}</Text>
        <Text style={screenStyles.muted}>@{user?.username || "pulse"}</Text>
        <Text style={screenStyles.muted}>Avatar: {bootstrap?.avatarUrl ? "loaded" : "not set"}</Text>
      </View>
      <View style={screenStyles.card}>
        <Text style={screenStyles.cardTitle}>Account bootstrap</Text>
        <Text style={screenStyles.muted}>Premium status: {bootstrap?.premiumStatus || "free"}</Text>
        <Text style={screenStyles.muted}>Founder status: {bootstrap?.founderStatus || "not detected"}</Text>
        <Text style={screenStyles.muted}>Unread notifications: {bootstrap?.notificationCount ?? 0}</Text>
      </View>
      <TouchableOpacity style={screenStyles.secondaryButton} onPress={() => loadBootstrap().catch(() => setMessage("Could not refresh profile bootstrap."))}>
        <Text style={screenStyles.secondaryButtonText}>Refresh profile</Text>
      </TouchableOpacity>
      <TouchableOpacity style={screenStyles.secondaryButton} onPress={logout}>
        <Text style={screenStyles.secondaryButtonText}>Log out</Text>
      </TouchableOpacity>
      {message ? <Text style={[screenStyles.muted, { marginTop: 12 }]}>{message}</Text> : null}
    </ScreenScaffold>
  );
}
