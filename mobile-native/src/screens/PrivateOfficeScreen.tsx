/**
 * Private Office — the room, and the three things it is for.
 *
 * ## What this screen is allowed to claim
 *
 * Nothing on it is decided here. The entry state and the list of children
 * arrive from `/api/private-office/overview`, which is rendered by
 * `services/private_office/office.product_state` over the canonical feature
 * matrix. This screen reads `opens` to decide tappability, and computes it
 * nowhere.
 *
 * That is deliberate to the point of being awkward: it would be shorter to keep
 * a local list of the capabilities and light them up by tier. It would also be
 * a second authority on what exists, and the first time a capability ships or
 * is killed the two would disagree — with the client winning, because the
 * client is what the member sees. So the list itself comes down the wire.
 * The only local table is `COPY_KEYS`, which maps a feature id to a translation
 * key, and an id missing from it still renders (as its raw id) rather than
 * silently vanishing from the list.
 *
 * That property is why the narrowing of this office to Relationship
 * Intelligence and Private Meetings was done in `OFFICE_CHILD_IDS` on the
 * server and not by deleting tiles here. Deleting a tile hides a capability;
 * shortening that tuple retires it.
 *
 * ## Why there is no "coming later" section any more
 *
 * There used to be one, listing children the server had sent with a reason they
 * could not be opened — not built, awaiting a provider, switched off. It was an
 * honest section and it was still a catalogue of things the member could not
 * have. A capability the server does not open is now simply not drawn. The
 * office shows what it is, not what it might become.
 *
 * ## Why a degraded resolve is not "you don't have this"
 *
 * ENTRY_UNKNOWN means the tier resolver did not answer. The screen says so and
 * offers a retry. Rendering it as an empty or locked office would be a
 * confident answer to a question we failed to ask, told to the member most
 * likely to have paid for the thing.
 *
 * ## Office Security is not a capability
 *
 * It is drawn as a third card because that is what it is to the member, but it
 * comes from neither the wire nor the entitlement matrix. It is the lock on
 * this room: present whenever the room can be opened at all, and never
 * something a tier could fail to include.
 */

import { NativeStackScreenProps } from "@react-navigation/native-stack";
import { useCallback, useEffect, useState } from "react";
import { ActivityIndicator, Pressable, RefreshControl, ScrollView, StyleSheet, Text, View } from "react-native";
import { Ionicons } from "@expo/vector-icons";
import { useSafeAreaInsets } from "react-native-safe-area-context";
import {
  PrivateOfficeChild,
  PrivateOfficeOverview,
  UNKNOWN_OVERVIEW,
  getPrivateOfficeOverview
} from "../api/privateOffice";
import { useTranslation } from "../i18n";
import { BOTTOM_NAV_CONTENT_CLEARANCE } from "../navigation/BottomNavVisibility";
import { RootStackParamList } from "../navigation/types";
import { PrivateOfficeLockGate } from "../privateOffice/PrivateOfficeLockGate";
import { lockOfficeLocally } from "../privateOffice/officeLock";
import { colors } from "../theme/colors";

type Props = NativeStackScreenProps<RootStackParamList, "PrivateOffice">;

/**
 * Feature id → translation key stem. Copy only.
 *
 * This table says what a capability is *called*. It never says whether it is
 * available; that word always comes from the server row next to it.
 */
const COPY_KEYS: Readonly<Record<string, string>> = {
  relationship_intelligence: "relationshipIntelligence",
  private_meetings: "privateMeetings"
};

/**
 * Feature id → the screen that actually exists for it.
 *
 * Only built capabilities appear. A row whose id is absent here is
 * never tappable even if the server said it opens — a missing destination is a
 * client bug, and the honest failure is a row that does not move rather than a
 * tap into a screen that is not registered.
 */
const DESTINATIONS: Readonly<Record<string, keyof RootStackParamList>> = {
  relationship_intelligence: "PrivatePeople",
  private_meetings: "PrivateMeetings"
};

const ICONS: Readonly<Record<string, keyof typeof Ionicons.glyphMap>> = {
  relationship_intelligence: "people-outline",
  private_meetings: "videocam-outline"
};

type LoadState = "LOADING" | "LOADED";

/**
 * The screen exports a gated shell (Stage 19: a deep link lands on the correct
 * lock door and this content resumes after unlock) and keeps the original
 * component as the unlocked body.
 */
export function PrivateOfficeScreen(props: Props) {
  return (
    <PrivateOfficeLockGate
      onDismiss={() => props.navigation.goBack()}
      onRenew={() => props.navigation.navigate("Premium")}
    >
      <PrivateOfficeBody {...props} />
    </PrivateOfficeLockGate>
  );
}

function PrivateOfficeBody({ navigation }: Props) {
  const { t } = useTranslation();
  const insets = useSafeAreaInsets();
  const [loadState, setLoadState] = useState<LoadState>("LOADING");
  const [refreshing, setRefreshing] = useState(false);
  const [overview, setOverview] = useState<PrivateOfficeOverview>(UNKNOWN_OVERVIEW);

  const load = useCallback(async () => {
    const next = await getPrivateOfficeOverview();
    // The grant died between the gate's check and this fetch; drop the local
    // token so the enclosing gate shows the door instead of a stale office.
    //
    // The office's own endpoint is what is asked. This used to ride on the
    // records `attention` read, which meant the lock state of the room was
    // reported by one of the things inside it — and when that capability left
    // the surface the relock would have left with it. `locked` is on the
    // overview payload for exactly this reason.
    if (next.locked) lockOfficeLocally();
    setOverview(next);
    setLoadState("LOADED");
  }, []);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      const next = await getPrivateOfficeOverview();
      if (cancelled) return;
      if (next.locked) lockOfficeLocally();
      setOverview(next);
      setLoadState("LOADED");
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  const onRefresh = useCallback(async () => {
    setRefreshing(true);
    try {
      await load();
    } finally {
      setRefreshing(false);
    }
  }, [load]);

  const open = useCallback(
    (child: PrivateOfficeChild) => {
      const destination = DESTINATIONS[child.featureId];
      if (!child.opens || !destination) return;
      navigation.navigate(destination as never);
    },
    [navigation]
  );

  const office = overview.office;
  const label = (featureId: string, part: "label" | "hint") => {
    const stem = COPY_KEYS[featureId];
    if (!stem) return part === "label" ? featureId : "";
    return t(`premium:privateOffice.features.${stem}.${part}`);
  };

  return (
    <ScrollView
      style={styles.root}
      contentContainerStyle={[
        styles.content,
        { paddingBottom: Math.max(insets.bottom, 18) + BOTTOM_NAV_CONTENT_CLEARANCE }
      ]}
      refreshControl={
        <RefreshControl refreshing={refreshing} onRefresh={onRefresh} tintColor={colors.accent} />
      }
    >
      <View style={styles.header}>
        <Text style={styles.title}>{t("premium:privateOffice.title")}</Text>
        <Text style={styles.subtitle}>{t("premium:privateOffice.subtitle")}</Text>
      </View>

      {loadState === "LOADING" ? (
        <View style={styles.panel} accessibilityRole="progressbar">
          <ActivityIndicator color={colors.accent} />
          <Text style={styles.panelText}>{t("premium:privateOffice.loading")}</Text>
        </View>
      ) : null}

      {loadState === "LOADED" && office.state === "ENTRY_UNKNOWN" ? (
        <View style={styles.panel}>
          <Ionicons name="cloud-offline-outline" size={22} color={colors.warning} />
          <Text style={styles.panelTitle}>{t("premium:privateOffice.unknown.title")}</Text>
          <Text style={styles.panelText}>{t("premium:privateOffice.unknown.body")}</Text>
          <Pressable style={styles.retry} onPress={onRefresh} accessibilityRole="button">
            <Text style={styles.retryText}>{t("premium:privateOffice.retry")}</Text>
          </Pressable>
        </View>
      ) : null}

      {loadState === "LOADED" && office.state === "ENTRY_UPGRADE_REQUIRED" ? (
        <View style={styles.panel}>
          <Ionicons name="lock-closed-outline" size={22} color={colors.warning} />
          <Text style={styles.panelTitle}>{t("premium:privateOffice.upgrade.title")}</Text>
          <Text style={styles.panelText}>
            {office.upgradeTier
              ? t("premium:privateOffice.upgrade.body", { tier: office.upgradeTier })
              : t("premium:privateOffice.upgrade.bodyGeneric")}
          </Text>
        </View>
      ) : null}

      {loadState === "LOADED" && office.state === "ENTRY_UNAVAILABLE" ? (
        <View style={styles.panel}>
          <Ionicons name="construct-outline" size={22} color={colors.muted} />
          <Text style={styles.panelTitle}>{t("premium:privateOffice.unavailable.title")}</Text>
          <Text style={styles.panelText}>{t("premium:privateOffice.unavailable.body")}</Text>
        </View>
      ) : null}

      {loadState === "LOADED" && office.state !== "ENTRY_UNKNOWN" ? (
        <View style={styles.cards}>
          {office.available.map((child) => (
            <OfficeCard
              key={child.featureId}
              icon={ICONS[child.featureId] || "ellipse-outline"}
              label={label(child.featureId, "label")}
              hint={label(child.featureId, "hint")}
              openLabel={t("premium:privateOffice.open")}
              // A row the server did not open, or one this build has no screen
              // for, is inert rather than a tap into nothing. Both are the same
              // failure to the member: a card that does not move.
              disabled={!child.opens || !DESTINATIONS[child.featureId]}
              onPress={() => open(child)}
            />
          ))}
          <OfficeCard
            icon="lock-closed-outline"
            label={t("premium:privateOffice.security.row.label")}
            hint={t("premium:privateOffice.security.row.hint")}
            openLabel={t("premium:privateOffice.open")}
            disabled={false}
            onPress={() => navigation.navigate("PrivateOfficeSecurity" as never)}
          />
        </View>
      ) : null}

      {loadState === "LOADED" ? (
        <Text style={styles.footnote}>{t("premium:privateOffice.footnote")}</Text>
      ) : null}
    </ScrollView>
  );
}

/**
 * One capability, drawn the same way whatever it is.
 *
 * Office Security uses this component too. The member does not experience the
 * lock as a different *kind* of thing from the two capabilities, and giving it
 * its own section header with one item in it was the old screen admitting the
 * office had grown into a list of lists.
 */
function OfficeCard({
  icon,
  label,
  hint,
  openLabel,
  disabled,
  onPress
}: {
  icon: keyof typeof Ionicons.glyphMap;
  label: string;
  hint: string;
  openLabel: string;
  disabled: boolean;
  onPress: () => void;
}) {
  return (
    <Pressable
      style={({ pressed }) => [styles.card, pressed && !disabled ? styles.cardPressed : null]}
      onPress={onPress}
      disabled={disabled}
      accessibilityRole="button"
      accessibilityState={{ disabled }}
      accessibilityLabel={label}
      accessibilityHint={hint}
    >
      <View style={styles.cardBadge}>
        <Ionicons name={icon} size={20} color={colors.accent} />
      </View>
      <View style={styles.cardBody}>
        <Text style={styles.cardLabel}>{label}</Text>
        {hint ? <Text style={styles.cardHint}>{hint}</Text> : null}
      </View>
      <Text style={styles.openMark}>{openLabel}</Text>
    </Pressable>
  );
}

const styles = StyleSheet.create({
  root: { flex: 1, backgroundColor: colors.background },
  content: { padding: 18, gap: 16 },
  header: { gap: 6 },
  title: { color: colors.text, fontSize: 26, fontWeight: "800", letterSpacing: 1.2 },
  subtitle: { color: colors.muted, fontSize: 14, lineHeight: 20 },
  panel: {
    backgroundColor: colors.surface,
    borderColor: colors.border,
    borderWidth: 1,
    borderRadius: 16,
    padding: 18,
    gap: 8,
    alignItems: "flex-start"
  },
  panelTitle: { color: colors.text, fontSize: 15, fontWeight: "700" },
  panelText: { color: colors.muted, fontSize: 13, lineHeight: 19 },
  retry: {
    marginTop: 6,
    paddingHorizontal: 14,
    paddingVertical: 8,
    borderRadius: 999,
    backgroundColor: colors.surfaceRaised,
    borderColor: colors.border,
    borderWidth: 1
  },
  retryText: { color: colors.accentStrong, fontSize: 13, fontWeight: "700" },
  /**
   * The whole office, in three cards. `gap` rather than per-card margin so the
   * rhythm is one number instead of one per edge.
   */
  cards: { gap: 12 },
  card: {
    flexDirection: "row",
    alignItems: "center",
    gap: 14,
    backgroundColor: colors.surface,
    borderColor: colors.border,
    borderWidth: 1,
    borderRadius: 18,
    paddingVertical: 18,
    paddingHorizontal: 16
  },
  cardPressed: { backgroundColor: colors.surfaceRaised },
  cardBadge: {
    width: 42,
    height: 42,
    borderRadius: 14,
    alignItems: "center",
    justifyContent: "center",
    backgroundColor: colors.surfaceRaised,
    borderColor: colors.border,
    borderWidth: 1
  },
  cardBody: { flex: 1, gap: 4 },
  cardLabel: { color: colors.text, fontSize: 16, fontWeight: "700", letterSpacing: 0.2 },
  cardHint: { color: colors.muted, fontSize: 12.5, lineHeight: 18 },
  openMark: { color: colors.accent, fontSize: 11, fontWeight: "800", letterSpacing: 1 },
  footnote: { color: colors.muted, fontSize: 11, lineHeight: 16 }
});

export default PrivateOfficeScreen;
