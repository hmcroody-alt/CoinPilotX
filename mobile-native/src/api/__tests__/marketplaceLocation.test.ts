/**
 * "Just listed near you", over listings with no geography on them.
 *
 * The feed was sorted by recency and nothing else. Three words of that heading
 * were false — and the strip directly beneath it read "Location not set", so
 * the screen contradicted itself within one scroll position.
 *
 * WHY THERE IS NO "SET YOUR LOCATION" BUTTON
 * ------------------------------------------
 * The brief asks for one. Nothing in this app can honour it: there is no
 * location library in the dependency list, no city or country on the account or
 * the profile, no coordinates on a listing, and `distanceMeters` is hard-coded
 * `null` — which `MARKETPLACE_MOCK_DATA_GAPS` already records. A button that
 * opens nothing is exactly the dead control this tier exists to delete, so the
 * false claim is dropped and the empty state gets an action that really works.
 *
 * The tests below pin both halves of that decision: that the claim is gone, and
 * that the day geo arrives the claim comes back on its own — because `city` is
 * an input, not a constant, and the whole heading/strip/footer set is derived
 * from it in one place rather than written out three times.
 */
import {
  MARKETPLACE_LOCATION_FLAG,
  MARKETPLACE_MOCK_DATA_GAPS,
  marketplaceLocation,
  marketplaceLocationHonestyEnabled
} from "../marketplaceScreen";

describe("the flag", () => {
  it('is off unless the build opts in, and accepts every spelling of "on"', () => {
    // The accepted spellings are the shared set in core/envFlag.ts, not this
    // module's own idea of one. This flag shipped taking the literal "1" alone
    // while flags on adjacent screens also took "true" — so a build that set it
    // to "true" got a silent no-op. Both work now; unset is still off.
    const original = process.env[MARKETPLACE_LOCATION_FLAG];
    try {
      for (const value of ["", " ", "0", "false", "off", "no", "2"]) {
        process.env[MARKETPLACE_LOCATION_FLAG] = value;
        expect(marketplaceLocationHonestyEnabled()).toBe(false);
      }
      for (const value of ["1", "true", "on", "yes", " TRUE ", "Yes"]) {
        process.env[MARKETPLACE_LOCATION_FLAG] = value;
        expect(marketplaceLocationHonestyEnabled()).toBe(true);
      }
      delete process.env[MARKETPLACE_LOCATION_FLAG];
      expect(marketplaceLocationHonestyEnabled()).toBe(false);
    } finally {
      if (original === undefined) delete process.env[MARKETPLACE_LOCATION_FLAG];
      else process.env[MARKETPLACE_LOCATION_FLAG] = original;
    }
  });
});

describe("with no location known", () => {
  const place = marketplaceLocation();

  /**
   * The regression, stated as a property rather than as one string.
   *
   * `unavailableReason` is excluded deliberately: it is the only line allowed
   * to say "distance", because saying it is how it denies it. Every line that
   * makes a claim is checked; the line that withdraws one is not.
   */
  it("makes no claim about proximity anywhere on the screen", () => {
    const claims = [
      place.feedTitle,
      place.stripText,
      place.moreLabel,
      place.empty.title,
      place.empty.body
    ].join(" ");
    expect(claims).not.toMatch(/near/i);
    expect(claims).not.toMatch(/nearby/i);
    expect(claims).not.toMatch(/around you/i);
    expect(claims).not.toMatch(/\bdistance\b/i);
    expect(claims).not.toMatch(/\bmiles?\b|\bkm\b/i);
  });

  it("heads the feed with what it can actually promise", () => {
    expect(place.feedTitle).toBe("Just listed");
    expect(place.known).toBe(false);
  });

  it("says what the list is showing instead of what is missing from it", () => {
    // The old strip led with the absence — "Location not set" — which reads as
    // a fault the seller should fix, over a control that cannot fix it.
    expect(place.stripText).toBe("Showing every listing");
    expect(place.stripText).not.toMatch(/not set/i);
  });

  /**
   * The strip is an unavailable row with a stated reason rather than a line
   * that looks tappable. Silence there would leave the reader assuming the
   * feature is broken rather than absent.
   */
  it("explains why distance is not on offer", () => {
    expect(place.unavailableReason).toBeTruthy();
    expect(String(place.unavailableReason)).toMatch(/isn't part of the app yet/i);
  });

  it("drops the claim from the footer too, not only the heading", () => {
    expect(place.moreLabel).toBe("Show more");
  });
});

describe("when a location becomes known", () => {
  const place = marketplaceLocation({ city: "Bristol" });

  /**
   * The reason this is a derivation and not three edited strings. Nothing
   * supplies a city today; when something does, the heading, the strip and the
   * footer move together, and no screen has to be found and changed.
   */
  it("brings the proximity claim back across the whole feed at once", () => {
    expect(place.known).toBe(true);
    expect(place.feedTitle).toBe("Just listed near Bristol");
    expect(place.stripText).toBe("Showing listings near Bristol");
    expect(place.moreLabel).toBe("Show more nearby");
  });

  it("drops the unavailable reason, since the thing is now available", () => {
    expect(place.unavailableReason).toBeNull();
  });

  it("treats a blank or whitespace city as no city at all", () => {
    for (const city of ["", "   ", null, undefined]) {
      const blank = marketplaceLocation({ city });
      expect(blank.known).toBe(false);
      expect(blank.feedTitle).toBe("Just listed");
    }
  });

  it("names the actual place rather than saying 'near you'", () => {
    // "near you" is unfalsifiable; "near Bristol" is something the reader can
    // check against and correct.
    expect(place.feedTitle).not.toMatch(/near you/i);
    expect(place.feedTitle).toContain("Bristol");
  });
});

describe("the empty state", () => {
  /**
   * The brief calls the old empty state inert, and it was: "Nothing nearby
   * right now. Try another category" with no way to try another category. The
   * replacement offers the one filter that really exists.
   */
  it("offers a working action when a filter is what emptied the list", () => {
    const filtered = marketplaceLocation({ categoryFiltered: true });
    expect(filtered.empty.action).toEqual({ key: "clear_category", label: "Show all categories" });
    expect(filtered.empty.title).toMatch(/category/i);
  });

  /**
   * And offers none when there is nothing to clear. Rendering a button in both
   * cases would put us back where we started, with a control that does nothing
   * — just a different nothing.
   */
  it("offers no action when no filter is narrowing anything", () => {
    const unfiltered = marketplaceLocation({ categoryFiltered: false });
    expect(unfiltered.empty.action).toBeNull();
    expect(unfiltered.empty.title).toMatch(/nothing has been listed yet/i);
  });

  it("distinguishes an empty category from an empty marketplace", () => {
    const filtered = marketplaceLocation({ categoryFiltered: true });
    const unfiltered = marketplaceLocation({ categoryFiltered: false });
    expect(filtered.empty.title).not.toBe(unfiltered.empty.title);
    expect(filtered.empty.body).not.toBe(unfiltered.empty.body);
  });

  it("never suggests widening a radius that does not exist", () => {
    for (const filtered of [true, false]) {
      const place = marketplaceLocation({ categoryFiltered: filtered });
      const copy = `${place.empty.title} ${place.empty.body}`;
      expect(copy).not.toMatch(/radius/i);
      expect(copy).not.toMatch(/further away/i);
      expect(copy).not.toMatch(/wider/i);
    }
  });

  it("writes both lines as sentences and neither as a dash", () => {
    for (const filtered of [true, false]) {
      const place = marketplaceLocation({ categoryFiltered: filtered });
      expect(place.empty.title.length).toBeGreaterThan(0);
      expect(place.empty.body.length).toBeGreaterThan(0);
      expect(`${place.empty.title}${place.empty.body}`).not.toContain("—");
    }
  });
});

describe("the recorded gap", () => {
  /**
   * The claim was dropped rather than implemented, so the ledger has to carry
   * the reason. Without this, a later reader sees an unexplained absence and
   * has no way to know it was a decision.
   */
  it("records that location has no source, with what would close it", () => {
    const entry = MARKETPLACE_MOCK_DATA_GAPS.find((gap) =>
      `${gap.field} ${gap.needs}`.toLowerCase().includes("distance")
    );
    expect(entry).toBeTruthy();
    expect(entry!.needs.length).toBeGreaterThan(20);
  });
});
