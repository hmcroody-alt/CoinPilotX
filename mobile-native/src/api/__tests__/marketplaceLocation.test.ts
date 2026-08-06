/**
 * "Just listed near you", over listings with no geography on them.
 *
 * The feed was sorted by recency and nothing else, and the strip directly
 * above the heading read "Location not set — showing all listings" — the
 * screen contradicted itself within one scroll position. The contradictory
 * fallback (and the flag that kept it alive) is deleted; `marketplaceLocation`
 * is now the only source for the heading, the strip, the footer and the empty
 * state, so they derive from one `city` input and cannot disagree.
 *
 * The city is a stored, self-reported preference (`loadMarketplaceCity`), not
 * a geo lookup: listings still carry no coordinates and `distanceMeters` is
 * still null — which `MARKETPLACE_MOCK_DATA_GAPS` continues to record. Setting
 * a city changes what the screen claims, not what the feed contains.
 */
import AsyncStorage from "@react-native-async-storage/async-storage";
import {
  MARKETPLACE_MOCK_DATA_GAPS,
  clearMarketplaceCity,
  loadMarketplaceCity,
  marketplaceLocation,
  saveMarketplaceCity
} from "../marketplaceScreen";

describe("with no location known", () => {
  const place = marketplaceLocation();

  /** The regression, stated as a property rather than as one string. */
  it("makes no claim about proximity anywhere on the screen", () => {
    const claims = [
      place.feedTitle,
      place.stripText,
      place.moreLabel,
      place.empty.title
    ].join(" ");
    expect(claims).not.toMatch(/near you/i);
    expect(claims).not.toMatch(/nearby item/i);
    expect(claims).not.toMatch(/around you/i);
    expect(claims).not.toMatch(/\bdistance\b/i);
    expect(claims).not.toMatch(/\bmiles?\b|\bkm\b/i);
    // The one permitted mention: the empty state may invite the reader to SET
    // a location to find nearby items — an offer, not a claim. So the body is
    // held to a narrower bar: it may not assert proximity as fact.
    expect(place.empty.body).not.toMatch(/near you/i);
    expect(place.empty.body).not.toMatch(/around you/i);
    expect(place.empty.body).not.toMatch(/\bdistance\b/i);
    expect(place.empty.body).not.toMatch(/\bmiles?\b|\bkm\b/i);
    expect(place.feedTitle).not.toMatch(/near/i);
    expect(place.stripText).not.toMatch(/near/i);
    expect(place.moreLabel).not.toMatch(/near/i);
  });

  it("heads the feed with what it can actually promise", () => {
    expect(place.feedTitle).toBe("Recently listed");
    expect(place.known).toBe(false);
  });

  it("labels the strip with the plain state, over a control that can fix it", () => {
    // "Location not set" was dishonest only while nothing could set one. The
    // strip now opens the location sheet, so leading with the absence is
    // exactly right: it names the thing the tap will change.
    expect(place.stripText).toBe("Location not set");
    expect(place.stripAction).toEqual({ key: "set_location", label: "Set location" });
  });

  it("never claims to be 'showing all listings' as if that were a choice", () => {
    expect(place.stripText).not.toMatch(/all listings/i);
    expect(place.stripText).not.toMatch(/every listing/i);
  });

  it("drops the claim from the footer too, not only the heading", () => {
    expect(place.moreLabel).toBe("Show more");
  });
});

describe("when a location becomes known", () => {
  const place = marketplaceLocation({ city: "Bristol" });

  it("brings the proximity claim back across the whole feed at once", () => {
    expect(place.known).toBe(true);
    expect(place.feedTitle).toBe("Just listed near you");
    expect(place.stripText).toBe("Showing listings near Bristol");
    expect(place.moreLabel).toBe("Show more nearby");
  });

  it("names the actual place in the strip, so the claim is checkable", () => {
    // "near you" in the heading is anchored by the strip directly above it,
    // which names the city the reader typed and offers to change it.
    expect(place.stripText).toContain("Bristol");
    expect(place.stripAction).toEqual({ key: "edit_location", label: "Change location" });
  });

  it("treats a blank or whitespace city as no city at all", () => {
    for (const city of ["", "   ", null, undefined]) {
      const blank = marketplaceLocation({ city });
      expect(blank.known).toBe(false);
      expect(blank.feedTitle).toBe("Recently listed");
    }
  });
});

describe("the empty state", () => {
  it("offers Set location when nothing is set and nothing is filtered", () => {
    const place = marketplaceLocation({ categoryFiltered: false });
    expect(place.empty.title).toBe("No listings available right now.");
    expect(place.empty.actions.map((a) => a.key)).toEqual(["set_location"]);
    expect(place.empty.action).toEqual({ key: "set_location", label: "Set location" });
  });

  it("leads with the filter when a filter is what narrowed the list", () => {
    const place = marketplaceLocation({ categoryFiltered: true });
    expect(place.empty.title).toMatch(/category/i);
    expect(place.empty.actions.map((a) => a.key)).toEqual(["clear_category", "set_location"]);
    expect(place.empty.action?.key).toBe("clear_category");
  });

  it("offers Change location when a city is set and the feed is empty", () => {
    const place = marketplaceLocation({ city: "Bristol", categoryFiltered: false });
    expect(place.empty.title).toBe("Nothing nearby right now.");
    expect(place.empty.actions.map((a) => a.key)).toEqual(["edit_location"]);
  });

  it("never suggests widening a radius that does not exist", () => {
    for (const city of [null, "Bristol"]) {
      for (const filtered of [true, false]) {
        const place = marketplaceLocation({ city, categoryFiltered: filtered });
        const copy = `${place.empty.title} ${place.empty.body}`;
        expect(copy).not.toMatch(/radius/i);
        expect(copy).not.toMatch(/further away/i);
        expect(copy).not.toMatch(/wider/i);
      }
    }
  });

  it("writes both lines as sentences and neither as a dash", () => {
    for (const city of [null, "Bristol"]) {
      for (const filtered of [true, false]) {
        const place = marketplaceLocation({ city, categoryFiltered: filtered });
        expect(place.empty.title.length).toBeGreaterThan(0);
        expect(place.empty.body.length).toBeGreaterThan(0);
        expect(`${place.empty.title}${place.empty.body}`).not.toContain("—");
      }
    }
  });
});

describe("the stored city preference", () => {
  beforeEach(() => AsyncStorage.clear());

  it("round-trips a city, trimmed", async () => {
    await saveMarketplaceCity("  New York  ");
    expect(await loadMarketplaceCity()).toBe("New York");
  });

  it("reads null when nothing was ever set", async () => {
    expect(await loadMarketplaceCity()).toBeNull();
  });

  it("clears on blank input and on the explicit clear", async () => {
    await saveMarketplaceCity("Bristol");
    await saveMarketplaceCity("   ");
    expect(await loadMarketplaceCity()).toBeNull();

    await saveMarketplaceCity("Bristol");
    await clearMarketplaceCity();
    expect(await loadMarketplaceCity()).toBeNull();
  });
});

describe("the recorded gap", () => {
  /**
   * The city is a claim, not data: listings still carry no coordinates, so the
   * ledger must keep saying so — otherwise a later reader assumes the "near"
   * wording is backed by geo that does not exist.
   */
  it("records that distance has no source, with what would close it", () => {
    const entry = MARKETPLACE_MOCK_DATA_GAPS.find((gap) =>
      `${gap.field} ${gap.needs}`.toLowerCase().includes("distance")
    );
    expect(entry).toBeTruthy();
    expect(entry!.needs.length).toBeGreaterThan(20);
  });
});
