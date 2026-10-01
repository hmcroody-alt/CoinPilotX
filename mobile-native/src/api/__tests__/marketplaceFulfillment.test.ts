import {
  firstMissingFulfillmentField,
  fulfillmentDestinationSummary,
  fulfillmentFields,
  fulfillmentNeedsAddress,
  groupFulfillmentKind,
  resolveFulfillmentChoice,
  resolveFulfillmentKind,
  ticketOptions,
  type MarketplaceFulfillmentKind
} from "../marketplaceFulfillment";
import type { MarketplaceListing } from "../marketplace";

const listing = (over: Partial<MarketplaceListing>): MarketplaceListing =>
  ({ id: 1, title: "Ball", ...over }) as MarketplaceListing;

const keys = (kind: MarketplaceFulfillmentKind, tickets: string[] = []) =>
  fulfillmentFields(kind, tickets).map((field) => field.key);

describe("the order type the buyer is checking out", () => {
  it("reads a booking as a booking instead of letting it fall through to shipping", () => {
    // The defect this module exists for: an unrecognised type became "shipping"
    // and the buyer was asked for a delivery address for a video call.
    expect(resolveFulfillmentKind(listing({ listing_type: "booking", delivery_type: "booking" })))
      .toBe("booking_remote");
    expect(resolveFulfillmentKind(listing({ listing_type: "booking", listing_metadata: { meeting_mode: "in_person" } })))
      .toBe("booking_in_person");
  });

  it("separates a service by where it happens", () => {
    expect(resolveFulfillmentKind(listing({ listing_type: "service" }))).toBe("service_remote");
    expect(resolveFulfillmentKind(listing({ listing_type: "service", listing_metadata: { service_location: "in_person" } })))
      .toBe("service_in_person");
    expect(resolveFulfillmentKind(listing({ listing_type: "service", listing_metadata: { service_location: "both" } })))
      .toBe("service_choice");
  });

  it("separates an event by its venue", () => {
    expect(resolveFulfillmentKind(listing({ listing_type: "event" }))).toBe("event_online");
    expect(resolveFulfillmentKind(listing({ listing_type: "event", listing_metadata: { venue_mode: "in_person" } })))
      .toBe("event_in_person");
  });

  it("keeps the physical lanes reading the same as before", () => {
    expect(resolveFulfillmentKind(listing({ listing_type: "digital" }))).toBe("digital");
    expect(resolveFulfillmentKind(listing({ delivery_type: "pickup" }))).toBe("pickup");
    expect(resolveFulfillmentKind(listing({ delivery_type: "shipping" }))).toBe("shipping");
    expect(resolveFulfillmentKind(listing({ delivery_type: "both" }))).toBe("shipping_or_pickup");
    expect(resolveFulfillmentKind(listing({ listing_metadata: { delivery_options: "pickup" } }))).toBe("pickup");
  });
});

describe("settling a kind the seller left open", () => {
  it("takes the lane the buyer picked", () => {
    expect(resolveFulfillmentChoice("shipping_or_pickup", "pickup")).toBe("pickup");
    expect(resolveFulfillmentChoice("shipping_or_pickup", "shipping")).toBe("shipping");
    expect(resolveFulfillmentChoice("service_choice", "remote")).toBe("service_remote");
    expect(resolveFulfillmentChoice("service_choice", "in_person")).toBe("service_in_person");
  });

  it("refuses to guess when nothing was picked", () => {
    expect(resolveFulfillmentChoice("shipping_or_pickup", "")).toBeNull();
    expect(resolveFulfillmentChoice("service_choice", "somewhere")).toBeNull();
  });

  it("leaves a decided kind alone whatever is passed with it", () => {
    expect(resolveFulfillmentChoice("digital", "pickup")).toBe("digital");
    expect(resolveFulfillmentChoice("booking_remote", "")).toBe("booking_remote");
  });
});

describe("what each order type asks for", () => {
  it("asks a digital buyer for nothing at all", () => {
    expect(keys("digital")).toEqual([]);
  });

  it.each<MarketplaceFulfillmentKind>([
    "digital",
    "pickup",
    "service_remote",
    "booking_remote",
    "event_online",
    "event_in_person"
  ])("never asks for an address on a %s order", (kind) => {
    expect(fulfillmentNeedsAddress(kind)).toBe(false);
    expect(keys(kind, ["General"]).some((key) => key.startsWith("address_"))).toBe(false);
  });

  it("asks a shipping buyer for contact and address before payment", () => {
    expect(keys("shipping")).toEqual([
      "contact_name",
      "contact_phone",
      "address_line1",
      "address_line2",
      "address_city",
      "address_region",
      "address_postal_code",
      "address_country",
      "delivery_notes"
    ]);
  });

  it("asks a service buyer when it happens, not where to post it", () => {
    expect(keys("service_remote")).toEqual([
      "contact_name",
      "contact_phone",
      "scheduled_date",
      "scheduled_time",
      "timezone",
      "notes"
    ]);
  });

  it("asks an in-person service for both the time and the place", () => {
    const fields = keys("service_in_person");
    expect(fields).toContain("scheduled_date");
    expect(fields).toContain("address_line1");
  });

  it("asks a pickup buyer for a phone number the seller can reach", () => {
    const phone = fulfillmentFields("pickup").find((field) => field.key === "contact_phone");
    expect(phone?.required).toBe(true);
  });

  it("offers ticket tiers only when the seller published them", () => {
    expect(keys("event_online")).toEqual(["attendee_name"]);
    const withTiers = fulfillmentFields("event_online", ["Early bird", "Door"]);
    expect(withTiers.map((field) => field.key)).toEqual(["attendee_name", "ticket_type"]);
    expect(withTiers[1]).toMatchObject({ type: "choice", options: ["Early bird", "Door"] });
  });

  it("reads the published tiers off the listing", () => {
    expect(ticketOptions(listing({ listing_metadata: { tickets: [{ name: "VIP" }, { name: " " }] } })))
      .toEqual(["VIP"]);
    expect(ticketOptions(listing({}))).toEqual([]);
  });

  it("asks nothing until an undecided kind has been settled", () => {
    expect(keys("shipping_or_pickup")).toEqual([]);
    expect(keys("service_choice")).toEqual([]);
  });
});

describe("the Continue button", () => {
  const address = {
    contact_name: "Ada",
    address_line1: "1 Main St",
    address_city: "Austin",
    address_country: "US"
  };

  it("names the first blank required field", () => {
    expect(firstMissingFulfillmentField("shipping", [], {})?.key).toBe("contact_name");
  });

  it("holds a US address until it has a state and a postal code", () => {
    expect(firstMissingFulfillmentField("shipping", [], address)?.key).toBe("address_region");
    expect(firstMissingFulfillmentField("shipping", [], { ...address, address_region: "TX" })?.key)
      .toBe("address_postal_code");
    expect(firstMissingFulfillmentField("shipping", [], { ...address, address_region: "TX", address_postal_code: "78701" }))
      .toBeNull();
  });

  it("lets a valid Irish address through without a postal code", () => {
    // Eircode is optional there; demanding one is a US assumption that blocks a
    // real buyer with a real address.
    expect(firstMissingFulfillmentField("shipping", [], {
      contact_name: "Ada",
      address_line1: "1 Grafton St",
      address_city: "Dublin",
      address_country: "IE"
    })).toBeNull();
  });

  it("never asks a digital buyer for anything", () => {
    expect(firstMissingFulfillmentField("digital", [], {})).toBeNull();
  });
});

describe("what a whole cart group is asked", () => {
  it("asks for the address when anything in the group ships", () => {
    expect(groupFulfillmentKind(["pickup", "shipping"])).toBe("shipping");
  });

  it("falls back to the scheduled item, then to pickup", () => {
    expect(groupFulfillmentKind(["digital", "booking_remote"])).toBe("booking_remote");
    expect(groupFulfillmentKind(["digital", "pickup"])).toBe("pickup");
  });

  it("asks a purely digital group nothing", () => {
    expect(groupFulfillmentKind(["digital", "digital"])).toBe("");
  });
});

describe("the destination line on the review step", () => {
  it("shows the address the buyer typed rather than a promise to ask later", () => {
    expect(fulfillmentDestinationSummary("shipping", {
      address_line1: "1 Main St",
      address_city: "Austin",
      address_region: "TX",
      address_postal_code: "78701",
      address_country: "US"
    })).toBe("1 Main St, Austin, TX, 78701, US");
  });

  it("describes the non-shipping order types without an address", () => {
    expect(fulfillmentDestinationSummary("digital", {})).toBe("Delivered to your PulseSoc account");
    expect(fulfillmentDestinationSummary("pickup", {})).toBe("Collected in person from the seller");
    expect(fulfillmentDestinationSummary("booking_remote", {
      scheduled_date: "2026-09-01",
      scheduled_time: "14:00",
      timezone: "UTC"
    })).toBe("Online, 2026-09-01 14:00 UTC");
  });
});
