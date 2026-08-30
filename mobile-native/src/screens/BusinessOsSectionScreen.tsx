/**
 * A Business OS section's landing layer.
 *
 * WHAT IT ANSWERS
 *
 * "What can I actually do in here?" — asked of a section that works but is not
 * finished. The grid tile has room for one line; this has room for the truth:
 * the section's purpose, everything it does today, and everything it will do,
 * with the second list marked rather than hidden.
 *
 * WHEN IT APPEARS
 *
 * Only where something is locked. `businessOsSectionHasLanding` decides, and a
 * section with nothing missing never comes here — its card opens its real
 * screen exactly as before. A landing in front of a finished feature is a page
 * of text standing between someone and their work.
 *
 * THE SHAPE OF THE PAGE
 *
 * Two lists and, when the section itself is open, one button into it. The
 * button is absent when the section is gated, because there is nowhere to go —
 * and it is absent rather than disabled, for the same reason `LaunchTile` does
 * not grey out: a disabled control says the user did something wrong.
 *
 * Locked rows are pressable and open the same Coming Soon message every locked
 * card in the app opens. One wording, everywhere.
 */

import { Pressable, StyleSheet, Text, View } from "react-native";
import { Ionicons } from "@expo/vector-icons";
import { businessOsNavigationArgs, businessOsSection, type BusinessOsSectionKey } from "../api/businessOs";
import { Panel } from "../components/Panel";
import { Screen } from "../components/Screen";
import { ComingSoonSheet } from "../launch/ComingSoonSheet";
import { businessModuleId, isLaunchGated, readinessOf } from "../launch/readiness";
import { businessOsSectionLists, businessOsSectionOverview, type ResolvedCapability } from "../launch/sectionCapabilities";
import { useLaunchCopy, useLaunchGate } from "../launch/useLaunchGate";
import { colors } from "../theme/colors";
import { presenceTheme } from "../theme/presenceTheme";
import { createThemedStyles } from "../theme/themedStyles";

type Props = {
  navigation: { navigate: (...args: any[]) => void };
  route?: { params?: { section?: BusinessOsSectionKey } };
};

export function BusinessOsSectionScreen({ navigation, route }: Props) {
  const gate = useLaunchGate();
  const { badge } = useLaunchCopy();

  const key = route?.params?.section;
  const section = key ? businessOsSection(key) : undefined;
  const overview = key ? businessOsSectionOverview(key) : undefined;

  // Reached with a section that does not exist — a stale deep link, or a
  // renamed key. Say so plainly rather than rendering an empty page that looks
  // like the section is finished and does nothing.
  if (!key || !section || !overview) {
    return (
      <Screen title="Business OS">
        <Panel>
          <Text style={styles.muted}>That section is not part of Business OS.</Text>
        </Panel>
      </Screen>
    );
  }

  const sectionId = businessModuleId(key);
  const sectionGated = isLaunchGated(sectionId);
  const { available, upcoming } = businessOsSectionLists(key);
  // A gated section has nowhere to send anyone, so it gets no button at all.
  const canOpen = !sectionGated && Boolean(section.route);

  function openSection() {
    const [target, params] = businessOsNavigationArgs(section!);
    navigation.navigate(target, params);
  }

  return (
    <Screen title={section.label} subtitle={overview.purpose}>
      {sectionGated ? (
        <Panel>
          <View style={styles.statusRow}>
            <Ionicons name="sparkles-outline" size={18} color={presenceTheme.teal} />
            <View style={styles.statusBadge}>
              <Text style={styles.statusBadgeText} testID={`business-section-status-${key}`}>
                {badge(readinessOf(sectionId))}
              </Text>
            </View>
          </View>
          <Text style={styles.muted}>
            {section.label} is being built. Everything it will do is listed below, so you can see it coming rather
            than find it missing.
          </Text>
        </Panel>
      ) : null}

      <Panel>
        <Text style={styles.panelTitle}>Available now</Text>
        {available.length ? (
          <View style={styles.list}>
            {available.map((capability) => (
              <CapabilityRow key={capability.key} capability={capability} />
            ))}
          </View>
        ) : (
          // Only reachable for a section with no live capability at all —
          // Customers, Team, Events. Saying it outright is better than an
          // empty panel, which reads as a loading failure.
          <Text style={styles.muted}>Nothing here yet. This section is on its way.</Text>
        )}
        {canOpen ? (
          <Pressable
            accessibilityRole="button"
            accessibilityLabel={`Open ${section.label}`}
            testID={`business-section-open-${key}`}
            onPress={openSection}
            style={styles.primaryButton}
          >
            <Text style={styles.primaryButtonText}>Open {section.label}</Text>
          </Pressable>
        ) : null}
      </Panel>

      {upcoming.length ? (
        <Panel>
          <Text style={styles.panelTitle}>Coming next</Text>
          <Text style={styles.muted}>
            These are on the way. Nothing here shows made-up numbers in the meantime.
          </Text>
          <View style={styles.list}>
            {upcoming.map((capability) => (
              <CapabilityRow
                key={capability.key}
                capability={capability}
                onPress={() => gate.open(capability.id, capability.label, () => undefined)}
              />
            ))}
          </View>
        </Panel>
      ) : null}

      <ComingSoonSheet target={gate.target} onDismiss={gate.dismiss} />
    </Screen>
  );
}

/**
 * One capability, in either list.
 *
 * A locked row takes the teal edge and the word badge from `LaunchTile` and
 * leaves the drift and the halo behind. Those mark one card among several on a
 * grid; a dozen of them animating down a list is noise, and the row is already
 * unambiguous without movement — it says "Coming Soon" in words. Reduce Motion
 * therefore has nothing to switch off here.
 */
function CapabilityRow({ capability, onPress }: { capability: ResolvedCapability; onPress?: () => void }) {
  const { badge, accessibility } = useLaunchCopy();
  const locked = capability.state !== "READY";
  const a11y = accessibility(capability.id, capability.label, capability.blurb);

  const body = (
    <>
      <Ionicons
        name={locked ? "lock-closed-outline" : "checkmark-circle"}
        size={18}
        color={locked ? presenceTheme.teal : colors.accent}
      />
      <View style={styles.rowBody}>
        <View style={styles.rowHeading}>
          <Text style={styles.rowLabel}>{capability.label}</Text>
          {locked ? (
            <View style={styles.badge}>
              <Text style={styles.badgeText}>{badge(capability.state)}</Text>
            </View>
          ) : null}
        </View>
        <Text style={styles.rowBlurb}>{capability.blurb}</Text>
      </View>
    </>
  );

  // An available capability is a statement, not a control: it describes what
  // the section already does, and the way to use it is the button above. Only
  // locked rows are pressable, because only they have an answer to give.
  if (!locked) {
    return (
      <View accessible accessibilityLabel={a11y.accessibilityLabel} style={styles.row} testID={`capability-${capability.id}`}>
        {body}
      </View>
    );
  }

  return (
    <Pressable
      accessibilityRole="button"
      accessibilityLabel={a11y.accessibilityLabel}
      accessibilityHint={a11y.accessibilityHint}
      testID={`capability-${capability.id}`}
      onPress={onPress}
      style={[styles.row, styles.rowLocked]}
    >
      {body}
    </Pressable>
  );
}

const styles = createThemedStyles(() => ({
  badge: {
    backgroundColor: presenceTheme.tealSoft,
    borderColor: presenceTheme.tealBorder,
    borderRadius: 999,
    borderWidth: StyleSheet.hairlineWidth,
    paddingHorizontal: 8,
    paddingVertical: 2
  },
  badgeText: {
    color: presenceTheme.teal,
    fontSize: 10,
    fontWeight: "900",
    letterSpacing: 0.6
  },
  list: {
    gap: 10,
    marginTop: 10
  },
  muted: {
    color: colors.muted,
    fontSize: 14,
    lineHeight: 20
  },
  panelTitle: {
    color: colors.text,
    fontSize: 16,
    fontWeight: "800"
  },
  primaryButton: {
    alignItems: "center",
    backgroundColor: colors.accent,
    borderRadius: 12,
    justifyContent: "center",
    marginTop: 14,
    minHeight: 48,
    paddingHorizontal: 20
  },
  primaryButtonText: {
    color: "#08110f",
    fontSize: 15,
    fontWeight: "800"
  },
  row: {
    alignItems: "flex-start",
    backgroundColor: colors.surfaceRaised,
    borderColor: colors.border,
    borderRadius: 10,
    borderWidth: StyleSheet.hairlineWidth,
    flexDirection: "row",
    gap: 10,
    padding: 12
  },
  rowBlurb: {
    color: colors.muted,
    fontSize: 13,
    lineHeight: 18
  },
  rowBody: {
    flex: 1,
    gap: 3
  },
  rowHeading: {
    alignItems: "center",
    flexDirection: "row",
    flexWrap: "wrap",
    gap: 8
  },
  rowLabel: {
    color: colors.text,
    fontSize: 15,
    fontWeight: "700"
  },
  rowLocked: {
    borderColor: presenceTheme.tealBorder
  },
  statusBadge: {
    backgroundColor: presenceTheme.tealSoft,
    borderColor: presenceTheme.tealBorder,
    borderRadius: 999,
    borderWidth: StyleSheet.hairlineWidth,
    paddingHorizontal: 10,
    paddingVertical: 3
  },
  statusBadgeText: {
    color: presenceTheme.teal,
    fontSize: 11,
    fontWeight: "900",
    letterSpacing: 1
  },
  statusRow: {
    alignItems: "center",
    flexDirection: "row",
    gap: 8,
    marginBottom: 8
  }
}));
