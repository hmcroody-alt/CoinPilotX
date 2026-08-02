/**
 * A buyer's avatar: their photo when we have one, otherwise a gradient tile with
 * their initials. The gradient is derived deterministically from a stable key
 * (`avatarGradientFor`), so the same buyer is the same colour on every screen and
 * across sessions — the colour is identity, never decoration. All five hues come
 * from the app's existing accent family, so avatars never introduce a foreign
 * colour.
 */

import { Image, StyleSheet, Text, View } from "react-native";
import { LinearGradient } from "expo-linear-gradient";
import { avatarGradientFor } from "../../api/commerceInbox";
import { messagesLight } from "../../theme/messagesLight";

function initials(name: string): string {
  const parts = name.trim().split(/\s+/).filter(Boolean);
  if (!parts.length) return "?";
  if (parts.length === 1) return parts[0].slice(0, 2).toUpperCase();
  return (parts[0][0] + parts[parts.length - 1][0]).toUpperCase();
}

export function InboxAvatar({
  name,
  colorKey,
  avatarUrl,
  size = messagesLight.size.avatar
}: {
  name: string;
  colorKey: string;
  avatarUrl?: string;
  size?: number;
}) {
  const radius = size / 2;

  if (avatarUrl) {
    return (
      <Image
        source={{ uri: avatarUrl }}
        style={{ width: size, height: size, borderRadius: radius }}
        accessibilityIgnoresInvertColors
      />
    );
  }

  const gradient = avatarGradientFor(colorKey);
  return (
    <LinearGradient
      colors={[gradient.from, gradient.to]}
      start={{ x: 0, y: 0 }}
      end={{ x: 1, y: 1 }}
      style={[styles.tile, { width: size, height: size, borderRadius: radius }]}
    >
      <Text style={[styles.initials, { fontSize: Math.round(size / 2.6) }]} allowFontScaling={false}>
        {initials(name)}
      </Text>
    </LinearGradient>
  );
}

const styles = StyleSheet.create({
  tile: { alignItems: "center", justifyContent: "center" },
  initials: { color: messagesLight.text.onDark, fontWeight: "800" }
});
