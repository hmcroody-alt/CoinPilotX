/**
 * One order, rendered from either end. The card is the same object seen from two
 * perspectives, so it shares every sub-component (timeline, source badge, status
 * pill, escrow panel) with only labels and the action row differing:
 *
 *   • seller → counterparty is the buyer; the action row offers the next
 *     fulfillment step (pack / ship / handoff / view payout) via sellerActionsFor.
 *     Fulfillment writes are flag-gated previews today, so those buttons render
 *     disabled with their reason rather than as live no-ops.
 *   • buyer  → counterparty is the seller; the action row offers tracking / buy
 *     again; the escrow safety panel appears on pickup orders when presentable.
 *
 * Money is display-only (order.amountLabel — already server-formatted). Actions
 * are owned by the parent through `onAction`; this card enforces the idempotency
 * surface (disable-on-tap, in-flight/busy) but never calls a backend itself, so a
 * double-tap cannot fire two transitions.
 */

import { useState } from "react";
import { ActivityIndicator, Pressable, StyleSheet, Text, View } from "react-native";
import { ordersLight } from "../../theme/ordersLight";
import {
  OrderPerspective,
  SellerActionKey,
  UnifiedOrder,
  sellerActionsFor
} from "../../api/ordersDashboard";
import { OrderTimeline } from "./OrderTimeline";
import { OrdersStatusPill } from "./OrdersStatusPill";
import { SourceBadge } from "./SourceBadge";
import { DeadlineLine } from "./DeadlineLine";
import { EscrowSafetyPanel } from "./EscrowSafetyPanel";

export function OrderCard({
  order,
  perspective,
  deadline,
  onAction,
  onOpenTracking,
  onBuyAgain
}: {
  order: UnifiedOrder;
  perspective: OrderPerspective;
  /** Seller-only MOCK ship-by deadline, passed down as a preview. */
  deadline?: string;
  onAction?: (key: SellerActionKey, order: UnifiedOrder) => Promise<void> | void;
  onOpenTracking?: (order: UnifiedOrder) => void;
  onBuyAgain?: (order: UnifiedOrder) => void;
}) {
  const counterpartyLine =
    perspective === "seller" ? `Buyer · ${order.counterpartyName}` : `Seller · ${order.counterpartyName}`;

  return (
    <View style={styles.card}>
      <View style={styles.topRow}>
        <View style={styles.thumbWrap}>
          <View style={styles.thumb} />
          {order.quantity > 1 ? (
            <View style={styles.qtyBadge} accessibilityLabel={`Quantity ${order.quantity}`}>
              <Text style={styles.qtyText}>×{order.quantity}</Text>
            </View>
          ) : null}
        </View>

        <View style={styles.headText}>
          <View style={styles.badgeRow}>
            <SourceBadge source={order.source} />
            <OrdersStatusPill status={order.status} />
          </View>
          <Text style={styles.title} numberOfLines={2}>
            {order.title}
          </Text>
          <Text style={styles.meta} numberOfLines={1}>
            #{order.reference} · {counterpartyLine}
          </Text>
        </View>

        <Text style={styles.amount}>{order.amountLabel}</Text>
      </View>

      {perspective === "seller" && deadline ? (
        <DeadlineLine deadline={deadline} preview />
      ) : null}

      <OrderTimeline status={order.status} variant={order.variant} perspective={perspective} />

      {order.escrowPresentable ? <EscrowSafetyPanel order={order} perspective={perspective} /> : null}

      {perspective === "seller" ? (
        <SellerActionRow order={order} onAction={onAction} />
      ) : (
        <BuyerActionRow order={order} onOpenTracking={onOpenTracking} onBuyAgain={onBuyAgain} />
      )}
    </View>
  );
}

/* ------------------------------------------------------------------ *
 * Seller action row — idempotent disable-on-tap. The row owns only the
 * in-flight guard; the actual transition is the parent's job via onAction.
 * ------------------------------------------------------------------ */

function SellerActionRow({
  order,
  onAction
}: {
  order: UnifiedOrder;
  onAction?: (key: SellerActionKey, order: UnifiedOrder) => Promise<void> | void;
}) {
  const [busyKey, setBusyKey] = useState<SellerActionKey | null>(null);
  const actions = sellerActionsFor(order);

  async function run(key: SellerActionKey) {
    if (busyKey) return; // in-flight guard — a second tap is ignored
    setBusyKey(key);
    try {
      await onAction?.(key, order);
    } finally {
      setBusyKey(null);
    }
  }

  return (
    <View style={styles.actionRow}>
      {actions.map((action) => {
        const isPrimary = action.key === "view_payout";
        const busy = busyKey === action.key;
        const disabled = !action.enabled || Boolean(busyKey);
        return (
          <View key={action.key} style={styles.actionCell}>
            <Pressable
              style={[
                styles.btn,
                isPrimary ? styles.btnPrimary : styles.btnSecondary,
                disabled && !isPrimary ? styles.btnDisabled : null
              ]}
              disabled={disabled}
              accessibilityRole="button"
              accessibilityState={{ disabled, busy }}
              accessibilityLabel={action.label}
              onPress={() => run(action.key)}
            >
              {busy ? (
                <ActivityIndicator size="small" color={ordersLight.text.primary} />
              ) : (
                <Text
                  style={[
                    styles.btnText,
                    isPrimary ? styles.btnTextPrimary : styles.btnTextSecondary,
                    disabled && !isPrimary ? styles.btnTextDisabled : null
                  ]}
                  numberOfLines={1}
                >
                  {action.label}
                </Text>
              )}
            </Pressable>
            {action.reason ? (
              <Text style={styles.reason} numberOfLines={2}>
                {action.reason}
              </Text>
            ) : null}
          </View>
        );
      })}
    </View>
  );
}

/* ------------------------------------------------------------------ *
 * Buyer action row — tracking + buy again. No money moves here.
 * ------------------------------------------------------------------ */

function BuyerActionRow({
  order,
  onOpenTracking,
  onBuyAgain
}: {
  order: UnifiedOrder;
  onOpenTracking?: (order: UnifiedOrder) => void;
  onBuyAgain?: (order: UnifiedOrder) => void;
}) {
  const canTrack = order.variant === "shipping" && Boolean(order.tracking?.available);
  const canBuyAgain = order.status === "delivered" || order.status === "complete";

  return (
    <View style={styles.actionRow}>
      {canTrack ? (
        <View style={styles.actionCell}>
          <Pressable
            style={[styles.btn, styles.btnSecondary]}
            accessibilityRole="button"
            accessibilityLabel="Track package"
            onPress={() => onOpenTracking?.(order)}
          >
            <Text style={[styles.btnText, styles.btnTextSecondary]}>Track package</Text>
          </Pressable>
        </View>
      ) : null}
      {canBuyAgain ? (
        <View style={styles.actionCell}>
          <Pressable
            style={[styles.btn, styles.btnPrimary]}
            accessibilityRole="button"
            accessibilityLabel={`Buy ${order.title} again`}
            onPress={() => onBuyAgain?.(order)}
          >
            <Text style={[styles.btnText, styles.btnTextPrimary]}>Buy again</Text>
          </Pressable>
        </View>
      ) : null}
    </View>
  );
}

const styles = StyleSheet.create({
  card: {
    backgroundColor: ordersLight.bg.card,
    borderRadius: ordersLight.radius.card,
    borderColor: ordersLight.border.hairline,
    borderWidth: StyleSheet.hairlineWidth,
    padding: ordersLight.space.card,
    gap: 12
  },
  topRow: {
    flexDirection: "row",
    alignItems: "flex-start",
    gap: 12
  },
  thumbWrap: {
    width: ordersLight.size.thumb,
    height: ordersLight.size.thumb
  },
  thumb: {
    width: ordersLight.size.thumb,
    height: ordersLight.size.thumb,
    borderRadius: ordersLight.radius.thumb,
    backgroundColor: ordersLight.bg.skeleton
  },
  qtyBadge: {
    position: "absolute",
    right: -6,
    top: -6,
    minWidth: 22,
    height: 22,
    paddingHorizontal: 5,
    borderRadius: 11,
    backgroundColor: ordersLight.quantity.badge,
    alignItems: "center",
    justifyContent: "center"
  },
  qtyText: {
    color: ordersLight.quantity.text,
    fontSize: 11,
    fontWeight: "800"
  },
  headText: {
    flex: 1,
    gap: 5
  },
  badgeRow: {
    flexDirection: "row",
    alignItems: "center",
    gap: 6,
    flexWrap: "wrap"
  },
  title: {
    fontSize: 15,
    fontWeight: "800",
    color: ordersLight.text.primary
  },
  meta: {
    fontSize: 12,
    color: ordersLight.text.muted
  },
  amount: {
    fontSize: 15,
    fontWeight: "800",
    color: ordersLight.text.primary
  },
  actionRow: {
    flexDirection: "row",
    flexWrap: "wrap",
    gap: 8
  },
  actionCell: {
    flexGrow: 1,
    flexBasis: "45%",
    gap: 4
  },
  btn: {
    minHeight: ordersLight.size.tapTarget,
    borderRadius: ordersLight.radius.control,
    alignItems: "center",
    justifyContent: "center",
    paddingHorizontal: 12
  },
  btnPrimary: {
    backgroundColor: ordersLight.cta.from
  },
  btnSecondary: {
    borderWidth: StyleSheet.hairlineWidth,
    borderColor: ordersLight.border.secondaryButton,
    backgroundColor: ordersLight.bg.card
  },
  btnDisabled: {
    opacity: 0.5
  },
  btnText: {
    fontSize: 13,
    fontWeight: "800"
  },
  btnTextPrimary: {
    color: ordersLight.cta.text
  },
  btnTextSecondary: {
    color: ordersLight.text.primary
  },
  btnTextDisabled: {
    color: ordersLight.text.muted
  },
  reason: {
    fontSize: 11,
    color: ordersLight.text.muted,
    lineHeight: 15
  }
});
