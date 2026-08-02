/**
 * The Away-mode tile in the inbox tools grid: a title, a subtitle that reflects
 * live state ("Auto-reply on · until changed" / "Auto-reply off"), and an inline
 * switch. Flipping the switch updates the subtitle immediately.
 *
 * There is no live away/auto-reply field on the backend (see INBOX_MOCK_DATA_GAPS),
 * so away mode is optimistic-local until a backend lands and the whole tool is
 * gated behind `EXPO_PUBLIC_MESSAGES_AWAY`. When the flag is off the screen passes
 * `disabled`, and the switch renders read-only with an honest subtitle rather than
 * pretending to persist.
 *
 * Accessibility: the switch announces both its state AND the consequence, so a
 * screen-reader user hears "Away mode, on, buyers get your auto-reply" rather than
 * a bare "on".
 */

import { StyleSheet, Switch, Text, View } from "react-native";
import { Ionicons } from "@expo/vector-icons";
import { messagesLight } from "../../theme/messagesLight";
import { awaySubtitle } from "../../api/commerceInbox";

export function AwayModeSwitchTile({
  on,
  onToggle,
  disabled
}: {
  on: boolean;
  onToggle: (next: boolean) => void;
  disabled?: boolean;
}) {
  const subtitle = disabled ? "Not available in this build" : awaySubtitle(on);
  const consequence = on ? "buyers get your auto-reply" : "buyers do not get an auto-reply";

  return (
    <View style={styles.tile}>
      <View style={styles.head}>
        <Ionicons name="moon-outline" size={18} color={messagesLight.text.primary} />
        <Text style={styles.title}>Away mode</Text>
      </View>
      <View style={styles.foot}>
        <Text style={styles.subtitle} numberOfLines={2}>
          {subtitle}
        </Text>
        <Switch
          value={on}
          onValueChange={onToggle}
          disabled={disabled}
          trackColor={{ true: messagesLight.presence.dot, false: messagesLight.border.hairline }}
          accessibilityLabel="Away mode"
          accessibilityRole="switch"
          accessibilityState={{ checked: on, disabled: Boolean(disabled) }}
          accessibilityHint={disabled ? undefined : consequence}
        />
      </View>
    </View>
  );
}

const styles = StyleSheet.create({
  tile: {
    flex: 1,
    minHeight: 84,
    justifyContent: "space-between",
    gap: 10,
    padding: 12,
    borderRadius: messagesLight.radius.card,
    backgroundColor: messagesLight.bg.card,
    borderWidth: StyleSheet.hairlineWidth,
    borderColor: messagesLight.border.hairline
  },
  head: { flexDirection: "row", alignItems: "center", gap: 8 },
  title: { fontSize: 14, fontWeight: "800", color: messagesLight.text.primary },
  foot: { flexDirection: "row", alignItems: "center", justifyContent: "space-between", gap: 8 },
  subtitle: { flex: 1, fontSize: 12, color: messagesLight.text.muted }
});
