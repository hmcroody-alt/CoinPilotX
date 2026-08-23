import { NativeStackScreenProps } from "@react-navigation/native-stack";
import { useCallback, useEffect, useState } from "react";
import { ActivityIndicator, Pressable, ScrollView, Text, View } from "react-native";
import { getPageLinkOptions, PageLinkSlot, setPageLink } from "../api/pages";
import { PulseApiError } from "../api/pulseApi";
import { RootStackParamList } from "../navigation/types";
import { colors } from "../theme/colors";
import { presenceTheme } from "../theme/presenceTheme";
import { createThemedStyles } from "../theme/themedStyles";

type Props = NativeStackScreenProps<RootStackParamList, "PageConnections">;

/**
 * Connections — what a Presence is wired to, and what it could be wired to.
 *
 * A presence does not hold a shop, an ad account, a community or a music
 * catalogue. Those live in their own systems and belong to people; a presence
 * only points at them. That pointer is what raises the Shop and Music tabs, so
 * a presence that cannot be connected is a presence whose tabs can never turn
 * on — which is exactly the state every page shipped in, because `setPageLink`
 * and `listPageLinks` existed in the client with no caller anywhere.
 *
 * Everything offered here is something the member or the page's owner actually
 * holds, resolved server-side. Nobody types an id: an id in a text box is
 * unusable, and it is also the shape that let a page point at a stranger's
 * storefront and publish their inventory under its own name.
 *
 * The offers are a convenience, never a permission. `setPageLink` re-derives
 * entitlement from the same tables, so a stale offer fails there rather than
 * succeeding on the strength of having been shown.
 */

/** What connecting each kind of resource actually turns on, in the member's terms. */
const CONSEQUENCE: Record<string, string> = {
  store: "Adds a shop tab to your public page, showing the items in that shop.",
  music_artist: "Adds a music tab to your public page, showing that catalogue.",
  ad_account: "Lets campaigns for this presence run from that ad account.",
  community: "Points followers at that community from this presence."
};

/** Why a member cannot change a given connection, phrased as what they'd need. */
const NEEDS: Record<string, string> = {
  manage_marketplace: "Only an owner, admin or marketplace manager can change the shop.",
  manage_ads: "Only an owner, admin or advertising manager can change the ad account.",
  manage_links: "Only an owner, admin or manager can change this connection."
};

export function PageConnectionsScreen({ route }: Props) {
  const pageId = route.params.pageId;
  const [slots, setSlots] = useState<PageLinkSlot[]>([]);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");

  const load = useCallback(async () => {
    setError("");
    try {
      const view = await getPageLinkOptions(pageId);
      setSlots(view.links);
    } catch (loadError) {
      setError(
        loadError instanceof PulseApiError
          ? loadError.message
          : "Your connections could not be loaded."
      );
    } finally {
      setLoading(false);
    }
  }, [pageId]);

  useEffect(() => {
    load();
  }, [load]);

  async function connect(slot: PageLinkSlot, refId: string, label: string) {
    if (busy) return;
    setBusy(`${slot.link_type}:${refId}`);
    setError("");
    setMessage("");
    try {
      await setPageLink(pageId, slot.link_type, refId);
      setMessage(`${label} is now connected.`);
      // Re-read rather than patching state locally: the server decides what is
      // connected, and a connection can change what else is offered.
      await load();
    } catch (connectError) {
      setError(
        connectError instanceof PulseApiError
          ? connectError.message
          : `${label} could not be connected.`
      );
    } finally {
      setBusy("");
    }
  }

  if (loading) {
    return (
      <View style={[styles.root, styles.center]}>
        <ActivityIndicator color={presenceTheme.teal} size="large" />
      </View>
    );
  }

  return (
    <ScrollView style={styles.root} contentContainerStyle={styles.content}>
      <Text style={styles.lead}>
        Your shop, ad account, community and music live in their own places. Connecting them here
        is what lets this presence show them.
      </Text>

      {error ? <Text style={styles.error}>{error}</Text> : null}
      {message ? <Text style={styles.message}>{message}</Text> : null}

      {slots.map((slot) => {
        const connected = slot.options.find((option) => option.ref_id === slot.connected_ref_id);
        return (
          <View key={slot.link_type} style={styles.card}>
            <Text style={styles.cardTitle}>{slot.label}</Text>
            <Text style={styles.cardBody}>{CONSEQUENCE[slot.link_type] || ""}</Text>

            {slot.connected_ref_id ? (
              <Text style={styles.connected}>
                Connected: {connected ? connected.label : slot.connected_ref_id}
              </Text>
            ) : (
              <Text style={styles.notConnected}>Not connected yet.</Text>
            )}

            {!slot.can_manage ? (
              <Text style={styles.locked}>{NEEDS[slot.permission] || NEEDS.manage_links}</Text>
            ) : !slot.options.length ? (
              /* Nothing to offer is not the same as nothing to do — say which
                 of the two it is, so the member knows whether to go set one up. */
              <Text style={styles.locked}>
                You don't have {slot.label.toLowerCase()} to connect yet. Set one up first and it
                will appear here.
              </Text>
            ) : (
              slot.options.map((option) => {
                const isConnected = option.ref_id === slot.connected_ref_id;
                const isBusy = busy === `${slot.link_type}:${option.ref_id}`;
                return (
                  <Pressable
                    key={option.ref_id}
                    accessibilityRole="button"
                    accessibilityState={{ selected: isConnected, disabled: isConnected || isBusy }}
                    disabled={isConnected || Boolean(busy)}
                    style={[styles.option, isConnected && styles.optionConnected]}
                    onPress={() => connect(slot, option.ref_id, option.label)}
                  >
                    <Text style={styles.optionLabel}>{option.label}</Text>
                    <Text style={styles.optionAction}>
                      {isConnected ? "Connected" : isBusy ? "Connecting…" : "Connect"}
                    </Text>
                  </Pressable>
                );
              })
            )}
          </View>
        );
      })}

      {!slots.length && !error ? (
        <Text style={styles.locked}>There is nothing to connect for this kind of presence.</Text>
      ) : null}
    </ScrollView>
  );
}

const styles = createThemedStyles(() => ({
  card: {
    backgroundColor: colors.surface,
    borderColor: colors.border,
    borderRadius: presenceTheme.radius.card,
    borderWidth: 1,
    gap: 6,
    marginTop: 14,
    padding: 16
  },
  cardBody: {
    color: colors.muted,
    fontSize: 13,
    lineHeight: 19
  },
  cardTitle: {
    color: colors.text,
    fontSize: 16,
    fontWeight: "900"
  },
  center: {
    alignItems: "center",
    justifyContent: "center",
    padding: 24
  },
  connected: {
    color: presenceTheme.teal,
    fontSize: 13,
    fontWeight: "800",
    marginTop: 2
  },
  content: {
    padding: 16,
    paddingBottom: 48
  },
  error: {
    color: colors.danger,
    fontSize: 13,
    marginTop: 12
  },
  lead: {
    color: colors.muted,
    fontSize: 13,
    lineHeight: 20
  },
  locked: {
    color: colors.muted,
    fontSize: 12,
    lineHeight: 18,
    marginTop: 4
  },
  message: {
    color: presenceTheme.teal,
    fontSize: 13,
    fontWeight: "700",
    marginTop: 12
  },
  notConnected: {
    color: colors.muted,
    fontSize: 13,
    fontWeight: "700",
    marginTop: 2
  },
  option: {
    alignItems: "center",
    borderColor: colors.border,
    borderRadius: 10,
    borderWidth: 1,
    flexDirection: "row",
    justifyContent: "space-between",
    marginTop: 8,
    minHeight: 48,
    paddingHorizontal: 14
  },
  optionAction: {
    color: presenceTheme.teal,
    fontSize: 12,
    fontWeight: "900"
  },
  optionConnected: {
    borderColor: presenceTheme.tealBorder
  },
  optionLabel: {
    color: colors.text,
    flex: 1,
    fontSize: 14,
    fontWeight: "700",
    paddingRight: 12
  },
  root: {
    backgroundColor: colors.background,
    flex: 1
  }
}));
