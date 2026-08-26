/**
 * A door that is visible, labelled, and does not open.
 *
 * ## Why this is a control and not a deletion
 *
 * The obvious way to stop a member reaching an unfinished workflow is to remove
 * the button. That works, and it costs the landing page the thing it is
 * currently best at: showing what Presence is going to be. A member who opens
 * Presence and sees two creation cards understands the section in three seconds.
 * A member who sees an empty page understands nothing and comes back never.
 *
 * So the control stays where it was, in the position and roughly the shape it
 * will have when it works, greyed out with its state named. Tapping it expands
 * the layers behind it — also greyed, also locked — rather than navigating.
 * Nothing about the tap reads as a failure: no alert, no error text, no route
 * name, no "not implemented". The member asked what is behind the door and got
 * an honest answer.
 *
 * ## Why the tap does something
 *
 * A greyed control that swallows taps silently is indistinguishable from a
 * broken one, and the second tap is the one that teaches somebody the app is
 * unreliable. This expands, which is both a visible acknowledgement and the
 * only place the "future ecosystem" list is shown — so the tap is worth making
 * and the landing page stays uncluttered for everyone who does not.
 *
 * Because it responds, it is *not* marked `accessibilityState.disabled`: a
 * screen reader must not be told a control is inert when it does something. The
 * lock is carried where it is true instead — in the label, the badge, the hint
 * and the expanded panel.
 */

import { Ionicons } from "@expo/vector-icons";
import { useState } from "react";
import { Pressable, Text, View } from "react-native";
import {
  PRESENCE_NEXT_LAYERS,
  PresenceSurface,
  presenceReadiness,
  readinessBadge,
  readinessNote
} from "../../core/launchReadiness";
import { colors } from "../../theme/colors";
import { presenceTheme } from "../../theme/presenceTheme";
import { createThemedStyles } from "../../theme/themedStyles";

type Props = {
  /** The words that were on the button before it was locked. Unchanged. */
  label: string;
  surface: PresenceSurface;
  /**
   * `primary` is a full-width card action, `compact` is a chip sitting in a row
   * of other chips. Only the metrics differ — both are equally locked.
   */
  variant?: "primary" | "compact";
  testID?: string;
};

export function LockedLayer({ label, surface, variant = "primary", testID }: Props) {
  const [open, setOpen] = useState(false);
  const state = presenceReadiness(surface);
  const badge = readinessBadge(state);
  const note = readinessNote(state);
  const layers = PRESENCE_NEXT_LAYERS[surface];
  const compact = variant === "compact";

  return (
    /*
      A compact lock sits in a wrapping row of other chips, so its panel cannot
      simply render underneath it — it would land beside the next chip. Claiming
      the full row width while open makes the flex wrap put it on its own line
      and the panel below the row, which is where it belongs. `flex-start` keeps
      the chip its own size rather than stretching it to that width.
    */
    <View
      style={compact ? [open && styles.compactOpen] : styles.block}
      testID={testID}
    >
      <Pressable
        accessibilityRole="button"
        /*
          Spelled out rather than composed from the children, which a screen
          reader would otherwise read as three fragments with an icon name in
          the middle. The state belongs in the label because it is the single
          most important thing about this control.
        */
        accessibilityLabel={badge ? `${label} — ${badge.toLowerCase()}` : label}
        // Expanded, not disabled — see the note at the top of this file.
        accessibilityState={{ expanded: open }}
        accessibilityHint={note ?? undefined}
        style={[styles.button, compact && styles.buttonCompact]}
        onPress={() => setOpen((wasOpen) => !wasOpen)}
      >
        <Ionicons name="lock-closed" size={compact ? 11 : 13} color={colors.disabled} />
        <Text style={[styles.label, compact && styles.labelCompact]}>{label}</Text>
        {badge ? <Text style={[styles.badge, compact && styles.badgeCompact]}>{badge}</Text> : null}
      </Pressable>

      {open ? (
        <View style={styles.panel} testID={testID ? `${testID}-panel` : undefined}>
          {layers.map((layer) => (
            <View key={layer} style={styles.layerRow}>
              <Ionicons name="lock-closed" size={11} color={colors.disabled} />
              <Text style={styles.layerText}>{layer}</Text>
            </View>
          ))}
          {note ? <Text style={styles.note}>{note}</Text> : null}
        </View>
      ) : null}
    </View>
  );
}

const styles = createThemedStyles(() => ({
  badge: {
    // Teal at a fraction of its strength: still recognisably Presence, clearly
    // not the live accent the working controls wear.
    backgroundColor: presenceTheme.tealSoft,
    borderRadius: 6,
    color: presenceTheme.teal,
    fontSize: 9,
    fontWeight: "900",
    letterSpacing: 0.8,
    opacity: 0.75,
    overflow: "hidden",
    paddingHorizontal: 6,
    paddingVertical: 2
  },
  badgeCompact: {
    fontSize: 8,
    paddingHorizontal: 4
  },
  block: {
    marginTop: 12
  },
  button: {
    alignItems: "center",
    backgroundColor: colors.surfaceRaised,
    borderColor: colors.border,
    borderRadius: 10,
    borderStyle: "dashed",
    borderWidth: 1,
    flexDirection: "row",
    gap: 8,
    justifyContent: "center",
    minHeight: presenceTheme.tapTarget,
    paddingHorizontal: 12
  },
  buttonCompact: {
    borderRadius: 8,
    gap: 6,
    minHeight: 36,
    paddingHorizontal: 10
  },
  compactOpen: {
    alignItems: "flex-start",
    width: "100%"
  },
  label: {
    color: colors.disabled,
    fontSize: 14,
    fontWeight: "900"
  },
  labelCompact: {
    fontSize: 12,
    fontWeight: "800"
  },
  layerRow: {
    alignItems: "center",
    flexDirection: "row",
    gap: 8,
    paddingVertical: 5
  },
  layerText: {
    color: colors.disabled,
    fontSize: 13,
    fontWeight: "700"
  },
  note: {
    color: colors.muted,
    fontSize: 12,
    lineHeight: 18,
    marginTop: 8
  },
  panel: {
    alignSelf: "stretch",
    backgroundColor: colors.surface,
    borderColor: colors.border,
    borderRadius: 10,
    borderWidth: 1,
    marginTop: 8,
    padding: 12
  }
}));
