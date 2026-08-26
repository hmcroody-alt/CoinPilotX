import { Modal, Pressable, StyleSheet, Text, View } from "react-native";
import { Ionicons } from "@expo/vector-icons";

import { colors } from "../../theme/colors";
import { createThemedStyles } from "../../theme/themedStyles";
import { readinessBadge, type ReadinessState } from "../../core/launchReadiness";

/**
 * What a member gets when they tap a locked Business OS module.
 *
 * The whole point of this sheet is what it does *not* do. It is not an error, it
 * does not apologise, and it does not report a technical condition — three
 * things a member reads as "the app is broken" rather than "this isn't built
 * yet". Those read identically on screen and mean opposite things about the
 * product, so the copy here is deliberately plain and forward-looking.
 *
 * It also stays a sheet rather than becoming a screen. Pushing a route for
 * "nothing is here yet" puts an entry in the back stack that a member has to
 * dismiss to get back to where they were, and leaves them on a page with no
 * content. A modal keeps the section they were reading visible behind it.
 */

type Props = {
  visible: boolean;
  /** Module label, used to name what is coming rather than leaving it abstract. */
  label: string;
  state: ReadinessState;
  onClose: () => void;
};

export function ComingSoonModal({ visible, label, state, onClose }: Props) {
  const badge = readinessBadge(state);

  return (
    <Modal
      visible={visible}
      transparent
      animationType="fade"
      // Android hardware back must dismiss, or the sheet becomes a trap.
      onRequestClose={onClose}
    >
      {/* Tapping the scrim closes, matching every other sheet in the app. */}
      <Pressable
        accessibilityRole="button"
        accessibilityLabel="Close"
        style={styles.scrim}
        onPress={onClose}
      >
        {/*
          The card swallows presses so a tap inside it does not fall through to
          the scrim and dismiss the thing the member just opened.
        */}
        <Pressable
          accessible
          accessibilityViewIsModal
          style={styles.card}
          onPress={() => undefined}
        >
          <View style={styles.iconRing}>
            <Ionicons name="lock-closed" size={20} color={colors.accent} />
          </View>

          <Text style={styles.badge}>{badge ?? "COMING SOON"}</Text>
          <Text style={styles.title}>{label}</Text>

          <Text style={styles.body}>
            This part of Business OS is still being built. Stay connected as new capabilities
            become available.
          </Text>

          <Pressable
            accessibilityRole="button"
            accessibilityLabel="Got it"
            style={styles.cta}
            onPress={onClose}
            testID="coming-soon-dismiss"
          >
            <Text style={styles.ctaText}>Got it</Text>
          </Pressable>
        </Pressable>
      </Pressable>
    </Modal>
  );
}

const styles = createThemedStyles(() => ({
  scrim: {
    flex: 1,
    alignItems: "center",
    justifyContent: "center",
    backgroundColor: "rgba(3, 8, 14, 0.78)",
    padding: 26
  },
  card: {
    width: "100%",
    maxWidth: 380,
    alignItems: "center",
    gap: 10,
    backgroundColor: colors.surfaceRaised,
    borderColor: colors.border,
    borderRadius: 18,
    borderWidth: StyleSheet.hairlineWidth,
    paddingHorizontal: 22,
    paddingVertical: 26
  },
  iconRing: {
    alignItems: "center",
    justifyContent: "center",
    width: 52,
    height: 52,
    borderRadius: 26,
    borderWidth: StyleSheet.hairlineWidth,
    borderColor: colors.border,
    backgroundColor: colors.surface
  },
  badge: {
    color: colors.accent,
    fontSize: 11,
    fontWeight: "800",
    letterSpacing: 1.4
  },
  title: {
    color: colors.text,
    fontSize: 19,
    fontWeight: "700",
    textAlign: "center"
  },
  body: {
    color: colors.muted,
    fontSize: 14,
    lineHeight: 20,
    textAlign: "center"
  },
  cta: {
    marginTop: 6,
    alignSelf: "stretch",
    alignItems: "center",
    backgroundColor: colors.accent,
    borderRadius: 12,
    paddingVertical: 13
  },
  ctaText: {
    color: colors.background,
    fontSize: 15,
    fontWeight: "700"
  }
}));
