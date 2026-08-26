import { Text } from "react-native";

import { Screen } from "../components/Screen";
import { Panel } from "../components/Panel";
import { BusinessOsModules } from "../components/business/BusinessOsModules";
import { businessOsSection, type BusinessOsSectionKey } from "../api/businessOs";
import { businessOsModules } from "../core/businessOsReadiness";
import { colors } from "../theme/colors";
import { createThemedStyles } from "../theme/themedStyles";

/**
 * The first layer for a Business OS section that has no working screen behind it
 * — Customers and Team today.
 *
 * ## Why this screen exists rather than a locked tile
 *
 * The alternative was to leave those two tiles out of the grid entirely, which
 * is what the hub did before: `businessOsHubSections()` kept only sections with
 * a live backend contract. That guarantees no dead taps, and it also guarantees
 * a member never learns the capability is planned. This screen is the trade the
 * other way — the tile opens, and what it opens is an honest account of what the
 * section will be, with every unbuilt module visible and locked.
 *
 * ## Why it is generated rather than hand-written per section
 *
 * Two sections need it today and both would be near-identical pages of prose. A
 * hand-written pair drifts: one gets a roadmap update and the other does not.
 * Everything specific to a section lives in two places already — the section's
 * label and blurb in `BUSINESS_OS_SECTIONS`, its modules in
 * `BUSINESS_OS_MODULES` — so this screen is a rendering of those, and a third
 * section needing a landing page is a config entry rather than a new file.
 *
 * ## The empty case
 *
 * A section key with no modules renders an explicit note rather than a blank
 * page. It should be unreachable — the hub only routes here for sections that
 * have modules — but "unreachable" states reached in production are how blank
 * screens ship, and a blank screen is on this mission's forbidden list.
 */

type Props = {
  navigation: { navigate: (...args: any[]) => void };
  route?: { params?: { section?: BusinessOsSectionKey } };
};

export function BusinessOsSectionScreen({ navigation, route }: Props) {
  const key = route?.params?.section;
  const section = key ? businessOsSection(key) : undefined;

  if (!key || !section) {
    return (
      <Screen title="Business OS">
        <Panel>
          <Text style={styles.muted}>
            This section could not be opened. Go back to Business OS and pick it again.
          </Text>
        </Panel>
      </Screen>
    );
  }

  const modules = businessOsModules(key);

  return (
    <Screen title={section.label} subtitle={section.blurb}>
      <Panel>
        <Text style={styles.panelTitle}>What this is for</Text>
        <Text style={styles.body}>{SECTION_PURPOSE[key] ?? section.blurb}</Text>
      </Panel>

      {modules.length ? (
        <BusinessOsModules
          section={key}
          testID={`section-modules-${key}`}
          onOpen={(module) => {
            // Only READY modules reach this, and the config test guarantees a
            // READY module carries a registered route.
            if (module.route) navigation.navigate(module.route, module.params);
          }}
        />
      ) : (
        <Panel>
          <Text style={styles.muted}>
            Nothing to show here yet. This section will fill in as it is built.
          </Text>
        </Panel>
      )}
    </Screen>
  );
}

/**
 * The longer "purpose" paragraph, which the one-line tile blurb cannot carry.
 * Sections without an entry fall back to their blurb rather than to filler.
 */
const SECTION_PURPOSE: Partial<Record<BusinessOsSectionKey, string>> = {
  customers:
    "Everything you know about the people who buy from you — who they are, what they have bought and how to reach them. Buyer conversations work today; the records and grouping tools underneath are being built.",
  team: "The people who help you run the business, and what each of them is allowed to do. Nothing here is live yet, so you are still the only account with access."
};

const styles = createThemedStyles(() => ({
  panelTitle: {
    color: colors.text,
    fontSize: 15,
    fontWeight: "700"
  },
  body: {
    color: colors.muted,
    fontSize: 14,
    lineHeight: 20
  },
  muted: {
    color: colors.muted,
    fontSize: 14,
    lineHeight: 20
  }
}));

export default BusinessOsSectionScreen;
