import { useCallback, useMemo } from "react";
import { Pressable, StyleSheet, Text, View } from "react-native";
// `native/haptics`, not `expo-haptics`: the owner module is the only thing wired
// to `accessibility.hapticFeedback` (settings/store.tsx calls
// `setHapticsEnabled` on every snapshot), so importing the SDK directly here
// would give this screen a buzz the user had switched off. The direct-import
// call sites elsewhere in `screens/settings/` are a baseline, not a pattern.
import { haptic } from "../../native/haptics";
import { SettingsHeader, SettingsSection, SettingsShell } from "../../settings/components/SettingsShell";
import { SettingsBadge, SettingsRow, SettingsSwitch } from "../../settings/components/SettingsControls";
import { usePreferenceGroup } from "../../settings/store";
import { CommerceFrequency } from "../../settings/schema";
import { useTheme } from "../../theme/ThemeContext";

/**
 * Length of the pause offered here.
 *
 * Deliberately a client-side constant rather than something fetched from the
 * server's `COMMERCE_DISCOVERY_HIDE_DAYS`, and the two cannot drift apart in a
 * way that matters: what gets stored is an **absolute instant**, not a
 * duration. The server honours the timestamp it is given. If the backend's own
 * default is ever retuned, a pause already in flight keeps the end date the
 * user was shown, which is the only behaviour that would not look like a bug
 * from the outside.
 */
const PAUSE_DAYS = 30;

const FREQUENCY_OPTIONS: { value: CommerceFrequency; label: string }[] = [
  { value: "low", label: "Fewer" },
  { value: "balanced", label: "Balanced" },
  { value: "more", label: "More" }
];

/**
 * What each frequency actually buys, in words rather than a multiplier.
 *
 * The real numbers live in `FREQUENCY_MULTIPLIER` on the server and are
 * tunable, so printing "40%" here would be a promise this screen cannot keep.
 * The invariant under "More" is worth stating outright, though: asking for more
 * suggestions never produces two in a row, because that rule is enforced at
 * placement time and is not a function of this setting.
 */
const FREQUENCY_NOTES: Record<CommerceFrequency, string> = {
  low: "Noticeably fewer suggestions, spaced further apart.",
  balanced: "The default — a suggestion every several posts.",
  more: "More suggestions. Never two in a row, whatever you pick here."
};

/* -------------------------------------------------------------------------- */
/*                             Frequency selector                              */
/* -------------------------------------------------------------------------- */

function FrequencyField({
  value,
  onChange,
  disabled,
  note
}: {
  value: CommerceFrequency;
  onChange: (next: CommerceFrequency) => void;
  disabled: boolean;
  note: string;
}) {
  const theme = useTheme();
  const selectedForeground = theme.scheme === "light" ? "#ffffff" : "#08110f";

  return (
    <View
      style={{
        paddingHorizontal: theme.metrics.rowPaddingHorizontal,
        paddingVertical: theme.metrics.rowPaddingVertical + 4
      }}
    >
      <Text
        style={{
          color: theme.colors.text,
          fontSize: theme.scaleFont(16),
          fontWeight: "700",
          opacity: disabled ? 0.45 : 1
        }}
      >
        How many
      </Text>
      <Text
        style={{
          color: theme.colors.muted,
          fontSize: theme.scaleFont(13),
          lineHeight: theme.scaleFont(18),
          marginTop: 2,
          opacity: disabled ? 0.45 : 1
        }}
      >
        Roughly how often a suggestion can appear while you scroll.
      </Text>

      <View
        accessibilityRole="radiogroup"
        accessibilityLabel="How many suggestions"
        style={[styles.segment, { backgroundColor: theme.colors.surfaceRaised, borderColor: theme.colors.border }]}
      >
        {FREQUENCY_OPTIONS.map((option) => {
          const selected = option.value === value;
          return (
            <Pressable
              key={option.value}
              testID={`commerce-frequency-${option.value}`}
              accessibilityRole="radio"
              accessibilityLabel={`How many suggestions: ${option.label}`}
              accessibilityState={{ selected, disabled }}
              disabled={disabled}
              onPress={() => {
                if (selected) return;
                haptic("selection");
                onChange(option.value);
              }}
              style={({ pressed }) => [
                styles.segmentItem,
                {
                  backgroundColor: selected ? theme.colors.accent : "transparent",
                  opacity: disabled ? 0.4 : pressed ? 0.75 : 1
                }
              ]}
            >
              <Text
                numberOfLines={1}
                style={{
                  color: selected ? selectedForeground : theme.colors.text,
                  fontSize: theme.scaleFont(13),
                  fontWeight: "700"
                }}
              >
                {option.label}
              </Text>
            </Pressable>
          );
        })}
      </View>

      <Text
        testID="commerce-frequency-note"
        style={{
          color: theme.colors.muted,
          fontSize: theme.scaleFont(12),
          lineHeight: theme.scaleFont(17),
          marginTop: 8,
          opacity: disabled ? 0.45 : 1
        }}
      >
        {note}
      </Text>
    </View>
  );
}

/* -------------------------------------------------------------------------- */
/*                                   Screen                                    */
/* -------------------------------------------------------------------------- */

/**
 * Marketplace suggestions.
 *
 * Four controls, and the reason they are four rather than one is that they
 * answer four different objections. "I don't want products in my feed" is the
 * master switch. "Not right now" is the pause. "Too many" is the frequency.
 * "Don't use my history for this" is personalization — that last one is a
 * privacy objection, and answering it by turning suggestions *off* entirely
 * would be answering a question the user did not ask.
 *
 * Two boundaries are stated on screen rather than left to be discovered:
 *
 *  - The master switch governs the **social** surfaces. Marketplace itself
 *    keeps recommending, because opening a shop is asking to be shown things.
 *    A switch here that emptied the shop would be a switch that broke a
 *    feature the user never complained about.
 *  - This is not the ad setting. Paid placements are governed by
 *    `data.personalizedAds`, one screen over. Letting one switch quietly stand
 *    for both would mean a user who turns this off keeps seeing Sponsored
 *    cards and concludes the control is decorative.
 *
 * Everything downstream of the master switch is disabled, not hidden, when it
 * is off — the same rule the privacy screen uses. A control that vanishes
 * leaves no way to see what the state was; a control that is visibly inert says
 * "this exists, and something above it is currently overriding it."
 */
export function CommerceSettingsScreen() {
  const { value, setGroup, pending } = usePreferenceGroup("commerce");
  const theme = useTheme();

  const enabled = value.marketplaceRecommendations;

  /**
   * Whether the stored pause is still running.
   *
   * Read against the clock rather than trusted as a flag, because the stored
   * value is an instant that the normalizer deliberately does not clear when it
   * lapses. An expired timestamp is simply not a pause, and must not render as
   * one — otherwise the screen would show "Paused until 3 August" forever to a
   * user whose suggestions came back weeks ago.
   */
  const pausedUntil = useMemo(() => {
    if (!value.snoozeUntil) return null;
    const at = new Date(value.snoozeUntil);
    const millis = at.getTime();
    if (!Number.isFinite(millis) || millis <= Date.now()) return null;
    return at;
  }, [value.snoozeUntil]);

  const pauseLabel = useMemo(() => {
    if (!pausedUntil) return "";
    try {
      return pausedUntil.toLocaleDateString(undefined, { month: "long", day: "numeric" });
    } catch {
      return pausedUntil.toISOString().slice(0, 10);
    }
  }, [pausedUntil]);

  const startPause = useCallback(() => {
    const until = new Date(Date.now() + PAUSE_DAYS * 24 * 60 * 60 * 1000);
    void setGroup({ snoozeUntil: until.toISOString() });
  }, [setGroup]);

  // `""` rather than deleting the key: the patch shape the store sends is a
  // partial group, and an absent key means "unchanged", which would leave the
  // pause running.
  const endPause = useCallback(() => void setGroup({ snoozeUntil: "" }), [setGroup]);

  return (
    <SettingsShell bottomDock={false}>
      <SettingsHeader
        title="Marketplace suggestions"
        subtitle="Products PulseSoc surfaces for free, and how often you see them."
      />

      <SettingsSection
        title="Suggestions"
        busy={pending}
        footnote="Marketplace keeps recommending products inside Marketplace either way. This controls being recommended to while you're doing something else."
      >
        <SettingsSwitch
          testID="commerce-master-switch"
          title="Show Marketplace suggestions"
          subtitle="Product cards in your feed, in reels, and above your chat list."
          icon="pricetags-outline"
          value={enabled}
          onValueChange={(next) => void setGroup({ marketplaceRecommendations: next })}
        />
      </SettingsSection>

      <SettingsSection title="Frequency">
        <FrequencyField
          value={value.frequency}
          disabled={!enabled}
          note={enabled ? FREQUENCY_NOTES[value.frequency] : "Suggestions are off, so this has no effect right now."}
          onChange={(next) => void setGroup({ frequency: next })}
        />
      </SettingsSection>

      <SettingsSection
        title="Personalization"
        footnote="This is separate from advertising. Paid placements are controlled under Data & personalization."
      >
        <SettingsSwitch
          testID="commerce-personalization-switch"
          title="Personalize suggestions"
          subtitle={
            enabled
              ? "Uses what you've viewed and saved to pick products. Turn it off and you still get suggestions — chosen by what's popular and new instead."
              : "Suggestions are off, so nothing is being personalized."
          }
          icon="sparkles-outline"
          value={value.personalizedRecommendations}
          disabled={!enabled}
          onValueChange={(next) => void setGroup({ personalizedRecommendations: next })}
        />
      </SettingsSection>

      <SettingsSection
        title="Take a break"
        footnote={
          pausedUntil
            ? "Suggestions come back on their own. You don't have to remember to turn them on again."
            : undefined
        }
      >
        {pausedUntil ? (
          <>
            <SettingsRow
              testID="commerce-pause-status"
              title={`Paused until ${pauseLabel}`}
              subtitle="No suggestions on your feed, reels, or chat list until then."
              icon="pause-circle-outline"
              accessory={<SettingsBadge label="Paused" tone="warning" />}
            />
            <View style={[styles.separator, { backgroundColor: theme.colors.border, marginLeft: theme.metrics.rowPaddingHorizontal }]} />
            <SettingsRow
              testID="commerce-resume"
              title="Resume now"
              subtitle="End the pause and start seeing suggestions again."
              icon="play-circle-outline"
              tone="accent"
              onPress={endPause}
            />
          </>
        ) : (
          <SettingsRow
            testID="commerce-pause"
            title={`Pause for ${PAUSE_DAYS} days`}
            subtitle={
              enabled
                ? "Hide Marketplace suggestions for a month without changing any of your settings."
                : "Suggestions are already off."
            }
            icon="pause-circle-outline"
            disabled={!enabled}
            onPress={enabled ? startPause : undefined}
          />
        )}
      </SettingsSection>
    </SettingsShell>
  );
}

const styles = StyleSheet.create({
  segment: {
    borderRadius: 10,
    borderWidth: StyleSheet.hairlineWidth,
    flexDirection: "row",
    gap: 4,
    marginTop: 12,
    padding: 4
  },
  segmentItem: {
    alignItems: "center",
    borderRadius: 7,
    flex: 1,
    justifyContent: "center",
    minHeight: 44,
    paddingHorizontal: 6
  },
  separator: { height: StyleSheet.hairlineWidth }
});
