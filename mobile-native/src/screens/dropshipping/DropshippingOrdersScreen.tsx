/**
 * Supplier orders — the half of an order that is not the customer's.
 *
 * ## Two orders, deliberately
 *
 * A customer order is a promise the merchant made to a buyer. A supplier order
 * is a separate purchase the merchant makes to keep that promise. They have
 * different money, different states and different failure modes: a supplier
 * order can be refused while the customer order stands, and collapsing them
 * into one row would leave the merchant with no way to see that.
 *
 * ## This screen shows nothing, and says why
 *
 * The fulfilment layer can create and read a single supplier order by id, but
 * nothing enumerates a merchant's supplier orders — so this app has no list to
 * render. It renders the absence.
 *
 * Inventing rows here would be the single most damaging piece of fake data in
 * the feature: a merchant reading "Order placed with supplier · tracking
 * 1Z…" concludes their customer's parcel is moving. They would stop chasing it.
 * The empty screen is worse product and better information, and the tradeoff is
 * not close.
 *
 * The gap is also machine-readable in `DROPSHIPPING_DATA_GAPS`, so a later
 * change that fakes it moves a number a test is watching.
 */

import { ScrollView, StyleSheet, Text, View } from "react-native";
import { useSafeAreaInsets } from "react-native-safe-area-context";
import { DROPSHIPPING_DATA_GAPS } from "../../api/dropshipping";
import { StoreHeader } from "../../components/store";
import { DropshippingGapNote } from "../../components/dropshipping/DropshippingStates";
import { BOTTOM_NAV_CONTENT_CLEARANCE } from "../../navigation/BottomNavVisibility";
import { RootStackParamList } from "../../navigation/types";
import { storeLight } from "../../theme/storeLight";
import { useLogiNexusReducedMotion } from "../../theme/logiNexusMotion";

type Props = {
  route?: { params?: RootStackParamList["DropshippingOrders"] };
  navigation: { navigate: (...args: any[]) => void; goBack?: () => void };
};

export function DropshippingOrdersScreen({ route, navigation }: Props) {
  const reducedMotion = useLogiNexusReducedMotion();
  const insets = useSafeAreaInsets();

  return (
    <View style={styles.root}>
      <StoreHeader
        title={route?.params?.title || "Supplier orders"}
        query=""
        onQueryChange={() => undefined}
        onSubmitSearch={() => undefined}
        onBack={() => navigation.goBack?.()}
        onNotifications={() => navigation.navigate("BusinessOsActivity")}
        unreadCount={0}
        searchPlaceholder="Supplier orders"
        reducedMotion={reducedMotion}
      />

      <ScrollView
        contentContainerStyle={[
          styles.content,
          { paddingBottom: Math.max(insets.bottom, 16) + BOTTOM_NAV_CONTENT_CLEARANCE }
        ]}
      >
        <View style={styles.card}>
          <Text style={styles.cardTitle}>Your orders and your supplier's are separate</Text>
          <Text style={styles.cardBody}>
            When a customer buys one of your dropshipped products, that's your order with them. A
            second order — yours with your supplier — is what actually gets the item shipped.
            PulseSoc keeps them apart so you can always see which one is stuck.
          </Text>
        </View>

        <View style={styles.card}>
          <Text style={styles.cardTitle}>Sandbox fulfilment</Text>
          <Text style={styles.cardBody}>
            Sending real orders to suppliers is switched off platform-wide. You can import and sell,
            but no purchase is placed with your supplier and nothing ships yet.
          </Text>
        </View>

        {/* Rendered from the exported list rather than typed out, so the screen
            cannot claim a gap has closed while the list still says it is open. */}
        {DROPSHIPPING_DATA_GAPS.map((gap) => (
          <DropshippingGapNote
            key={gap.surface}
            title={`${gap.surface} isn't available yet`}
            body="Once supplier fulfilment is switched on, this is where you'll see each supplier order and its tracking."
          />
        ))}
      </ScrollView>
    </View>
  );
}

const styles = StyleSheet.create({
  root: { flex: 1, backgroundColor: storeLight.bg.page },
  content: { padding: storeLight.space.card, gap: storeLight.space.gutter },
  card: {
    padding: storeLight.space.card,
    gap: 8,
    backgroundColor: storeLight.bg.card,
    borderRadius: storeLight.radius.card,
    borderWidth: StyleSheet.hairlineWidth,
    borderColor: storeLight.border.hairline
  },
  cardTitle: { fontSize: 15, fontWeight: "700", color: storeLight.text.primary },
  cardBody: { fontSize: 13, color: storeLight.text.muted, lineHeight: 18 }
});
