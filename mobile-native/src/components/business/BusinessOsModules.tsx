import { useState } from "react";
import { Pressable, StyleSheet, Text, View } from "react-native";
import { Ionicons } from "@expo/vector-icons";

import { colors } from "../../theme/colors";
import { createThemedStyles } from "../../theme/themedStyles";
import { Panel } from "../Panel";
import type { BusinessOsSectionKey } from "../../api/businessOs";
import {
  businessOsModules,
  isModuleReady,
  type BusinessOsModule
} from "../../core/businessOsReadiness";
import { readinessBadge } from "../../core/launchReadiness";
import { ComingSoonModal } from "./ComingSoonModal";

/**
 * The roadmap panel: every module a Business OS section will have, with the ones
 * that exist today separated from the ones that do not.
 *
 * ## Why locked rows are pressable
 *
 * The obvious implementation gives a locked row `disabled`, and it is wrong
 * twice over. A `disabled` control swallows the press silently, so a member who
 * taps it learns nothing and taps it again — the dead button this mission
 * explicitly forbids. It also drops the row out of the accessibility tree's
 * actionable set, so a screen-reader user gets a greyed label with no way to
 * find out why it is grey.
 *
 * So locked rows stay real buttons. They just lead to an explanation instead of
 * a workflow. The lock state travels in the accessibility label, because the
 * greying and the lock glyph are both invisible to a screen reader.
 *
 * ## Rendering nothing
 *
 * A section with no declared modules renders `null`, not an empty panel with a
 * heading. A "What's coming" box containing nothing reads as a loading failure.
 */

type Props = {
  section: BusinessOsSectionKey;
  /**
   * Opens a READY module. Omitted on surfaces that declare no READY modules;
   * a READY module rendered without it would be the dead button this component
   * exists to prevent, so that combination throws in development instead.
   */
  onOpen?: (module: BusinessOsModule) => void;
  /** Overrides the panel heading. Defaults to the forward-looking framing. */
  title?: string;
  testID?: string;
};

export function BusinessOsModules({ section, onOpen, title, testID }: Props) {
  const [locked, setLocked] = useState<BusinessOsModule | null>(null);
  const modules = businessOsModules(section);

  if (!modules.length) return null;

  const ready = modules.filter(isModuleReady);
  const upcoming = modules.filter((module) => !isModuleReady(module));

  return (
    <Panel>
      {ready.length ? (
        <>
          <Text style={styles.heading} testID={testID ? `${testID}-available` : undefined}>
            Available now
          </Text>
          {ready.map((module) => (
            <ModuleRow
              key={module.key}
              module={module}
              onPress={() => {
                if (!onOpen) {
                  // Loud in dev, inert in production: a member should never be
                  // punished for a wiring mistake with a crash.
                  if (__DEV__) {
                    throw new Error(
                      `Business OS section "${section}" declares READY module "${module.key}" but passed no onOpen handler.`
                    );
                  }
                  return;
                }
                onOpen(module);
              }}
            />
          ))}
        </>
      ) : null}

      {upcoming.length ? (
        <>
          <Text style={styles.heading}>{title ?? "Coming to this section"}</Text>
          {upcoming.map((module) => (
            <ModuleRow key={module.key} module={module} onPress={() => setLocked(module)} />
          ))}
          <Text style={styles.footnote}>
            These are on the way. Nothing you already use changes when they arrive.
          </Text>
        </>
      ) : null}

      <ComingSoonModal
        visible={Boolean(locked)}
        // Held after dismissal starts so the label does not blank mid-fade.
        label={locked?.label ?? ""}
        state={locked?.state ?? "COMING_SOON"}
        onClose={() => setLocked(null)}
      />
    </Panel>
  );
}

function ModuleRow({ module, onPress }: { module: BusinessOsModule; onPress: () => void }) {
  const ready = isModuleReady(module);
  const badge = readinessBadge(module.state);

  return (
    <Pressable
      accessibilityRole="button"
      accessibilityLabel={ready ? module.label : `${module.label} — ${badge?.toLowerCase()}`}
      accessibilityHint={ready ? module.blurb : "Not built yet. Opens a note about what is coming."}
      // Deliberately not `disabled` — see the note at the top of this file.
      style={[styles.row, !ready && styles.rowLocked]}
      onPress={onPress}
      testID={`business-module-${module.key}`}
    >
      <Ionicons
        name={ready ? "arrow-forward-circle-outline" : "lock-closed"}
        size={16}
        color={ready ? colors.accent : colors.disabled}
      />
      <View style={styles.rowText}>
        <Text style={[styles.label, !ready && styles.labelLocked]}>{module.label}</Text>
        <Text style={[styles.blurb, !ready && styles.blurbLocked]}>{module.blurb}</Text>
      </View>
      {badge ? <Text style={styles.badge}>{badge}</Text> : null}
    </Pressable>
  );
}

const styles = createThemedStyles(() => ({
  heading: {
    color: colors.muted,
    fontSize: 11,
    fontWeight: "800",
    letterSpacing: 1.1,
    textTransform: "uppercase"
  },
  row: {
    flexDirection: "row",
    alignItems: "center",
    gap: 10,
    backgroundColor: colors.surfaceRaised,
    borderColor: colors.border,
    borderRadius: 10,
    borderWidth: StyleSheet.hairlineWidth,
    paddingHorizontal: 12,
    paddingVertical: 11,
    // Comfortably above the 44pt minimum once the two text lines are laid out.
    minHeight: 52
  },
  rowLocked: {
    backgroundColor: colors.surface,
    borderStyle: "dashed",
    // The "reduced brightness" the locked state calls for, applied to the row as
    // a whole so the glyph, label and blurb dim together by the same amount.
    opacity: 0.62
  },
  rowText: {
    flex: 1,
    gap: 2
  },
  label: {
    color: colors.text,
    fontSize: 14,
    fontWeight: "700"
  },
  labelLocked: {
    color: colors.muted
  },
  blurb: {
    color: colors.muted,
    fontSize: 12,
    lineHeight: 16
  },
  blurbLocked: {
    color: colors.disabled
  },
  badge: {
    color: colors.accent,
    fontSize: 9,
    fontWeight: "800",
    letterSpacing: 0.8
  },
  footnote: {
    color: colors.disabled,
    fontSize: 11,
    lineHeight: 15
  }
}));
