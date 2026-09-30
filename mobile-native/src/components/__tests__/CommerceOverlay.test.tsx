/**
 * The commerce overlay is the only thing that makes a PulseDrop publication
 * shoppable, on either surface.
 *
 * `reel_composer` renders no price, no stock and no call to action into the
 * video — deliberately, so that a Reel published in March can still show
 * September's price. A Signal's body text has the same problem for the same
 * reason: it is written once and the listing keeps moving. Everything
 * commercial about either is therefore this overlay, drawn from a payload read
 * fresh on every request. If it does not render, a PulseDrop Reel is an
 * ordinary video of a product and a PulseDrop Signal is an ordinary post.
 *
 * These tests render the overlay through the **real host cards** —
 * `ReelPlayerCard` and `PostCard` — not on its own, because the defect worth
 * guarding against is not "the overlay draws wrong" — it is "the overlay never
 * mounts". Both cards gate it behind a shape check *and* the presence of a
 * navigation handler, and the very first version of the Reel wiring shipped
 * with the handler unpassed from `ReelsScreen`, which rendered exactly nothing
 * while every unit test of the overlay passed.
 *
 * The properties pinned on each surface are deliberately the same list, because
 * the whole argument for one component over two is that the commercial rules do
 * not vary by surface:
 *
 *  1. Ordinary content is completely unchanged. No overlay, no price, no CTA.
 *  2. A withdrawn product shows the product and **no price**. The server already
 *     omits it; this proves the client does not resurrect one.
 *  3. The CTA is a real button that navigates, and the merchant is a *separate*
 *     button — because PulseDrop publishes and the seller sells, and one tap
 *     that did both would make the two accounts look like one.
 *  4. No navigation handler means no overlay, rather than an inert button.
 */
import React from "react";
import { fireEvent, render } from "@testing-library/react-native";

jest.mock("expo-av", () => {
  const ReactActual = jest.requireActual("react");
  return {
    ResizeMode: { COVER: "cover", CONTAIN: "contain" },
    Audio: { Sound: { createAsync: jest.fn() }, setAudioModeAsync: jest.fn().mockResolvedValue(undefined) },
    Video: ReactActual.forwardRef(() => null)
  };
});
jest.mock("../../core/mediaPlaybackCoordinator", () => ({
  claimMediaPlayback: jest.fn().mockResolvedValue(true),
  releaseMediaPlayback: jest.fn().mockResolvedValue(undefined)
}));
jest.mock("../../media/mediaAccess", () => ({
  canonicalMediaPlaybackUrl: (url: string) => url,
  refreshCanonicalMediaAccess: jest.fn().mockResolvedValue(undefined)
}));
jest.mock("../../media/useTapMuteLike", () => ({
  useTapMuteLike: () => ({ onPress: jest.fn(), onLongPress: jest.fn() })
}));
jest.mock("../../media/MediaGestureFeedback", () => {
  const ReactActual = jest.requireActual("react");
  return {
    LikeBurst: ReactActual.forwardRef(() => null),
    MuteGlyphPulse: ReactActual.forwardRef(() => null)
  };
});
jest.mock("@react-native-async-storage/async-storage", () => ({
  getItem: jest.fn(),
  setItem: jest.fn(),
  removeItem: jest.fn()
}));
jest.mock("expo-haptics", () => ({
  impactAsync: jest.fn().mockResolvedValue(undefined),
  notificationAsync: jest.fn().mockResolvedValue(undefined),
  selectionAsync: jest.fn().mockResolvedValue(undefined),
  ImpactFeedbackStyle: { Light: "light", Medium: "medium", Heavy: "heavy" },
  NotificationFeedbackType: { Success: "success", Warning: "warning", Error: "error" }
}));
jest.mock("@expo/vector-icons", () => ({ Ionicons: ({ name }: { name: string }) => name }));
jest.mock("../NativeMediaViewer", () => ({
  NativeMediaViewer: () => null,
  mediaViewerItemFromPulseMedia: jest.fn()
}));
jest.mock("../reels/ReelPhotoSurface", () => ({ ReelPhotoSurface: () => null }));
jest.mock("../reels/ReelCarouselSurface", () => ({ ReelCarouselSurface: () => null }));
jest.mock("../reels/ReelLiveViewerSurface", () => ({ ReelLiveViewerSurface: () => null }));
jest.mock("../../sharing/nativeShare", () => ({ sharePulseObject: jest.fn().mockResolvedValue({ ok: true }) }));
jest.mock("../ContentTranslation", () => {
  const { Text } = jest.requireActual("react-native");
  const ReactActual = jest.requireActual("react");
  return { ContentTranslation: ({ text }: any) => ReactActual.createElement(Text, null, text) };
});

import { ReelPlayerCard } from "../ReelPlayerCard";
import { PostCard } from "../PostCard";
import type { PulsePost } from "../../api/feed";
import { activateLocale } from "../../i18n/engine";
import type { PulseCommerceOverlay } from "../../api/pulseCommerceOverlay";

function commerce(patch: Partial<PulseCommerceOverlay> = {}): PulseCommerceOverlay {
  return {
    pulsedrop: true,
    surface: "reel",
    publication_id: 5,
    attribution: {
      token: "pd1.5.reel.77",
      publisher_role: "publisher",
      merchant_role: "merchant",
      listing_id: 77,
      seller_user_id: 10,
      surface: "reel"
    },
    product: {
      listing_id: 77,
      title: "Aurora Desk Lamp",
      price_label: "$49.00",
      currency: "USD",
      cover_image_url: "https://cdn.example/lamp.jpg",
      buyer_visible: true,
      inventory_state: "in_stock",
      quantity: 4,
      product_type: "physical",
      denial_code: ""
      // No `route`/`screen` on the product: `_product` in
      // `services/pulsedrop/hydration.py` has never emitted either. The
      // product's destination lives on `cta`, the merchant's on `seller`.
    },
    seller: {
      user_id: 10,
      store_name: "Northlight Studio",
      username: "northlight",
      route: "/pulse/merchant/10",
      screen: "MerchantProfile"
    },
    label: { key: "TRENDING", i18n_key: "commerce:pulsedrop.label.trending", fallback: "Trending" },
    cta: {
      code: "VIEW_PRODUCT",
      i18n_key: "commerce:pulsedrop.cta.viewProduct",
      fallback: "View product",
      enabled: true,
      route: "/pulse/marketplace/77",
      screen: "MarketplaceDetail",
      url: "https://pulsesoc.com/pulse/marketplace/77"
    },
    availability: { code: "", i18n_key: "", fallback: "", purchasable: true },
    accessibility_text: "Aurora Desk Lamp, $49.00, from Northlight Studio",
    ...patch
  };
}

function withdrawn(): PulseCommerceOverlay {
  // What `hydration.py` emits for a listing the seller took off sale: no price,
  // no route, a disabled CTA, and a state the card names instead.
  const overlay = commerce();
  overlay.product = { ...overlay.product, price_label: "", denial_code: "UNAVAILABLE", buyer_visible: false };
  overlay.cta = { ...overlay.cta, code: "NONE", enabled: false, route: "", url: "", fallback: "" };
  overlay.availability = {
    code: "UNAVAILABLE",
    i18n_key: "commerce:pulsedrop.availability.unavailable",
    fallback: "No longer available",
    purchasable: false
  };
  return overlay;
}

function renderCard(overrides: Record<string, unknown>, handlers: Record<string, unknown> = {}) {
  const noop = jest.fn();
  return render(
    <ReelPlayerCard
      reel={
        {
          id: 88,
          reel_id: 88,
          user_id: 11,
          title: "Reel under test",
          caption: "A reel fixture.",
          video_url: "https://cdn.example/r.mp4",
          poster_url: "https://cdn.example/r.jpg",
          author: { id: 11, user_id: 11, display_name: "PulseDrop", username: "pulsedrop" },
          reactions_count: 0,
          comments_count: 0,
          media: [],
          ...overrides
        } as any
      }
      active={false}
      muted
      onToggleMuted={noop}
      onReact={noop}
      onOpenReactions={noop}
      onOpenComments={noop}
      onSave={noop}
      onRepost={noop}
      onShare={noop}
      onNotInterested={noop}
      onReport={noop}
      onFollowCreator={noop}
      onAuthorPress={noop}
      onOpenMusic={noop}
      onOpenMore={noop}
      onJoinLive={noop}
      {...(handlers as any)}
    />
  );
}

function renderPost(overrides: Partial<PulsePost>, handlers: Record<string, unknown> = {}) {
  return render(
    <PostCard
      post={
        {
          id: 91,
          body: "A PulseDrop find.",
          author: { id: 11, user_id: 11, display_name: "PulseDrop", username: "pulsedrop" },
          created_at: new Date().toISOString(),
          media: [],
          ...overrides
        } as PulsePost
      }
      {...(handlers as any)}
    />
  );
}

beforeAll(async () => {
  // The overlay's copy is extended-tier. Without this it renders humanized
  // fallbacks and every assertion about a label would be testing the wrong path.
  await activateLocale("en");
});

describe("a Reel with no commerce", () => {
  it("renders no overlay at all", () => {
    const { queryByText } = renderCard({}, { onOpenCommerceProduct: jest.fn() });
    expect(queryByText("$49.00")).toBeNull();
    expect(queryByText("View product")).toBeNull();
  });
});

describe("a PulseDrop Reel whose product is available", () => {
  it("shows the product, the price and a working call to action", () => {
    const onOpenCommerceProduct = jest.fn();
    const { getByText } = renderCard({ commerce: commerce() }, { onOpenCommerceProduct });

    expect(getByText("Aurora Desk Lamp")).toBeTruthy();
    expect(getByText("$49.00")).toBeTruthy();
    expect(getByText("Trending")).toBeTruthy();
    expect(getByText("View product")).toBeTruthy();
  });

  it("navigates to the product when the card is tapped", () => {
    const onOpenCommerceProduct = jest.fn();
    const { getByLabelText } = renderCard({ commerce: commerce() }, { onOpenCommerceProduct });

    fireEvent.press(getByLabelText("Aurora Desk Lamp, $49.00, from Northlight Studio"));
    expect(onOpenCommerceProduct).toHaveBeenCalledTimes(1);
    // The handler receives the overlay, so the *route* is what gets opened —
    // the card never names a screen.
    expect(onOpenCommerceProduct.mock.calls[0][0].cta.route).toBe("/pulse/marketplace/77");
  });

  it("sends the merchant tap somewhere else entirely", () => {
    const onOpenCommerceProduct = jest.fn();
    const onOpenCommerceSeller = jest.fn();
    const { getByText } = renderCard(
      { commerce: commerce() },
      { onOpenCommerceProduct, onOpenCommerceSeller }
    );

    fireEvent.press(getByText("Visit store"));
    expect(onOpenCommerceSeller).toHaveBeenCalledTimes(1);
    expect(onOpenCommerceSeller.mock.calls[0][0].seller.route).toBe("/pulse/merchant/10");
    // Publisher and merchant are different parties. One tap must not be both.
    expect(onOpenCommerceProduct).not.toHaveBeenCalled();
  });
});

describe("a PulseDrop Reel whose product was withdrawn", () => {
  it("names the state and shows no price", () => {
    const { getByText, queryByText } = renderCard(
      { commerce: withdrawn() },
      { onOpenCommerceProduct: jest.fn() }
    );

    // The video is content history and still plays; the commerce beside it is
    // current. The product stays, the price does not.
    expect(getByText("Aurora Desk Lamp")).toBeTruthy();
    expect(getByText("No longer available")).toBeTruthy();
    expect(queryByText("$49.00")).toBeNull();
  });

  it("offers no call to action", () => {
    const { queryByText } = renderCard({ commerce: withdrawn() }, { onOpenCommerceProduct: jest.fn() });
    // `/pulse/marketplace/<id>` 404s for a withdrawn listing, so a button here
    // would be a button that navigates to an error screen.
    expect(queryByText("View product")).toBeNull();
  });

  it("does not fire the product handler when pressed", () => {
    const onOpenCommerceProduct = jest.fn();
    const { getByText } = renderCard({ commerce: withdrawn() }, { onOpenCommerceProduct });
    fireEvent.press(getByText("Aurora Desk Lamp"));
    expect(onOpenCommerceProduct).not.toHaveBeenCalled();
  });
});

describe("the gate on the navigation handler", () => {
  it("draws nothing when the screen cannot open a product", () => {
    // A surface that renders the card without wiring navigation must not show a
    // button promising a destination it has no way to reach. This also fails if
    // someone removes the gate and the overlay starts rendering an inert CTA.
    const { queryByText } = renderCard({ commerce: commerce() }, {});
    expect(queryByText("View product")).toBeNull();
    expect(queryByText("Aurora Desk Lamp")).toBeNull();
  });
});

/**
 * The Signal surface.
 *
 * Mission 1's shoppable Signal is the half that shipped last, and it shipped as
 * a `commerce` payload the server attached and `PostCard` ignored, which is the
 * exact same shape of defect as the unwired Reel handler: the data was right,
 * the component was right, and nothing rendered.
 *
 * These assertions are a deliberate re-run of the Reel ones against the other
 * host. If one surface ever starts answering differently from the other, that
 * is the single-component argument failing and it should fail loudly here
 * rather than quietly in production.
 */
describe("a PulseDrop Signal", () => {
  it("leaves an ordinary post completely alone", () => {
    const { queryByText } = renderPost({}, { onOpenCommerceProduct: jest.fn() });
    expect(queryByText("$49.00")).toBeNull();
    expect(queryByText("View product")).toBeNull();
    expect(queryByText("Aurora Desk Lamp")).toBeNull();
  });

  it("shows the product, the price and a working call to action", () => {
    const { getByText } = renderPost(
      { commerce: commerce({ surface: "signal" }) } as Partial<PulsePost>,
      { onOpenCommerceProduct: jest.fn() }
    );
    expect(getByText("Aurora Desk Lamp")).toBeTruthy();
    expect(getByText("$49.00")).toBeTruthy();
    expect(getByText("Trending")).toBeTruthy();
    expect(getByText("View product")).toBeTruthy();
  });

  it("navigates to the product without also opening the post", () => {
    const onOpenCommerceProduct = jest.fn();
    const onOpen = jest.fn();
    const { getByLabelText } = renderPost(
      { commerce: commerce({ surface: "signal" }) } as Partial<PulsePost>,
      { onOpenCommerceProduct, onOpen }
    );

    // The whole card is a Pressable that opens the post, so this is a nested
    // tap target inside a larger one and a single gesture must not push two
    // screens.
    //
    // Today that is guaranteed by the responder system rather than by the
    // overlay's own `stopPropagation` — verified by deleting the shim and
    // watching this still pass. The assertion is kept anyway because it pins
    // the *property*, not the mechanism: the moment someone turns the overlay
    // into a View with `onTouchEnd`, or `PostCard` grows an outer
    // `onStartShouldSetResponderCapture`, the mechanism changes and this is
    // what we actually care about.
    fireEvent.press(getByLabelText("Aurora Desk Lamp, $49.00, from Northlight Studio"), {
      stopPropagation: () => undefined
    });
    expect(onOpenCommerceProduct).toHaveBeenCalledTimes(1);
    expect(onOpenCommerceProduct.mock.calls[0][0].cta.route).toBe("/pulse/marketplace/77");
    expect(onOpen).not.toHaveBeenCalled();
  });

  it("sends the merchant tap somewhere else entirely", () => {
    const onOpenCommerceProduct = jest.fn();
    const onOpenCommerceSeller = jest.fn();
    const { getByText } = renderPost(
      { commerce: commerce({ surface: "signal" }) } as Partial<PulsePost>,
      { onOpenCommerceProduct, onOpenCommerceSeller }
    );

    fireEvent.press(getByText("Visit store"), { stopPropagation: () => undefined });
    expect(onOpenCommerceSeller).toHaveBeenCalledTimes(1);
    expect(onOpenCommerceSeller.mock.calls[0][0].seller.route).toBe("/pulse/merchant/10");
    expect(onOpenCommerceProduct).not.toHaveBeenCalled();
  });

  it("names a withdrawn state and shows no price", () => {
    const overlay = withdrawn();
    overlay.surface = "signal";
    const { getByText, queryByText } = renderPost(
      { commerce: overlay } as Partial<PulsePost>,
      { onOpenCommerceProduct: jest.fn() }
    );
    expect(getByText("Aurora Desk Lamp")).toBeTruthy();
    expect(getByText("No longer available")).toBeTruthy();
    expect(queryByText("$49.00")).toBeNull();
    expect(queryByText("View product")).toBeNull();
  });

  it("draws nothing when the screen cannot open a product", () => {
    const { queryByText } = renderPost({ commerce: commerce({ surface: "signal" }) } as Partial<PulsePost>, {});
    expect(queryByText("View product")).toBeNull();
    expect(queryByText("Aurora Desk Lamp")).toBeNull();
  });
});

/**
 * A payload that says `reel` rendered inside a post card still renders, because
 * the surface prop the host passes wins over the one the payload carries. This
 * pins the precedence: the container decides the treatment, the payload only
 * supplies the default for a caller that did not say.
 */
/**
 * The product photo, which this card did not show for its entire life.
 *
 * `_product` in `services/pulsedrop/hydration.py` emits `cover_image_url`. The
 * type in `api/pulseCommerceOverlay.ts` declared `image_url`, the component read
 * that name, and no remapping existed anywhere in `mobile-native/src` — so
 * every PulseDrop product ever published rendered the grey placeholder beside
 * its own price.
 *
 * Nothing caught it, and the reason is the thing to fix rather than the bug:
 * the fixture above invented the field name *and* the value, so the suite was
 * green against a payload shape the server does not send. These tests assert
 * against a `testID`, and the Python side now checks every required field on
 * that type against a real `hydration.overlay()` payload — see
 * `tests/pulse_commerce/test_commerce_card_parity.py`.
 */
describe("the product photo", () => {
  it("renders the server's cover_image_url", () => {
    const { getByTestId, queryByTestId } = renderPost(
      { commerce: commerce({ surface: "signal" }) } as Partial<PulsePost>,
      { onOpenCommerceProduct: jest.fn() }
    );
    expect(getByTestId("commerce-overlay-thumb").props.source).toEqual({
      uri: "https://cdn.example/lamp.jpg"
    });
    expect(queryByTestId("commerce-overlay-thumb-empty")).toBeNull();
  });

  it("falls back to the legacy image_url when the current key is empty", () => {
    const payload = commerce({ surface: "signal" });
    const { getByTestId } = renderPost(
      {
        commerce: {
          ...payload,
          product: { ...payload.product, cover_image_url: "", image_url: "https://cdn.example/old.jpg" }
        }
      } as Partial<PulsePost>,
      { onOpenCommerceProduct: jest.fn() }
    );
    expect(getByTestId("commerce-overlay-thumb").props.source).toEqual({
      uri: "https://cdn.example/old.jpg"
    });
  });

  it("draws the placeholder and no broken image when the product has no photo", () => {
    const payload = commerce({ surface: "signal" });
    const { getByTestId, queryByTestId } = renderPost(
      {
        commerce: { ...payload, product: { ...payload.product, cover_image_url: "" } }
      } as Partial<PulsePost>,
      { onOpenCommerceProduct: jest.fn() }
    );
    expect(getByTestId("commerce-overlay-thumb-empty")).toBeTruthy();
    expect(queryByTestId("commerce-overlay-thumb")).toBeNull();
  });

  it("shows the photo on a Reel too", () => {
    const { getByTestId } = renderCard(
      { commerce: commerce() } as any,
      { onOpenCommerceProduct: jest.fn() }
    );
    expect(getByTestId("commerce-overlay-thumb").props.source).toEqual({
      uri: "https://cdn.example/lamp.jpg"
    });
  });
});

describe("surface precedence", () => {
  it("honours the host's surface over the payload's", () => {
    const { getByText } = renderPost(
      { commerce: commerce({ surface: "reel" }) } as Partial<PulsePost>,
      { onOpenCommerceProduct: jest.fn() }
    );
    expect(getByText("Aurora Desk Lamp")).toBeTruthy();
    expect(getByText("View product")).toBeTruthy();
  });
});
