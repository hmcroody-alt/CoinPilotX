/**
 * The domain type-icon circle used on Activity rows. Two uses:
 *
 *   • On a SYSTEM row (no human actor) it stands alone as the row's leading
 *     glyph — a shield-green circle for verification, a red one for live, etc.
 *   • On an ACTOR row it shrinks into a mini-badge overlapping the actor avatar
 *     (rendered by NotificationRow), so you can tell an order-from-Devon apart
 *     from a like-from-Devon at a glance.
 *
 * Every colour comes from eventsLight.activityType, which reuses the app-wide
 * domain semantics (violet = marketplace, blue = orders, green = money, …) so a
 * circle means the same thing here as on every other seller surface.
 */

import { StyleSheet, View } from "react-native";
import { Ionicons } from "@expo/vector-icons";
import { eventsLight } from "../../theme/eventsLight";
import type { ActivityDomain } from "../../api/activityFeed";

const GLYPH: Record<ActivityDomain, keyof typeof Ionicons.glyphMap> = {
  social: "heart",
  marketplace: "pricetag",
  orders: "cube",
  payments: "cash",
  live: "radio",
  system: "shield-checkmark"
};

export function TypeCircle({ domain, size = 40, mini }: { domain: ActivityDomain; size?: number; mini?: boolean }) {
  const c = eventsLight.activityType[domain];
  const iconSize = mini ? Math.round(size * 0.62) : Math.round(size * 0.5);
  return (
    <View
      style={[
        styles.circle,
        { width: size, height: size, borderRadius: size / 2, backgroundColor: c.bg },
        mini ? { borderWidth: 2, borderColor: "#FFFFFF" } : null
      ]}
    >
      <Ionicons name={GLYPH[domain]} size={iconSize} color={c.fg} />
    </View>
  );
}

const styles = StyleSheet.create({
  circle: { alignItems: "center", justifyContent: "center" }
});
