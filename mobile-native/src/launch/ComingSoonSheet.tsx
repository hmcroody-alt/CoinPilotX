/**
 * The Coming Soon message.
 *
 * One component, one wording, every gated module — so the answer a user gets for
 * Customers is the same answer they get for Events, and changing that answer is
 * one edit rather than a search across two screens.
 *
 * The copy is fixed by the product brief and lives in `commerce:launch.*`:
 * "Coming Soon", "Building", "Preparing for Launch". Developer language —
 * "broken", "not implemented", "unavailable", "disabled" — never appears, here
 * or in any accessibility string this module produces. What is unfinished is
 * PulseSoc's work, not the user's problem.
 */

import { Modal, Pressable, StyleSheet, Text, View } from "react-native";
import { useTranslation } from "../i18n";
import { colors } from "../theme/colors";
import { presenceTheme } from "../theme/presenceTheme";
import { createThemedStyles } from "../theme/themedStyles";

export type ComingSoonTarget = {
  /** The gate id, for tests and for the testID. */
  id: string;
  /** The module's own name, shown so the sheet answers "which one?". */
  label: string;
};

export function ComingSoonSheet({
  target,
  onDismiss
}: {
  /** Null closes the sheet. Passing the target in is what opens it. */
  target: ComingSoonTarget | null;
  onDismiss: () => void;
}) {
  const { t } = useTranslation();

  return (
    <Modal
      animationType="fade"
      transparent
      visible={Boolean(target)}
      // Android back button. Without it the sheet is a trap on that platform.
      onRequestClose={onDismiss}
      // Hides everything behind it from the screen reader, so a swipe cannot
      // land on the very card the sheet is explaining.
      accessibilityViewIsModal
    >
      <View style={styles.backdrop}>
        {/*
          The backdrop dismisses. `accessibilityElementsHidden` is not set on it
          because VoiceOver users need a way out that is not the button, and the
          button is the last element in the reading order.
        */}
        <Pressable
          accessibilityRole="button"
          accessibilityLabel={t("commerce:launch.comingSoonAction")}
          onPress={onDismiss}
          style={StyleSheet.absoluteFill}
        />
        <View
          accessible
          accessibilityRole="alert"
          accessibilityViewIsModal
          testID={target ? `coming-soon-${target.id}` : "coming-soon"}
          style={styles.card}
        >
          <View style={styles.halo} />
          <Text style={styles.eyebrow}>{t("commerce:launch.comingSoonTitle")}</Text>
          {target ? <Text style={styles.module}>{target.label}</Text> : null}
          <Text style={styles.body}>{t("commerce:launch.comingSoonBody")}</Text>
          <Pressable
            accessibilityRole="button"
            accessibilityLabel={t("commerce:launch.comingSoonAction")}
            testID="coming-soon-dismiss"
            onPress={onDismiss}
            style={styles.action}
          >
            <Text style={styles.actionText}>{t("commerce:launch.comingSoonAction")}</Text>
          </Pressable>
        </View>
      </View>
    </Modal>
  );
}

const styles = createThemedStyles(() => ({
  action: {
    alignItems: "center",
    backgroundColor: presenceTheme.teal,
    borderRadius: 12,
    marginTop: 6,
    minHeight: 48,
    justifyContent: "center",
    paddingHorizontal: 24
  },
  actionText: {
    color: "#08110f",
    fontSize: 15,
    fontWeight: "800"
  },
  backdrop: {
    alignItems: "center",
    backgroundColor: "rgba(5, 9, 16, 0.82)",
    flex: 1,
    justifyContent: "center",
    padding: 28
  },
  body: {
    color: colors.muted,
    fontSize: 15,
    lineHeight: 22,
    textAlign: "center"
  },
  card: {
    alignItems: "center",
    backgroundColor: colors.surface,
    borderColor: presenceTheme.tealBorder,
    borderRadius: presenceTheme.radius.card,
    borderWidth: StyleSheet.hairlineWidth,
    gap: 10,
    maxWidth: 420,
    overflow: "hidden",
    padding: 24,
    width: "100%"
  },
  eyebrow: {
    color: presenceTheme.teal,
    fontSize: 12,
    fontWeight: "900",
    letterSpacing: 2
  },
  /**
   * A static wash of brand teal at the top of the card. Static on purpose: this
   * sheet appears in response to a tap, and something that animates while the
   * user is reading a two-line message is noise, not polish.
   */
  halo: {
    backgroundColor: presenceTheme.tealSoft,
    height: 120,
    left: 0,
    position: "absolute",
    right: 0,
    top: -60
  },
  module: {
    color: colors.text,
    fontSize: 20,
    fontWeight: "800",
    textAlign: "center"
  }
}));
