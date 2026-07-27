import { useCallback, useMemo } from "react";
import { Pressable, StyleSheet, Text, View } from "react-native";
import { Ionicons } from "@expo/vector-icons";
import * as Haptics from "expo-haptics";
import { SettingsHeader, SettingsSection, SettingsShell } from "../../settings/components/SettingsShell";
import { SelectOption, SettingsSelect, SettingsSwitch } from "../../settings/components/SettingsControls";
import { usePreferenceGroup } from "../../settings/store";
import { Audience, ProfileVisibility } from "../../settings/schema";
import { useTheme } from "../../theme/ThemeContext";

const VISIBILITY_OPTIONS: SelectOption<ProfileVisibility>[] = [
  {
    value: "public",
    label: "Public",
    description: "Anyone on PulseSoc, signed in or not, can see your profile and posts.",
    icon: "globe-outline"
  },
  {
    value: "followers",
    label: "Followers only",
    description: "Your posts are visible to people who follow you. Your profile stays discoverable.",
    icon: "people-outline"
  },
  {
    value: "private",
    label: "Private",
    description: "New followers must be approved by you, and only approved followers see your content.",
    icon: "lock-closed-outline"
  }
];

const AUDIENCE_OPTIONS: { value: Audience; label: string }[] = [
  { value: "everyone", label: "Everyone" },
  { value: "followers", label: "Followers" },
  { value: "nobody", label: "No one" }
];

const AUDIENCE_LABELS: Record<Audience, string> = {
  everyone: "Everyone",
  followers: "Followers",
  nobody: "No one"
};

/* -------------------------------------------------------------------------- */
/*                              Audience selector                              */
/* -------------------------------------------------------------------------- */

/**
 * A three-way choice inline, not behind a nav row.
 *
 * There are five of these on the screen. Pushing each one onto its own page
 * would turn "who can see my stories" into a four-tap round trip, and would
 * hide the fact that the answers relate to one another. The segmented control
 * fits all three options and the current answer on a single line.
 */
function AudienceField({
  title,
  subtitle,
  value,
  onChange,
  disabled = false,
  lockedOptions,
  note,
  testID
}: {
  title: string;
  subtitle: string;
  value: Audience;
  onChange: (next: Audience) => void;
  disabled?: boolean;
  /** Options the current account state makes meaningless — shown, but inert. */
  lockedOptions?: Audience[];
  note?: string | null;
  testID: string;
}) {
  const theme = useTheme();
  const selectedForeground = theme.scheme === "light" ? "#ffffff" : "#08110f";

  return (
    <View style={{ paddingHorizontal: theme.metrics.rowPaddingHorizontal, paddingVertical: theme.metrics.rowPaddingVertical + 4 }}>
      <Text style={{ color: theme.colors.text, fontSize: theme.scaleFont(16), fontWeight: "700", opacity: disabled ? 0.45 : 1 }}>
        {title}
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
        {subtitle}
      </Text>

      <View
        accessibilityRole="radiogroup"
        accessibilityLabel={title}
        style={[styles.segment, { backgroundColor: theme.colors.surfaceRaised, borderColor: theme.colors.border }]}
      >
        {AUDIENCE_OPTIONS.map((option) => {
          const locked = disabled || Boolean(lockedOptions?.includes(option.value));
          const selected = option.value === value;
          return (
            <Pressable
              key={option.value}
              testID={`${testID}-${option.value}`}
              accessibilityRole="radio"
              accessibilityLabel={`${title}: ${option.label}`}
              accessibilityState={{ selected, disabled: locked }}
              accessibilityHint={locked ? note ?? undefined : undefined}
              disabled={locked}
              onPress={() => {
                if (selected) return;
                if (theme.hapticFeedback) void Haptics.selectionAsync().catch(() => undefined);
                onChange(option.value);
              }}
              style={({ pressed }) => [
                styles.segmentItem,
                {
                  backgroundColor: selected ? theme.colors.accent : "transparent",
                  opacity: locked ? 0.4 : pressed ? 0.75 : 1
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

      {note ? (
        <View style={styles.note}>
          <Ionicons name="information-circle-outline" size={theme.scaleFont(14)} color={theme.colors.warning} />
          <Text style={{ color: theme.colors.warning, fontSize: theme.scaleFont(12), lineHeight: theme.scaleFont(17), flex: 1 }}>
            {note}
          </Text>
        </View>
      ) : null}
    </View>
  );
}

/* -------------------------------------------------------------------------- */
/*                                   Screen                                    */
/* -------------------------------------------------------------------------- */

/**
 * Privacy.
 *
 * Two rules hold everywhere on this screen. Every control states its
 * consequence, including the reciprocal ones people get wrong (read receipts
 * are a trade, not a one-way switch). And where one choice overrides another —
 * a private account, or activity status turned off — the overridden control is
 * disabled and says so, instead of sitting there implying an effect it no
 * longer has.
 */
export function PrivacySettingsScreen() {
  const { value, setGroup, pending } = usePreferenceGroup("privacy");

  const isPrivate = value.accountVisibility === "private";
  const setVisibility = useCallback((next: ProfileVisibility) => void setGroup({ accountVisibility: next }), [setGroup]);

  // A private account already gates everything it publishes behind approval, so
  // "Everyone" on those fields is a promise the backend cannot keep. The stored
  // value is deliberately left alone: if the account goes public again the
  // user's original choice is still there, rather than having been rewritten
  // underneath them.
  const privateLock = useMemo<Audience[] | undefined>(() => (isPrivate ? ["everyone"] : undefined), [isPrivate]);
  const privateNote = isPrivate
    ? "Your account is private, so this is limited to approved followers no matter which option is chosen."
    : null;

  const effective = useCallback(
    (audience: Audience) => (isPrivate && audience === "everyone" ? "followers" : audience),
    [isPrivate]
  );

  return (
    <SettingsShell bottomDock={false}>
      <SettingsHeader title="Privacy" subtitle="Who can see you, reach you, and find you." />

      <SettingsSection
        title="Account visibility"
        busy={pending}
        footnote={
          isPrivate
            ? "People who already follow you keep their access. Everyone else has to send a request you approve."
            : undefined
        }
      >
        <SettingsSelect
          testID="privacy-account-visibility"
          options={VISIBILITY_OPTIONS}
          value={value.accountVisibility}
          onChange={setVisibility}
        />
      </SettingsSection>

      <SettingsSection title="Activity">
        <SettingsSwitch
          testID="privacy-online-status"
          title="Show when you're online"
          subtitle="A green dot next to your name while you're using PulseSoc. Turning this off also hides your last seen time."
          icon="ellipse-outline"
          value={value.onlineStatus}
          onValueChange={(next) => void setGroup({ onlineStatus: next })}
        />
        <AudienceField
          testID="privacy-last-seen"
          title="Last seen"
          subtitle="Who can see the time you were last active."
          value={value.lastSeen}
          disabled={!value.onlineStatus}
          lockedOptions={privateLock}
          note={
            !value.onlineStatus
              ? "Nobody sees your last seen time while activity status is off."
              : isPrivate
                ? `Only approved followers can see this, so "Everyone" behaves as ${AUDIENCE_LABELS[effective(value.lastSeen)]}.`
                : null
          }
          onChange={(next) => void setGroup({ lastSeen: next })}
        />
        <SettingsSwitch
          testID="privacy-read-receipts"
          title="Read receipts"
          subtitle="This works both ways: turn it off and you stop seeing when other people have read your messages too."
          icon="checkmark-done-outline"
          value={value.readReceipts}
          onValueChange={(next) => void setGroup({ readReceipts: next })}
        />
      </SettingsSection>

      <SettingsSection title="Who can see what you share">
        <AudienceField
          testID="privacy-story-audience"
          title="Stories"
          subtitle="The default audience for every new story you post."
          value={value.storyAudience}
          lockedOptions={privateLock}
          note={privateNote}
          onChange={(next) => void setGroup({ storyAudience: next })}
        />
        <AudienceField
          testID="privacy-live-audience"
          title="Live broadcasts"
          subtitle="Who can join when you go live."
          value={value.liveAudience}
          lockedOptions={privateLock}
          note={privateNote}
          onChange={(next) => void setGroup({ liveAudience: next })}
        />
      </SettingsSection>

      <SettingsSection
        title="Who can reach you"
        // Worth stating plainly: these three are about other people's content
        // and inboxes, which a private account does not govern.
        description="These are unaffected by your account visibility — they cover what other people can do from their side."
      >
        <AudienceField
          testID="privacy-allow-tagging"
          title="Tags"
          subtitle="Who can tag you in their posts, reels, and photos."
          value={value.allowTagging}
          onChange={(next) => void setGroup({ allowTagging: next })}
        />
        <AudienceField
          testID="privacy-allow-mentions"
          title="Mentions"
          subtitle="Who can @mention you in captions and comments."
          value={value.allowMentions}
          onChange={(next) => void setGroup({ allowMentions: next })}
        />
        <AudienceField
          testID="privacy-allow-direct-messages"
          title="Direct messages"
          subtitle='Anyone you block or mute is excluded regardless of this setting. "No one" leaves only existing conversations open.'
          value={value.allowDirectMessages}
          onChange={(next) => void setGroup({ allowDirectMessages: next })}
        />
      </SettingsSection>

      <SettingsSection
        title="Discoverability"
        footnote="Your contact details are never shown to anyone — they are only used to match a search someone already knows the answer to."
      >
        <SettingsSwitch
          testID="privacy-searchable-by-email"
          title="Find me by email"
          subtitle="People who know your email address can find your profile with it."
          icon="mail-outline"
          value={value.searchableByEmail}
          onValueChange={(next) => void setGroup({ searchableByEmail: next })}
        />
        <SettingsSwitch
          testID="privacy-searchable-by-phone"
          title="Find me by phone number"
          subtitle="People who have your number in their contacts can find your profile with it."
          icon="call-outline"
          value={value.searchableByPhone}
          onValueChange={(next) => void setGroup({ searchableByPhone: next })}
        />
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
  note: { alignItems: "flex-start", flexDirection: "row", gap: 6, marginTop: 8 }
});
