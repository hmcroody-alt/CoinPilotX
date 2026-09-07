/**
 * Connect a supplier — credential in, shop chosen, connection made.
 *
 * ## Two steps, because "connected to shop 4471" means nothing
 *
 * The merchant pastes their supplier API key, this screen asks the provider
 * which shops that key can act for, and the merchant picks one. Collapsing that
 * into a single "Connect" would silently bind the store to whichever shop the
 * provider happened to list first — and a merchant who runs two shops through
 * one supplier account would not find out until their orders went to the wrong
 * warehouse.
 *
 * ## The credential does not live on this device
 *
 * `apiKey` is component state and nothing else. It is not written to
 * `expo-secure-store`, not put in a navigation param, not logged, and not sent
 * anywhere except the two request bodies that need it. It is cleared the moment
 * the connection succeeds. The server stores it encrypted against the
 * connection; this screen is a conduit, not a keychain.
 *
 * Also why the field is `secureTextEntry` with autocorrect and autocapitalise
 * off: a supplier key that a keyboard has "helpfully" title-cased is a key that
 * fails to connect for a reason the merchant cannot see.
 *
 * ## Sandbox is stated, not implied
 *
 * The platform connects suppliers in sandbox. The merchant is told before they
 * type anything, because discovering it after importing forty products is
 * discovering it too late.
 */

import { useCallback, useState } from "react";
import { KeyboardAvoidingView, Platform, Pressable, ScrollView, StyleSheet, Text, TextInput, View } from "react-native";
import { useSafeAreaInsets } from "react-native-safe-area-context";
import {
  connectSupplier,
  discoverSupplierShops,
  stateForError,
  type DropshippingState,
  type SupplierShop
} from "../../api/dropshipping";
import { StoreHeader, StoreSectionError } from "../../components/store";
import { useDropshippingScope } from "./useDropshippingScope";
import { BOTTOM_NAV_CONTENT_CLEARANCE } from "../../navigation/BottomNavVisibility";
import { RootStackParamList } from "../../navigation/types";
import { storeLight } from "../../theme/storeLight";
import { useLogiNexusReducedMotion } from "../../theme/logiNexusMotion";

type Props = {
  route?: { params?: RootStackParamList["DropshippingConnect"] };
  navigation: { navigate: (...args: any[]) => void; goBack?: () => void };
};

type Step = "credential" | "shop" | "connecting";

export function ConnectSupplierScreen({ route, navigation }: Props) {
  const reducedMotion = useLogiNexusReducedMotion();
  const insets = useSafeAreaInsets();
  const scopeStatus = useDropshippingScope();

  const [apiKey, setApiKey] = useState("");
  const [step, setStep] = useState<Step>("credential");
  const [shops, setShops] = useState<SupplierShop[]>([]);
  const [busy, setBusy] = useState(false);
  const [failure, setFailure] = useState<{ state: DropshippingState; message: string } | null>(null);

  const scope = scopeStatus.status.phase === "ready" ? scopeStatus.status.scope : null;

  /**
   * Turn a thrown error into a sentence about *this* step.
   *
   * A rejected credential and an unreachable provider are the same HTTP shape to
   * a careless reader and completely different problems to a merchant: one means
   * "check what you pasted", the other means "this is not your fault, wait".
   */
  const describe = useCallback((error: unknown, during: "discover" | "connect") => {
    const state = stateForError(error);
    if (state === "PROVIDER_UNAVAILABLE") {
      return { state, message: "Your supplier isn't responding. Nothing was connected — try again shortly." };
    }
    if (state === "UNAUTHORIZED") {
      return { state, message: "You're not signed in to this store any more." };
    }
    if (state === "SUPPLIER_DISCONNECTED" || during === "discover") {
      return {
        state,
        message: "That key didn't work. Check you copied the whole thing from your supplier's dashboard."
      };
    }
    return { state, message: "That connection couldn't be created. Nothing was saved." };
  }, []);

  const discover = useCallback(async () => {
    if (!scope || !apiKey.trim()) return;
    setBusy(true);
    setFailure(null);
    try {
      const found = await discoverSupplierShops(scope, apiKey.trim());
      if (found.length === 0) {
        // A valid key with no shops is a real supplier state and not an error.
        // It needs a different sentence, so it is not routed through `describe`.
        setFailure({
          state: "EMPTY",
          message: "That key works, but the account has no shops we can sell through yet."
        });
        return;
      }
      setShops(found);
      setStep("shop");
    } catch (error) {
      setFailure(describe(error, "discover"));
    } finally {
      setBusy(false);
    }
  }, [apiKey, describe, scope]);

  const connect = useCallback(
    async (shop: SupplierShop) => {
      if (!scope) return;
      setStep("connecting");
      setBusy(true);
      setFailure(null);
      try {
        await connectSupplier(scope, { apiKey: apiKey.trim(), externalShopId: shop.externalShopId });
        // Cleared before navigating, so the credential does not survive in a
        // state tree behind the screen the merchant lands on.
        setApiKey("");
        navigation.navigate("DropshippingSuppliers", { title: "Suppliers" });
      } catch (error) {
        setFailure(describe(error, "connect"));
        setStep("shop");
      } finally {
        setBusy(false);
      }
    },
    [apiKey, describe, navigation, scope]
  );

  const canSubmit = Boolean(scope) && apiKey.trim().length > 0 && !busy;

  return (
    <View style={styles.root}>
      <StoreHeader
        title={route?.params?.title || "Connect a supplier"}
        query=""
        onQueryChange={() => undefined}
        onSubmitSearch={() => undefined}
        onBack={() => navigation.goBack?.()}
        onNotifications={() => navigation.navigate("BusinessOsActivity")}
        unreadCount={0}
        searchPlaceholder="Connect a supplier"
        reducedMotion={reducedMotion}
      />

      <KeyboardAvoidingView
        style={styles.flex}
        behavior={Platform.OS === "ios" ? "padding" : undefined}
      >
        <ScrollView
          contentContainerStyle={[
            styles.content,
            { paddingBottom: Math.max(insets.bottom, 16) + BOTTOM_NAV_CONTENT_CLEARANCE }
          ]}
          keyboardShouldPersistTaps="handled"
        >
          <View style={styles.card}>
            <Text style={styles.cardTitle}>Sandbox connection</Text>
            <Text style={styles.cardBody}>
              Suppliers connect in sandbox on PulseSoc today. You can browse the full catalogue and
              import real product data, but no order is placed with the supplier and nothing ships.
            </Text>
          </View>

          {scopeStatus.status.phase === "failed" ? (
            <StoreSectionError
              message="We couldn't work out which store to connect this supplier to."
              onRetry={scopeStatus.reload}
              reducedMotion={reducedMotion}
            />
          ) : null}

          {step === "credential" || step === "connecting" ? (
            <View style={styles.card}>
              <Text style={styles.cardTitle}>Your supplier access key</Text>
              <Text style={styles.cardBody}>
                Copy it from your supplier's own dashboard. PulseSoc stores it encrypted and never
                shows it again.
              </Text>
              <TextInput
                style={styles.input}
                value={apiKey}
                onChangeText={setApiKey}
                placeholder="Paste your access key"
                placeholderTextColor={storeLight.text.muted}
                // A supplier key is case- and character-exact. Every one of
                // these off is a key the keyboard would quietly mangle.
                secureTextEntry
                autoCapitalize="none"
                autoCorrect={false}
                spellCheck={false}
                editable={!busy}
                accessibilityLabel="Supplier access key"
                onSubmitEditing={() => {
                  if (canSubmit) void discover();
                }}
              />
              <Pressable
                style={[styles.primary, canSubmit ? null : styles.primaryDisabled]}
                onPress={() => void discover()}
                disabled={!canSubmit}
                accessibilityRole="button"
                accessibilityState={{ disabled: !canSubmit }}
                accessibilityLabel="Find my shops"
              >
                <Text style={styles.primaryText}>{busy ? "Checking…" : "Find my shops"}</Text>
              </Pressable>
            </View>
          ) : null}

          {step === "shop" ? (
            <View style={styles.card}>
              <Text style={styles.cardTitle}>Choose a shop</Text>
              <Text style={styles.cardBody}>
                This key can act for {shops.length === 1 ? "one shop" : `${shops.length} shops`}.
                Pick the one this store should import from.
              </Text>
              {shops.map((shop) => (
                <Pressable
                  key={shop.externalShopId}
                  style={styles.shopRow}
                  onPress={() => void connect(shop)}
                  disabled={busy}
                  accessibilityRole="button"
                  accessibilityState={{ disabled: busy }}
                  accessibilityLabel={`Connect ${shop.name || `shop ${shop.externalShopId}`}`}
                >
                  <View style={styles.flex}>
                    <Text style={styles.shopName}>{shop.name || `Shop ${shop.externalShopId}`}</Text>
                    <Text style={styles.shopId}>ID {shop.externalShopId}</Text>
                  </View>
                  <Text style={styles.shopAction}>Connect</Text>
                </Pressable>
              ))}
              <Pressable
                style={styles.secondary}
                onPress={() => {
                  setStep("credential");
                  setShops([]);
                  setFailure(null);
                }}
                accessibilityRole="button"
                accessibilityLabel="Use a different key"
              >
                <Text style={styles.secondaryText}>Use a different key</Text>
              </Pressable>
            </View>
          ) : null}

          {failure ? (
            <StoreSectionError
              message={failure.message}
              // Nothing to retry when the account genuinely has no shops, and
              // no retry for a signed-out session either — both fail identically
              // the second time.
              onRetry={
                failure.state === "EMPTY" || failure.state === "UNAUTHORIZED"
                  ? null
                  : () => void discover()
              }
              reducedMotion={reducedMotion}
            />
          ) : null}
        </ScrollView>
      </KeyboardAvoidingView>
    </View>
  );
}

const styles = StyleSheet.create({
  root: { flex: 1, backgroundColor: storeLight.bg.page },
  flex: { flex: 1 },
  content: { padding: storeLight.space.card, gap: storeLight.space.gutter },
  card: {
    padding: storeLight.space.card,
    gap: 10,
    backgroundColor: storeLight.bg.card,
    borderRadius: storeLight.radius.card,
    borderWidth: StyleSheet.hairlineWidth,
    borderColor: storeLight.border.hairline
  },
  cardTitle: { fontSize: 15, fontWeight: "700", color: storeLight.text.primary },
  cardBody: { fontSize: 13, color: storeLight.text.muted, lineHeight: 18 },
  input: {
    minHeight: storeLight.size.tapTarget,
    paddingHorizontal: 12,
    borderRadius: storeLight.radius.control,
    borderWidth: 1,
    borderColor: storeLight.border.secondaryButton,
    color: storeLight.text.primary,
    fontSize: 14
  },
  primary: {
    minHeight: storeLight.size.tapTarget,
    alignItems: "center",
    justifyContent: "center",
    borderRadius: storeLight.radius.pill,
    backgroundColor: storeLight.cta.from
  },
  primaryDisabled: { opacity: 0.5 },
  primaryText: { fontSize: 14, fontWeight: "800", color: storeLight.cta.text },
  secondary: {
    minHeight: storeLight.size.tapTarget,
    alignItems: "center",
    justifyContent: "center",
    borderRadius: storeLight.radius.pill,
    borderWidth: 1,
    borderColor: storeLight.border.secondaryButton
  },
  secondaryText: { fontSize: 13, fontWeight: "600", color: storeLight.text.primary },
  shopRow: {
    flexDirection: "row",
    alignItems: "center",
    gap: 10,
    minHeight: storeLight.size.tapTarget + 8,
    paddingVertical: 8,
    borderTopWidth: StyleSheet.hairlineWidth,
    borderTopColor: storeLight.border.hairline
  },
  shopName: { fontSize: 14, fontWeight: "600", color: storeLight.text.primary },
  shopId: { fontSize: 11, color: storeLight.text.muted },
  shopAction: { fontSize: 13, fontWeight: "700", color: storeLight.text.link }
});
