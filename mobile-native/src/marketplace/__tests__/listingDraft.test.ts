/**
 * Draft model: validation and payload assembly for each of the five listing
 * types. These are the pure functions the wizard trusts at the publish
 * boundary, so the assertions here mirror the backend contract exactly.
 */

import {
  BookingListingMetadata,
  DigitalListingMetadata,
  EventListingMetadata,
  PhysicalListingMetadata,
  ServiceListingMetadata
} from "../../api/marketplace";
import {
  buildListingCreatePayload,
  createListingDraft,
  defaultBookingAvailability,
  ListingDraft,
  listingDraftHasContent,
  normalizeListingDraft,
  validateListingDraft
} from "../listingDraft";

function baseDraft(overrides: Partial<ListingDraft> = {}): ListingDraft {
  return {
    ...createListingDraft(),
    title: "Vintage lamp",
    description: "A lovely mid-century lamp in working order.",
    price: "25",
    currency: "USD",
    coverMediaId: 11,
    galleryMediaIds: [12, 13],
    ...overrides
  };
}

describe("validateListingDraft", () => {
  it("requires a type before anything else", () => {
    const issues = validateListingDraft(createListingDraft());
    expect(issues).toEqual([{ field: "listingType", messageKey: "commerce:listingWizard.errorType" }]);
  });

  it("passes a complete physical draft", () => {
    const draft = baseDraft({ listingType: "physical" });
    expect(validateListingDraft(draft)).toEqual([]);
  });

  it("requires location for physical pickup but not shipping", () => {
    const draft = baseDraft({ listingType: "physical" });
    draft.physical = { ...draft.physical, deliveryOption: "pickup", location: "" };
    expect(validateListingDraft(draft).map((i) => i.field)).toContain("location");
    draft.physical = { ...draft.physical, deliveryOption: "shipping" };
    expect(validateListingDraft(draft)).toEqual([]);
  });

  it("requires at least one uploaded file for digital listings", () => {
    const draft = baseDraft({ listingType: "digital" });
    expect(validateListingDraft(draft).map((i) => i.field)).toContain("files");
    draft.digital = { ...draft.digital, files: [{ file_id: 5, name: "kit.zip", size_bytes: 1024 }] };
    expect(validateListingDraft(draft)).toEqual([]);
  });

  it("requires a positive limit only when downloads are limited", () => {
    const draft = baseDraft({ listingType: "digital" });
    draft.digital = {
      ...draft.digital,
      files: [{ file_id: 5, name: "kit.zip", size_bytes: 1024 }],
      downloadLimitMode: "limited",
      downloadLimit: "0"
    };
    expect(validateListingDraft(draft).map((i) => i.field)).toContain("downloadLimit");
    draft.digital = { ...draft.digital, downloadLimit: "3" };
    expect(validateListingDraft(draft)).toEqual([]);
  });

  it("checks event name, date format, and tickets", () => {
    const draft = baseDraft({ listingType: "event" });
    draft.event = { ...draft.event, name: "", date: "next friday", location: "Studio 4" };
    const fields = validateListingDraft(draft).map((i) => i.field);
    expect(fields).toEqual(expect.arrayContaining(["eventName", "eventDate", "tickets"]));

    draft.event = {
      ...draft.event,
      name: "Pottery night",
      date: "2026-09-01",
      tickets: [{ name: "General", price: "15", capacity: "20" }]
    };
    expect(validateListingDraft(draft)).toEqual([]);
  });

  it("requires an online url only for online events", () => {
    const draft = baseDraft({ listingType: "event" });
    draft.event = {
      ...draft.event,
      name: "Webinar",
      date: "2026-09-01",
      venueMode: "online",
      onlineUrl: "",
      tickets: [{ name: "Seat", price: "0", capacity: "100" }]
    };
    expect(validateListingDraft(draft).map((i) => i.field)).toContain("onlineUrl");
    draft.event = { ...draft.event, onlineUrl: "https://example.com/webinar" };
    expect(validateListingDraft(draft)).toEqual([]);
  });

  it("requires at least one open day for bookings", () => {
    const draft = baseDraft({ listingType: "booking" });
    draft.booking = {
      ...draft.booking,
      availability: { mon: [], tue: [], wed: [], thu: [], fri: [], sat: [], sun: [] }
    };
    expect(validateListingDraft(draft).map((i) => i.field)).toContain("availability");
    draft.booking = { ...draft.booking, availability: defaultBookingAvailability() };
    expect(validateListingDraft(draft)).toEqual([]);
  });
});

describe("buildListingCreatePayload", () => {
  it("assembles the physical payload with quantity and refund policy", () => {
    const draft = baseDraft({ listingType: "physical" });
    draft.physical = {
      condition: "like_new",
      quantity: 4,
      variants: [
        { name: "Color", value: "Brass" },
        { name: "", value: "ignored" }
      ],
      deliveryOption: "both",
      location: "Brooklyn",
      returnPolicy: "14_days"
    };
    const payload = buildListingCreatePayload(draft);

    expect(payload.listing_type).toBe("physical");
    expect(payload.product_type).toBe("physical");
    expect(payload.quantity).toBe(4);
    expect(payload.refund_policy).toBe("14_days");
    expect(payload.price_label).toBe("$25.00");
    expect(payload.media_ids).toEqual([11, 12, 13]);
    const metadata = payload.listing_metadata as PhysicalListingMetadata;
    expect(metadata.condition).toBe("like_new");
    expect(metadata.variants).toEqual([{ name: "Color", value: "Brass" }]);
    expect(metadata.delivery_options).toBe("both");
    expect(metadata.location).toBe("Brooklyn");
    expect(metadata.return_policy).toBe("14_days");
  });

  it("assembles the digital payload with files and a null unlimited limit", () => {
    const draft = baseDraft({ listingType: "digital" });
    draft.digital = {
      files: [{ file_id: 9, name: "preset-pack.zip", size_bytes: 2048 }],
      license: "commercial",
      downloadLimitMode: "unlimited",
      downloadLimit: ""
    };
    const payload = buildListingCreatePayload(draft);

    expect(payload.listing_type).toBe("digital");
    expect(payload.quantity).toBeUndefined();
    const metadata = payload.listing_metadata as DigitalListingMetadata;
    expect(metadata.delivery).toBe("automatic");
    expect(metadata.license).toBe("Commercial Use");
    expect(metadata.download_limit).toBeNull();
    expect(metadata.files).toEqual([{ file_id: 9, name: "preset-pack.zip", size_bytes: 2048 }]);
  });

  it("assembles the service payload with trimmed includes and priced add-ons", () => {
    const draft = baseDraft({ listingType: "service" });
    draft.service = {
      pricingMode: "hourly",
      deliveryTimeDays: 7,
      serviceLocation: "both",
      location: "Miami",
      included: [" Consultation ", "", "Two revisions"],
      addons: [
        { title: "Rush delivery", price: "10" },
        { title: "  ", price: "99" }
      ]
    };
    const metadata = buildListingCreatePayload(draft).listing_metadata as ServiceListingMetadata;

    expect(metadata.pricing_mode).toBe("hourly");
    expect(metadata.delivery_time_days).toBe(7);
    expect(metadata.service_location).toBe("both");
    expect(metadata.included).toEqual(["Consultation", "Two revisions"]);
    expect(metadata.addons).toEqual([{ title: "Rush delivery", price_label: "$10.00" }]);
  });

  it("assembles the event payload and derives quantity from ticket capacity", () => {
    const draft = baseDraft({ listingType: "event" });
    draft.event = {
      name: "Pottery night",
      date: "2026-09-01",
      startTime: "18:00",
      endTime: "20:00",
      venueMode: "in_person",
      location: "Studio 4",
      onlineUrl: "https://ignored.example",
      tickets: [
        { name: "General", price: "15", capacity: "20" },
        { name: "VIP", price: "40", capacity: "5" },
        { name: "", price: "1", capacity: "99" }
      ]
    };
    const payload = buildListingCreatePayload(draft);

    expect(payload.quantity).toBe(124); // 20 + 5 + 99: raw capacity sum, valid or not
    const metadata = payload.listing_metadata as EventListingMetadata;
    expect(metadata.venue_mode).toBe("in_person");
    expect(metadata.location).toBe("Studio 4");
    expect(metadata.online_url).toBe(""); // scrubbed for in-person events
    expect(metadata.tickets).toEqual([
      { name: "General", price_label: "$15.00", capacity: 20 },
      { name: "VIP", price_label: "$40.00", capacity: 5 }
    ]);
  });

  it("assembles the booking payload with availability and policies", () => {
    const draft = baseDraft({ listingType: "booking" });
    draft.booking = {
      durationMinutes: 60,
      meetingMode: "in_person",
      availability: { ...defaultBookingAvailability(), sat: [{ start: "10:00", end: "14:00" }] },
      bufferMinutes: 15,
      cancellationPolicy: "strict"
    };
    const metadata = buildListingCreatePayload(draft).listing_metadata as BookingListingMetadata;

    expect(metadata.duration_minutes).toBe(60);
    expect(metadata.meeting_mode).toBe("in_person");
    expect(metadata.buffer_minutes).toBe(15);
    expect(metadata.cancellation_policy).toBe("strict");
    expect(metadata.availability.mon).toEqual([{ start: "09:00", end: "17:00" }]);
    expect(metadata.availability.sat).toEqual([{ start: "10:00", end: "14:00" }]);
    expect(metadata.availability.sun).toEqual([]);
  });
});

describe("normalizeListingDraft", () => {
  it("recovers a fresh draft from garbage", () => {
    const draft = normalizeListingDraft({ step: "preview", listingType: "hoverboard" } as never);
    expect(draft.listingType).toBeNull();
    expect(draft.step).toBe("type"); // no type means the step can't be past the picker
    expect(listingDraftHasContent(draft)).toBe(false);
  });

  it("keeps stored content and repairs missing branches", () => {
    const draft = normalizeListingDraft({
      listingType: "booking",
      step: "details",
      title: "Guitar lessons",
      booking: { availability: { mon: [{ start: "08:00", end: "12:00" }] } }
    } as never);
    expect(draft.title).toBe("Guitar lessons");
    expect(draft.step).toBe("details");
    expect(draft.booking.availability.mon).toEqual([{ start: "08:00", end: "12:00" }]);
    expect(draft.booking.availability.tue).toEqual([{ start: "09:00", end: "17:00" }]);
    expect(listingDraftHasContent(draft)).toBe(true);
  });
});
