/**
 * A listing with no price shows no price line. It does not show a sentence
 * standing in for one.
 *
 * Both marketplace surfaces used to fill an empty `price_label` with prose —
 * the grid card said "Price at checkout", the product page said "Price shown at
 * checkout". Neither is a fallback in the harmless sense. They are claims about
 * a checkout that neither screen can see: a dropship draft carries no price
 * anywhere in the system, so the sentence promised a number that never arrives.
 * Worse, the two screens disagreed with each other about the *same row*, so
 * which lie you got depended on how you reached the product.
 *
 * The server stopped inventing a price on the way out and the web grid stopped
 * inventing one on the way in (`tests/web_parity/` and
 * `tests/marketplace/test_seller_listing_edit.py` pin those two). This file is
 * the same rule on the third and fourth surface, and it exists because when the
 * screens were fixed all 31 existing native marketplace tests passed unchanged
 * — nothing in the suite could see the defect.
 *
 * Both halves are asserted deliberately:
 *
 *   - absent price renders nothing, AND
 *   - a price the seller actually set still renders.
 *
 * Without the second half, deleting the price line entirely would satisfy this
 * file. Each screen is also checked to have rendered *something* real, so
 * "invents no price" cannot be won by a screen that failed to mount.
 *
 * The phrases are written out as literals rather than imported from the screens.
 * A shared constant would let a future edit rename the phrase and keep this
 * suite green while the app still says it.
 */

import React from "react";
import { render, waitFor } from "@testing-library/react-native";
import { readdirSync, readFileSync, statSync } from "fs";
import { extname, join, relative } from "path";

jest.mock("react-native-safe-area-context", () => ({
  useSafeAreaInsets: () => ({ top: 0, bottom: 0, left: 0, right: 0 })
}));
jest.mock("@expo/vector-icons", () => ({ Ionicons: () => null }));

const mockSearchMarketplace = jest.fn();
const mockLoadCachedMarketplace = jest.fn();
jest.mock("../../api/marketplace", () => ({
  ...jest.requireActual("../../api/marketplace"),
  searchMarketplace: (...args: unknown[]) => mockSearchMarketplace(...args),
  loadCachedMarketplace: (...args: unknown[]) => mockLoadCachedMarketplace(...args)
}));
jest.mock("../../api/marketplaceCommerce", () => ({
  addToCart: jest.fn(async () => ({ lines: [], badgeCount: 0 })),
  fetchCart: jest.fn(async () => ({ lines: [], badgeCount: 0 }))
}));
jest.mock("../../core/eventSync", () => ({
  registerSyncInvalidation: jest.fn(() => () => undefined)
}));
jest.mock("../../navigation/BottomNavVisibility", () => ({
  useBottomNavSurface: () => ({ handlers: {}, contentPadding: {} })
}));
// Native playback has nothing to do with what a card prints.
jest.mock("../../components/NativeMediaViewer", () => ({
  mediaViewerItemFromPulseMedia: jest.fn(() => ({})),
  NativeMediaViewer: () => null
}));
jest.mock("@react-native-async-storage/async-storage", () => ({
  getItem: jest.fn(async () => null),
  setItem: jest.fn(async () => undefined)
}));

import { MarketplaceProductScreen } from "../MarketplaceProductScreen";
import { MarketplaceScreen } from "../MarketplaceScreen";

/**
 * Every sentence either screen has ever used in place of a price, plus the one
 * the web surfaces used. A screen that invents a *new* phrase is a new bug and
 * will need a new line here; that is the point of listing them rather than
 * pattern-matching, since a regex loose enough to catch the next invention
 * would also catch a legitimately priced row.
 */
const INVENTED = [
  "Price at checkout",
  "Price shown at checkout",
  "Request access",
  "Contact for price",
  "See price at checkout",
  // Added after this file's own header had recorded it as past tense. The
  // header said Buy Now "pushed the checkout screen, which promised 'Shown at
  // checkout'" — and the fix stopped at the buy gate, so the phrase was still
  // on the checkout screen, still rendering, just harder to reach. It survived
  // because this list was written from the two screens this file renders and
  // the checkout screen is a third.
  "Shown at checkout"
];

/**
 * Collapse the hits to plain strings before asserting.
 *
 * `expect(queryByText(phrase)).toBeNull()` is the obvious spelling and it is a
 * trap: on failure Jest serializes the matched host element, and a React Native
 * element tree is deep enough that building that diff exhausts the V8 heap —
 * the run dies with a stack dump instead of naming the phrase it found. A test
 * whose failure mode is a crash cannot tell anyone what broke, so what gets
 * compared here is an array of strings.
 */
function expectInventsNoPrice(queryByText: (text: string) => unknown, where: string) {
  const found = INVENTED.filter((phrase) => queryByText(phrase) !== null);
  expect({ where, invented: found }).toEqual({ where, invented: [] });
}

const UNPRICED = {
  id: 501,
  title: "Imported ceramic mug",
  price_label: "",
  currency: "USD",
  seller_user_id: 12,
  seller_name: "Dana R.",
  category: "Home",
  status: "active",
  approval_status: "approved",
  media: []
};

const PRICED = { ...UNPRICED, id: 502, title: "Oak dining table", price_label: "$220.00" };

/**
 * The same missing price, on a full shelf.
 *
 * `UNPRICED` above carries no `quantity`, so it reads as sold out and the buy
 * button is disabled for a reason that has nothing to do with the price. That
 * is why the gap below survived this file: every unpriced fixture it had was
 * also out of stock.
 */
const UNPRICED_IN_STOCK = {
  ...UNPRICED,
  id: 503,
  title: "Imported walnut stool",
  quantity: 4,
  inventory_state: "available",
  buyer_visible: true
};

/** The control: priced, in stock, and therefore genuinely on sale. */
const PRICED_IN_STOCK = { ...UNPRICED_IN_STOCK, id: 504, title: "Linen armchair", price_label: "$1,240.00" };

const navigation = { navigate: jest.fn(), goBack: jest.fn(), setOptions: jest.fn(), addListener: jest.fn(() => () => undefined) };

function renderProduct(listing: Record<string, unknown>) {
  return render(
    <MarketplaceProductScreen
      navigation={navigation as never}
      route={{ params: { listingId: listing.id, listing, title: listing.title } } as never}
    />
  );
}

async function renderGrid(listing: Record<string, unknown>) {
  mockSearchMarketplace.mockResolvedValue({ items: [listing] });
  const screen = render(<MarketplaceScreen navigation={navigation as never} route={{ params: {} } as never} />);
  // The card only exists once the search settles; asserting an absence before
  // then would pass against an empty grid.
  await waitFor(() => expect(screen.getByText(listing.title as string)).toBeTruthy());
  return screen;
}

beforeEach(() => {
  mockSearchMarketplace.mockReset();
  mockLoadCachedMarketplace.mockReset();
  mockLoadCachedMarketplace.mockResolvedValue([]);
  navigation.navigate.mockReset();
});

describe("the grid card", () => {
  it("prints no price line at all for a listing the seller never priced", async () => {
    const { queryByText, getByText } = await renderGrid(UNPRICED);
    // The card mounted — the absence below is a real absence, not an empty grid.
    expect(getByText("Imported ceramic mug")).toBeTruthy();
    expectInventsNoPrice(queryByText, "marketplace grid card");
  });

  it("still prints a price the seller did set", async () => {
    const { getByText } = await renderGrid(PRICED);
    expect(getByText("$220.00")).toBeTruthy();
  });
});

describe("the product page", () => {
  it("prints no price line at all for a listing the seller never priced", async () => {
    const { queryByText, getByText } = renderProduct(UNPRICED);
    expect(getByText("Imported ceramic mug")).toBeTruthy();
    expectInventsNoPrice(queryByText, "marketplace product page");
  });

  it("still prints a price the seller did set", async () => {
    const { getByText } = renderProduct(PRICED);
    expect(getByText("$220.00")).toBeTruthy();
  });
});

describe("the two surfaces agree about one product", () => {
  /**
   * The specific regression: the grid and the product page rendered *different*
   * invented sentences for the identical row, so tapping a card changed what
   * the app claimed about the price. Pinning both against the same listing
   * object is what makes a one-sided fix fail here.
   */
  it("shows the same price text on the card and on the page", async () => {
    const grid = await renderGrid(PRICED);
    const product = renderProduct(PRICED);
    expect(grid.getByText("$220.00")).toBeTruthy();
    expect(product.getByText("$220.00")).toBeTruthy();
  });

  it("shows no price text on either when there is none to show", async () => {
    const grid = await renderGrid(UNPRICED);
    const product = renderProduct(UNPRICED);
    expectInventsNoPrice(grid.queryByText, "marketplace grid card");
    expectInventsNoPrice(product.queryByText, "marketplace product page");
  });
});

/**
 * Not printing a price was only half the job. The other half is not offering to
 * take money for it.
 *
 * Both screens derived every disabled state from one boolean and rendered it as
 * "Sold out", so an unpriced listing on a full shelf showed an enabled "Add to
 * cart" — and the server answered 400 `ITEM_UNAVAILABLE`. Buy Now was worse: it
 * pushed the checkout screen, which promised "Shown at checkout", collected a
 * delivery address, and only then hit the same refusal.
 *
 * Asserted on the rendered screens rather than on the helper, because the helper
 * was already capable of saying so — nothing on the way to the button asked it.
 */
describe("an unpriced listing on a full shelf", () => {
  /** Phrases that would mean the screen mistook "unpriced" for "sold out". */
  const SOLD_OUT_PHRASES = ["SOLD OUT", "Sold out"];

  function soldOutPhrasesOn(queryByText: (text: string) => unknown, where: string) {
    return { where, saidSoldOut: SOLD_OUT_PHRASES.filter((p) => queryByText(p) !== null) };
  }

  it("does not offer the grid card's Add to cart", async () => {
    const { getAllByText, getByText, queryByText } = await renderGrid(UNPRICED_IN_STOCK);
    expect(getByText("Imported walnut stool")).toBeTruthy();
    // Twice, deliberately: the availability pill and the button itself. The
    // button used to read "Add to cart" beside a pill reading "4 available".
    expect(getAllByText("Not priced yet")).toHaveLength(2);
    // The shelf is full, so neither the scrim nor the button may say otherwise.
    expect(soldOutPhrasesOn(queryByText, "grid card")).toEqual({ where: "grid card", saidSoldOut: [] });
  });

  it("does not offer the product page's Add to cart or Buy now", () => {
    const { getAllByText, getByText, queryByText } = renderProduct(UNPRICED_IN_STOCK);
    expect(getByText("Imported walnut stool")).toBeTruthy();
    // Three sites on this screen — the pill, the "Availability" fact row, and
    // the buy button — and the point is that they agree. Leaving the fact row
    // on "4 available" beside a button that will not sell it is the same
    // contradiction in a smaller font.
    expect(getAllByText("Not priced yet")).toHaveLength(3);
    expect(soldOutPhrasesOn(queryByText, "product page")).toEqual({ where: "product page", saidSoldOut: [] });
  });

  it("still offers to sell a listing that does have a price", async () => {
    // Without this the whole block could be satisfied by a screen that never
    // offers anything to anyone.
    const grid = await renderGrid(PRICED_IN_STOCK);
    expect(grid.getByText("Add to cart")).toBeTruthy();
    const product = renderProduct(PRICED_IN_STOCK);
    expect(product.getByText("Add to cart")).toBeTruthy();
  });

  it("still says sold out when the shelf is actually empty", async () => {
    // "Not priced yet" must not become the new blanket answer either: UNPRICED
    // carries no quantity, and out of stock is the more specific fact.
    const grid = await renderGrid(UNPRICED);
    expect(grid.getAllByText("Sold out").length).toBeGreaterThan(0);
    expect(grid.queryByText("Not priced yet")).toBeNull();
  });
});

/**
 * The rule, asked of the whole tree instead of the two screens above.
 *
 * Everything before this point renders a surface and inspects it, which can
 * only ever cover surfaces someone remembered to add here. That is how
 * "Shown at checkout" survived: `bot.py` carried a comment enumerating the
 * surfaces that no longer invent a price — the serializer, the web grid, the
 * web product page, the client-side search card, the app's grid, the app's
 * product page — and the enumeration was accurate about all six. The checkout
 * screen was the seventh, and a list written by hand cannot notice the item
 * that was never on it.
 *
 * So the claim stops being an enumeration. These two tests read the source and
 * the catalogs, which means a surface written next year is covered on the day
 * it is written rather than on the day somebody remembers this file.
 */
describe("no surface invents a price, including the ones this file never renders", () => {
  const SRC = join(__dirname, "..", "..");

  /**
   * Comments are stripped before searching, not skipped line by line.
   *
   * Three of the files that must pass this scan discuss the banned phrases in
   * prose — this file's own header, the checkout screen's explanation of what
   * it removed, and the grid card's note about what it stopped saying. A
   * detector that cannot tell a quoted phrase in a comment from a rendered one
   * would either fail on documentation or force the documentation to be
   * deleted, and the documentation is the part that explains why the rule
   * exists. Block form is handled too, because JSX writes its comments as
   * `{/* … *␣/}` and a line-based check reads straight past them.
   */
  function withoutComments(source: string) {
    return source
      .replace(/\/\*[\s\S]*?\*\//g, " ")
      .replace(/(^|[^:])\/\/.*$/gm, "$1");
  }

  function sourceFiles(dir: string, out: string[] = []): string[] {
    for (const name of readdirSync(dir)) {
      const full = join(dir, name);
      if (statSync(full).isDirectory()) {
        if (name === "node_modules" || name === "__tests__") continue;
        sourceFiles(full, out);
        continue;
      }
      if ([".ts", ".tsx"].includes(extname(name)) && !/\.test\.tsx?$/.test(name)) out.push(full);
    }
    return out;
  }

  const files = sourceFiles(SRC);

  /**
   * Two ways for the scan below to pass without looking at anything, both
   * closed here: an empty file list, and a comment stripper that eats the code
   * along with the comments.
   *
   * The second is the one worth spelling out. `withoutComments` is the only
   * moving part in the scan, and a regression there fails *open* — every phrase
   * goes missing, every test stays green, and the guard reports success while
   * guarding nothing. So a string that genuinely ships in rendered copy is
   * looked for and must be found. It is deliberately one of the sentences the
   * checkout screen prints, which puts the control in the same file the scan
   * most needs to read.
   */
  it("is reading the source it is guarding", () => {
    expect(files.length).toBeGreaterThan(200);

    const CONTROL = "No card or Stripe charge will start.";
    const survives = files.filter((file) => withoutComments(readFileSync(file, "utf8")).includes(CONTROL));
    expect(survives.map((file) => relative(SRC, file).split("\\").join("/"))).toContain(
      "screens/MarketplaceCheckoutScreen.tsx"
    );
  });

  it("does not ship any of these phrases in rendered copy", () => {
    const found: string[] = [];
    for (const file of files) {
      const source = withoutComments(readFileSync(file, "utf8"));
      const lines = source.split("\n");
      lines.forEach((line, index) => {
        for (const phrase of INVENTED) {
          if (!line.includes(phrase)) continue;
          found.push(`${relative(SRC, file).split("\\").join("/")}:${index + 1} says "${phrase}"`);
        }
      });
    }
    expect(found).toEqual([]);
  });

  /**
   * The catalogs, by key name rather than by phrase.
   *
   * `priceFallback` shipped in eleven languages, read by no code, one `t()`
   * call away from putting "Price at checkout" back on a card — and the ten
   * translations of it could not be caught by searching for English. What is
   * language-independent is the key: a name that says "the price is missing, so
   * print this instead" describes a thing this product does not do, whatever
   * language the value is in.
   *
   * A field placeholder is deliberately NOT caught here, though the first draft
   * of this rule caught twenty-two of them. `priceLabelPlaceholder` ("Price
   * label") and `pricePlaceholder` ("0.00") sit inside seller-facing inputs and
   * tell a seller what to type into an empty box; they are never rendered as a
   * price to a buyer. Widening the rule to cover them would have forced an
   * exemption list, and an exemption list is where a rule starts negotiating.
   * The rule is narrow so that every hit is a real one.
   */
  it("ships no catalog string whose job is to stand in for a missing price", () => {
    const catalogs = join(SRC, "i18n", "catalogs");
    const offenders: string[] = [];

    function walk(node: unknown, path: string, locale: string) {
      if (!node || typeof node !== "object") return;
      for (const [key, value] of Object.entries(node as Record<string, unknown>)) {
        const here = path ? `${path}.${key}` : key;
        const lowered = key.toLowerCase();
        if (lowered.includes("price") && lowered.includes("fallback")) {
          if (typeof value === "string") offenders.push(`${locale}: ${here} = ${JSON.stringify(value)}`);
        }
        walk(value, here, locale);
      }
    }

    for (const locale of readdirSync(catalogs)) {
      const dir = join(catalogs, locale);
      if (!statSync(dir).isDirectory()) continue;
      for (const name of readdirSync(dir)) {
        if (extname(name) !== ".json") continue;
        walk(JSON.parse(readFileSync(join(dir, name), "utf8")), "", locale);
      }
    }

    expect(offenders).toEqual([]);
  });
});
