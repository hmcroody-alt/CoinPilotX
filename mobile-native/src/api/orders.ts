import { readJsonCache, writeJsonCache } from "../core/cache";
import { PULSE_API_BASE_URL } from "./config";
import { pulseApi } from "./pulseApi";

const BUYER_ORDERS_CACHE_KEY = "pulsesoc.native.buyer.orders";

export type BuyerOrderStatus =
  | "pending"
  | "paid"
  | "processing"
  | "shipped"
  | "delivered"
  | "cancelled"
  | "refunded"
  | "failed";

export type BuyerOrder = {
  id: number;
  order_id?: string;
  transaction_id?: number;
  source_table?: "seller_transactions" | "creator_transactions" | string;
  item_type?: string;
  item_id?: number | string;
  marketplace_listing_id?: number;
  item_title?: string;
  title?: string;
  amount_cents?: number;
  gross_amount_cents?: number;
  currency?: string;
  status?: string;
  status_group?: BuyerOrderStatus | string;
  payment_status?: string;
  created_at?: string;
  updated_at?: string;
  receipt_url?: string;
  support_url?: string;
  dispute_url?: string;
  detail_url?: string;
  seller?: {
    user_id?: number;
    display_name?: string;
    username?: string;
    public_player_id?: string;
    avatar_url?: string;
  };
  listing?: {
    id?: number;
    title?: string;
    category?: string;
    price_label?: string;
    cover_image_url?: string;
    image_url?: string;
    thumbnail_url?: string;
  };
  tracking?: {
    available?: boolean;
    message?: string;
    tracking_number?: string;
    tracking_url?: string;
  };
};

export type BuyerOrdersResponse = {
  ok?: boolean;
  orders?: BuyerOrder[];
  purchases?: BuyerOrder[];
  order?: BuyerOrder;
  count?: number;
  message?: string;
};

export async function listBuyerOrders(params: { limit?: number } = {}) {
  const query = new URLSearchParams({
    limit: String(params.limit || 80)
  });
  const data = await pulseApi<BuyerOrdersResponse>(`/api/pulse/orders?${query.toString()}`);
  const orders = normalizeBuyerOrders(data.orders || data.purchases || []);
  await cacheBuyerOrders(orders).catch(() => undefined);
  return { ...data, orders };
}

export async function getBuyerOrder(orderId: number | string, source?: string) {
  const query = new URLSearchParams();
  if (source) query.set("source", source);
  const suffix = query.toString() ? `?${query.toString()}` : "";
  const data = await pulseApi<BuyerOrdersResponse>(`/api/pulse/orders/${encodeURIComponent(String(orderId))}${suffix}`);
  return { ...data, order: data.order ? normalizeBuyerOrder(data.order) : undefined };
}

export async function loadCachedBuyerOrders() {
  return (await readJsonCache<BuyerOrder[]>(BUYER_ORDERS_CACHE_KEY, normalizeBuyerOrders)) || [];
}

export async function cacheBuyerOrders(orders: BuyerOrder[]) {
  await writeJsonCache(BUYER_ORDERS_CACHE_KEY, normalizeBuyerOrders(orders).slice(0, 100));
}

export function buyerOrderWebUrl(order?: BuyerOrder) {
  const orderId = order?.id || order?.transaction_id || "";
  const source = order?.source_table || "";
  const query = new URLSearchParams();
  if (orderId) query.set("order_id", String(orderId));
  if (source) query.set("source", source);
  const suffix = query.toString() ? `?${query.toString()}` : "";
  return `${PULSE_API_BASE_URL}/dashboard/orders${suffix}`;
}

export function supportOrderWebUrl(order?: BuyerOrder) {
  const orderId = order?.id || order?.transaction_id || "";
  const source = order?.source_table || "";
  const query = new URLSearchParams({ topic: "order" });
  if (orderId) query.set("order_id", String(orderId));
  if (source) query.set("source", source);
  return `${PULSE_API_BASE_URL}/support?${query.toString()}`;
}

export async function openBuyerOrderFallback(url: string) {
  const absolute = /^https?:\/\//i.test(url) ? url : `${PULSE_API_BASE_URL}${url.startsWith("/") ? "" : "/"}${url}`;
  return {
    ok: false,
    target: absolute,
    status: "native_provider_boundary",
    message: "Order provider operation remains inside native Buyer Orders until the protected contract is available."
  };
}

export function normalizeBuyerOrders(orders: BuyerOrder[]) {
  return orders.map(normalizeBuyerOrder).filter((order) => order.id > 0);
}

export function normalizeBuyerOrder(order: BuyerOrder): BuyerOrder {
  const id = Number(order.id || order.transaction_id || order.order_id || 0);
  const amount = Number(order.amount_cents || order.gross_amount_cents || 0);
  const status = normalizeStatus(order.status_group || order.status || order.payment_status);
  return {
    ...order,
    id,
    transaction_id: Number(order.transaction_id || id),
    item_title: String(order.item_title || order.title || "PulseSoc purchase"),
    title: String(order.title || order.item_title || "PulseSoc purchase"),
    amount_cents: amount,
    gross_amount_cents: Number(order.gross_amount_cents || amount),
    currency: String(order.currency || "USD").toUpperCase(),
    status_group: status,
    payment_status: String(order.payment_status || status),
    seller: {
      user_id: Number(order.seller?.user_id || 0),
      display_name: String(order.seller?.display_name || "PulseSoc Seller"),
      username: String(order.seller?.username || ""),
      public_player_id: String(order.seller?.public_player_id || ""),
      avatar_url: String(order.seller?.avatar_url || "")
    },
    tracking: {
      available: Boolean(order.tracking?.available),
      message: String(order.tracking?.message || "Shipping and tracking remain provider controlled when available."),
      tracking_number: String(order.tracking?.tracking_number || ""),
      tracking_url: String(order.tracking?.tracking_url || "")
    }
  };
}

export function normalizeStatus(value?: string) {
  const status = String(value || "pending").toLowerCase();
  if (["paid", "checkout_completed", "succeeded"].includes(status)) return "paid";
  if (status.includes("refund")) return "refunded";
  if (status.includes("cancel")) return "cancelled";
  if (status.includes("ship")) return "shipped";
  if (status.includes("deliver")) return "delivered";
  if (status.includes("process")) return "processing";
  if (status.includes("fail") || status.startsWith("blocked")) return "failed";
  return "pending";
}

export function formatOrderMoney(order: BuyerOrder) {
  const amount = Number(order.amount_cents || order.gross_amount_cents || 0) / 100;
  const currency = String(order.currency || "USD").toUpperCase();
  try {
    return new Intl.NumberFormat(undefined, { style: "currency", currency }).format(amount);
  } catch {
    return `${currency} ${amount.toFixed(2)}`;
  }
}
