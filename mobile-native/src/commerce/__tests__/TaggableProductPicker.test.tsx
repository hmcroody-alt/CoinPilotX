/**
 * The picker, and the three things it must never do to a seller.
 *
 *   - **Render an error as an empty store.** The endpoint answers 500 rather
 *     than `200 []` when it cannot read the catalogue, and the client lets that
 *     throw, specifically so this component can tell the difference. If the two
 *     branches ever collapse, every one of those upstream decisions becomes
 *     decoration. So the two states are asserted *mutually exclusive*, in both
 *     directions — the error case must not show the empty copy, and the empty
 *     case must not show the retry. Asserting only that each state renders its
 *     own text would stay green if both rendered at once.
 *
 *   - **Hide a listing that will not serve.** Filtering the unservable ones out
 *     is the intuitive design and it is the bug: a product missing from your own
 *     picker teaches you nothing, while a product labelled "Needs a cover photo"
 *     tells you what to fix. The endpoint returns every listing with a
 *     `blocked_reason` for exactly this, so the picker must render them.
 *
 *   - **Refuse to tag one.** `eligibility.gate()` runs per serve request on the
 *     tagged listing — verified in `pool.py`, where the tagged source is a
 *     clause on a query whose `WHERE` is `candidate_sql()` — so the gate is never
 *     frozen at tag time. A tag on a listing that is blocked today starts
 *     serving the moment the listing is fixed. Disabling the row would throw
 *     away a true statement the seller is entitled to make about their own post,
 *     to protect them from a condition that is temporary and theirs to clear.
 *
 * The cap is asserted from the *response*, never against a literal 5. A
 * hardcoded client copy of `tagging.MAX_TAGGED_PER_CONTENT` presents as a seller
 * being promised five tags when three will be stored, and a test written against
 * the same literal would defend the divergence rather than catch it.
 */
import { act, fireEvent, render, waitFor } from "@testing-library/react-native";
import { TaggableProductPicker } from "../TaggableProductPicker";
import { fetchTaggableProducts } from "../../api/taggableProducts";
import type { TaggableProduct, TaggableProductsResult } from "../../api/taggableProducts";

jest.mock("../../api/taggableProducts", () => ({ fetchTaggableProducts: jest.fn() }));

const fetchMock = fetchTaggableProducts as jest.MockedFunction<typeof fetchTaggableProducts>;

function product(listingId: number, blockedReason: TaggableProduct["blockedReason"] = ""): TaggableProduct {
  return {
    listingId,
    product: {
      listingId,
      title: `Listing ${listingId}`,
      priceLabel: "$28.00",
      coverImageUrl: blockedReason === "no_cover_image" ? "" : "https://cdn.example/x.jpg",
      sellerUserId: 1,
      sellerStoreName: "Kiln",
      category: "home",
      rating: 4.5,
      ratingCount: 12
    },
    serves: blockedReason === "",
    blockedReason
  };
}

function result(products: TaggableProduct[], maxPerContent = 5): TaggableProductsResult {
  return { products, maxPerContent, requestLimit: 20 };
}

function renderPicker(overrides: Partial<React.ComponentProps<typeof TaggableProductPicker>> = {}) {
  const onChange = jest.fn();
  const onClose = jest.fn();
  const view = render(
    <TaggableProductPicker visible selectedIds={[]} onChange={onChange} onClose={onClose} {...overrides} />
  );
  return { ...view, onChange, onClose };
}

beforeEach(() => {
  fetchMock.mockReset();
});

describe("error and empty are different states", () => {
  it("offers a retry on failure and does not claim the store is empty", async () => {
    fetchMock.mockRejectedValueOnce(new Error("TAGGABLE_PRODUCTS_UNAVAILABLE"));
    const view = renderPicker();
    await waitFor(() => expect(view.getByTestId("taggable-picker-error")).toBeTruthy());
    // The mutual exclusion, not just the presence of the error branch.
    expect(view.queryByTestId("taggable-picker-empty")).toBeNull();
    expect(view.queryByTestId("taggable-picker-list")).toBeNull();
  });

  it("says the store is empty without offering a retry when it really is", async () => {
    fetchMock.mockResolvedValueOnce(result([]));
    const view = renderPicker();
    await waitFor(() => expect(view.getByTestId("taggable-picker-empty")).toBeTruthy());
    expect(view.queryByTestId("taggable-picker-error")).toBeNull();
    expect(view.queryByTestId("taggable-picker-retry")).toBeNull();
  });

  it("recovers from an error without remounting", async () => {
    // A retry that cannot succeed is a dead end wearing a button.
    fetchMock.mockRejectedValueOnce(new Error("nope")).mockResolvedValueOnce(result([product(43)]));
    const view = renderPicker();
    await waitFor(() => expect(view.getByTestId("taggable-picker-retry")).toBeTruthy());
    await act(async () => {
      fireEvent.press(view.getByTestId("taggable-picker-retry"));
    });
    await waitFor(() => expect(view.getByTestId("taggable-picker-row-43")).toBeTruthy());
    expect(view.queryByTestId("taggable-picker-error")).toBeNull();
  });

  it("does not fetch at all until it is opened", () => {
    // The sheet is mounted only while open, so a creator who never taps the
    // button never asks the server for their catalogue.
    render(<TaggableProductPicker visible={false} selectedIds={[]} onChange={jest.fn()} onClose={jest.fn()} />);
    expect(fetchMock).not.toHaveBeenCalled();
  });
});

describe("a listing that will not serve is shown, explained, and still taggable", () => {
  it("renders the blocked listing rather than filtering it out", async () => {
    fetchMock.mockResolvedValueOnce(result([product(43), product(44, "no_cover_image")]));
    const view = renderPicker();
    await waitFor(() => expect(view.getByTestId("taggable-picker-row-44")).toBeTruthy());
    expect(view.getByTestId("taggable-picker-blocked-44")).toBeTruthy();
    // And the servable one carries no warning, or the badge means nothing.
    expect(view.queryByTestId("taggable-picker-blocked-43")).toBeNull();
  });

  it("selects a blocked listing when pressed", async () => {
    fetchMock.mockResolvedValueOnce(result([product(44, "listing_risk")]));
    const view = renderPicker();
    await waitFor(() => expect(view.getByTestId("taggable-picker-row-44")).toBeTruthy());
    fireEvent.press(view.getByTestId("taggable-picker-row-44"));
    expect(view.onChange).toHaveBeenCalledWith([44]);
  });

  it("names the reason rather than printing the wire code", async () => {
    fetchMock.mockResolvedValueOnce(result([product(44, "no_cover_image")]));
    const view = renderPicker();
    await waitFor(() => expect(view.getByTestId("taggable-picker-blocked-44")).toBeTruthy());
    expect(view.getByText("Needs a cover photo")).toBeTruthy();
    expect(view.queryByText("no_cover_image")).toBeNull();
  });

  it("falls back to a readable sentence for a code it has never heard of", async () => {
    fetchMock.mockResolvedValueOnce(result([product(44, "unknown_reason")]));
    const view = renderPicker();
    await waitFor(() => expect(view.getByTestId("taggable-picker-blocked-44")).toBeTruthy());
    expect(view.getByText("Cannot be shown right now")).toBeTruthy();
  });
});

describe("selection", () => {
  it("deselects a listing that is already selected", async () => {
    fetchMock.mockResolvedValueOnce(result([product(43)]));
    const view = renderPicker({ selectedIds: [43] });
    await waitFor(() => expect(view.getByTestId("taggable-picker-row-43")).toBeTruthy());
    fireEvent.press(view.getByTestId("taggable-picker-row-43"));
    expect(view.onChange).toHaveBeenCalledWith([]);
  });

  it("stops accepting new selections at the server's cap", async () => {
    // Cap of 2 from the response, deliberately not the production 5: a test
    // written against the literal would pass even if the component ignored the
    // response and hardcoded its own number.
    fetchMock.mockResolvedValueOnce(result([product(43), product(44), product(45)], 2));
    const view = renderPicker({ selectedIds: [43, 44] });
    await waitFor(() => expect(view.getByTestId("taggable-picker-row-45")).toBeTruthy());
    fireEvent.press(view.getByTestId("taggable-picker-row-45"));
    expect(view.onChange).not.toHaveBeenCalled();
  });

  it("still allows undoing a selection once at the cap", async () => {
    // Otherwise the seller's fifth choice is permanent until they discard the
    // draft, which is a trap rather than a limit.
    fetchMock.mockResolvedValueOnce(result([product(43), product(44)], 2));
    const view = renderPicker({ selectedIds: [43, 44] });
    await waitFor(() => expect(view.getByTestId("taggable-picker-row-44")).toBeTruthy());
    fireEvent.press(view.getByTestId("taggable-picker-row-44"));
    expect(view.onChange).toHaveBeenCalledWith([43]);
  });

  it("counts against the cap the server sent, not a literal", async () => {
    fetchMock.mockResolvedValueOnce(result([product(43)], 3));
    const view = renderPicker({ selectedIds: [43] });
    await waitFor(() => expect(view.getByTestId("taggable-picker-count")).toBeTruthy());
    expect(view.getByTestId("taggable-picker-count").props.children).toContain("3");
  });
});
