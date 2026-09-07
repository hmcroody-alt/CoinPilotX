/**
 * A Business OS section landing page — the first layer of a section that has no
 * finished screen of its own.
 *
 * WHAT PROBLEM THIS SOLVES
 *
 * Before this, a section with no backend (Customers, Team) or with a screen that
 * could not hold a row (Events) answered a tap with the Coming Soon message and
 * nothing else. That is a closed door: correct, but it tells the owner nothing
 * about what the section will be, and it makes a tile that looks like every other
 * tile behave like a dead end.
 *
 * This screen is the open door. It states what the section is for, lists the
 * capabilities that already work, and lists the ones still being built as locked
 * rows. The section becomes enterable while the unfinished depth behind it stays
 * shut — which is the whole point of the layer.
 *
 * WHAT IT IS NOT
 *
 * It is not a second Business OS and it holds no data of its own: every working
 * row here navigates to a screen that already existed, and no row invents a
 * capability. A section whose tile already opens a real screen is deliberately
 * absent from `BUSINESS_OS_SECTION_MODULES` and never reaches this screen — it
 * would be a menu in front of a working feature.
 *
 * When a module's backend lands, its row comes out of `readiness.ts` and gains a
 * `route`; nothing here changes. When the last locked row in a section opens, the
 * section can be pointed straight at its own screen and this landing retires.
 */

import { Text, View } from "react-native";
import { Panel } from "../components/Panel";
import { Screen } from "../components/Screen";
import { businessOsSection } from "../api/businessOs";
import { useTranslation } from "../i18n";
import { ComingSoonSheet } from "../launch/ComingSoonSheet";
import { LaunchModuleRow } from "../launch/LaunchModuleRow";
import { sectionCapabilityLists, type ResolvedCapability } from "../launch/sectionCapabilities";
import { useLaunchGate } from "../launch/useLaunchGate";
import type { RootStackParamList } from "../navigation/types";
import { colors } from "../theme/colors";
import { createThemedStyles } from "../theme/themedStyles";

type Props = {
  navigation: { navigate: (...args: any[]) => void; goBack?: () => void };
  route?: { params?: RootStackParamList["BusinessOsSection"] };
};

export function BusinessOsSectionScreen({ navigation, route }: Props) {
  const { t } = useTranslation();
  const gate = useLaunchGate();

  const sectionKey = route?.params?.section;
  const section = sectionKey ? businessOsSection(sectionKey as never) : undefined;
  /*
   * The one place this screen asks whether a capability can be used. The split
   * is not `isLaunchReady` because readiness alone answers a narrower question
   * than the rows need — see `sectionCapabilities.ts`.
   */
  const { available, upcoming } = sectionKey
    ? sectionCapabilityLists(sectionKey)
    : { available: [], upcoming: [] };

  /*
   * Reached with a section this screen has no landing for — an unknown key from
   * a deep link, or a section that has since been given its own screen. Say so
   * plainly rather than rendering an empty shell that looks like a load failure.
   */
  if (!section || available.length + upcoming.length === 0) {
    return (
      <Screen title={t("commerce:launch.sectionFallbackTitle")}>
        <Panel>
          <Text style={styles.muted}>{t("commerce:launch.sectionFallbackBody")}</Text>
        </Panel>
      </Screen>
    );
  }

  /*
   * Every row goes through the gate rather than navigating directly, for the
   * same reason the hub's tiles do: the conditional IS the navigation, so a row
   * cannot become live by someone forgetting a check here.
   *
   * A capability with nowhere to go passes no callback at all. That is the gate's
   * documented way of saying "there is nothing to open" — it hands the refusal to
   * the one place that already decides, instead of the old `if (!module.route)
   * return;`, which returned from inside the callback after the gate had already
   * concluded the tap would land somewhere, and so read to the user as a dead tap.
   */
  function openModule(capability: ResolvedCapability) {
    const destination = capability.available ? capability.route : undefined;
    gate.open(
      capability.id,
      capability.module.label,
      destination ? () => navigation.navigate(destination, capability.params) : undefined
    );
  }

  return (
    <Screen title={section.label} subtitle={section.blurb}>
      {available.length ? (
        <Panel>
          <Text style={styles.panelTitle}>{t("commerce:launch.availableTitle")}</Text>
          <Text style={styles.muted}>{t("commerce:launch.availableBody")}</Text>
          <View style={styles.rows}>
            {available.map((capability) => (
              <LaunchModuleRow
                key={capability.module.key}
                id={capability.id}
                label={capability.module.label}
                blurb={capability.module.blurb}
                icon={capability.module.icon}
                availability={capability.availability}
                onPress={() => openModule(capability)}
              />
            ))}
          </View>
        </Panel>
      ) : null}

      {upcoming.length ? (
        <Panel>
          <Text style={styles.panelTitle}>{t("commerce:launch.upcomingTitle")}</Text>
          <Text style={styles.muted}>{t("commerce:launch.upcomingBody")}</Text>
          <View style={styles.rows}>
            {upcoming.map((capability) => (
              <LaunchModuleRow
                key={capability.module.key}
                id={capability.id}
                label={capability.module.label}
                blurb={capability.module.blurb}
                icon={capability.module.icon}
                availability={capability.availability}
                onPress={() => openModule(capability)}
              />
            ))}
          </View>
        </Panel>
      ) : null}

      <ComingSoonSheet target={gate.target} onDismiss={gate.dismiss} />
    </Screen>
  );
}

const styles = createThemedStyles(() => ({
  muted: {
    color: colors.muted,
    fontSize: 13,
    lineHeight: 19
  },
  panelTitle: {
    color: colors.text,
    fontSize: 16,
    fontWeight: "800"
  },
  rows: {
    gap: 10,
    marginTop: 4
  }
}));

export default BusinessOsSectionScreen;
