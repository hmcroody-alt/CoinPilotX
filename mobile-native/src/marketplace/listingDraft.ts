/**
 * The listing-creation draft: shape, defaults, validation and payload assembly.
 *
 * Everything in this module is pure — no React, no storage, no network — so the
 * whole creation contract is unit-testable without rendering the wizard.
 * Persistence lives in `listingDraftStore.ts`; the screen only ever moves data
 * between the two.
 *
 * The draft deliberately keeps *every* type's fields at once. Switching from
 * Physical to Service and back must not lose the condition and quantity the
 * seller already set, so type sections are parallel branches rather than a
 * single mutable bag.
 */

import {
  BookingAvailability,
  BookingWeekday,
  ListingMetadata,
  MarketplaceDigitalFile,
  MarketplaceListingCreatePayload,
  MarketplaceListingType
} from "../api/marketplace";

export type ListingWizardStep = "type" | "details" | "preview";

export const LISTING_TYPES: MarketplaceListingType[] = ["physical", "digital", "service", "event", "booking"];

export const BOOKING_WEEKDAYS: BookingWeekday[] = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"];

export const BOOKING_DURATIONS = [15, 30, 45, 60, 90] as const;

export const BOOKING_BUFFERS = [0, 10, 15, 30] as const;

export const SERVICE_DELIVERY_DAYS = [1, 2, 3, 5, 7, 14, 30] as const;

export const DESCRIPTION_MAX_LENGTH = 2000;

export type DraftVariant = { name: string; value: string };
export type DraftAddon = { title: string; price: string };
export type DraftTicket = { name: string; price: string; capacity: string };

export type PhysicalDraft = {
  condition: "new" | "like_new" | "good" | "fair";
  quantity: number;
  variants: DraftVariant[];
  deliveryOption: "pickup" | "shipping" | "both";
  location: string;
  returnPolicy: "none" | "7_days" | "14_days" | "30_days";
};

export type DigitalDraft = {
  files: MarketplaceDigitalFile[];
  license: "personal" | "commercial";
  downloadLimitMode: "unlimited" | "limited";
  downloadLimit: string;
};

export type ServiceDraft = {
  pricingMode: "fixed" | "starting_at" | "hourly";
  deliveryTimeDays: number;
  serviceLocation: "remote" | "in_person" | "both";
  location: string;
  included: string[];
  addons: DraftAddon[];
};

export type EventDraft = {
  name: string;
  date: string;
  startTime: string;
  endTime: string;
  venueMode: "in_person" | "online" | "pulsesoc_live";
  location: string;
  onlineUrl: string;
  tickets: DraftTicket[];
};

export type BookingDraft = {
  durationMinutes: number;
  meetingMode: "video" | "audio" | "in_person";
  availability: BookingAvailability;
  bufferMinutes: number;
  cancellationPolicy: "flexible" | "24_hours" | "48_hours" | "strict";
};

export type ListingDraft = {
  version: 1;
  serverListingId: number;
  step: ListingWizardStep;
  listingType: MarketplaceListingType | null;
  updatedAt: string;
  title: string;
  description: string;
  price: string;
  currency: string;
  category: string;
  /** First uploaded media id — the cover the backend requires. */
  coverMediaId: number;
  coverPreviewUri: string;
  /** Additional uploaded media ids, in gallery order. */
  galleryMediaIds: number[];
  physical: PhysicalDraft;
  digital: DigitalDraft;
  service: ServiceDraft;
  event: EventDraft;
  booking: BookingDraft;
};

export const LISTING_CATEGORY_KEYS = [
  "education",
  "electronics",
  "fashion",
  "homeGarden",
  "beauty",
  "sports",
  "services",
  "events",
  "digitalGoods",
  "other"
] as const;

export type ListingCategoryKey = (typeof LISTING_CATEGORY_KEYS)[number];

/** Category values as the backend stores them (English labels, matching web). */
export const LISTING_CATEGORY_VALUES: Record<ListingCategoryKey, string> = {
  education: "Education",
  electronics: "Electronics",
  fashion: "Fashion",
  homeGarden: "Home & Garden",
  beauty: "Beauty",
  sports: "Sports",
  services: "Services",
  events: "Events",
  digitalGoods: "Digital Goods",
  other: "Other"
};

export const LISTING_CURRENCIES = ["USD", "EUR", "GBP", "CAD", "AUD", "HTG"] as const;

export function defaultBookingAvailability(): BookingAvailability {
  const workday = () => [{ start: "09:00", end: "17:00" }];
  return {
    mon: workday(),
    tue: workday(),
    wed: workday(),
    thu: workday(),
    fri: workday(),
    sat: [],
    sun: []
  };
}

export function createListingDraft(): ListingDraft {
  return {
    version: 1,
    serverListingId: 0,
    step: "type",
    listingType: null,
    updatedAt: new Date().toISOString(),
    title: "",
    description: "",
    price: "",
    currency: "USD",
    category: LISTING_CATEGORY_VALUES.education,
    coverMediaId: 0,
    coverPreviewUri: "",
    galleryMediaIds: [],
    physical: {
      condition: "new",
      quantity: 1,
      variants: [],
      deliveryOption: "shipping",
      location: "",
      returnPolicy: "none"
    },
    digital: {
      files: [],
      license: "personal",
      downloadLimitMode: "unlimited",
      downloadLimit: ""
    },
    service: {
      pricingMode: "fixed",
      deliveryTimeDays: 3,
      serviceLocation: "remote",
      location: "",
      included: [],
      addons: []
    },
    event: {
      name: "",
      date: "",
      startTime: "18:00",
      endTime: "20:00",
      venueMode: "in_person",
      location: "",
      onlineUrl: "",
      tickets: [{ name: "", price: "", capacity: "" }]
    },
    booking: {
      durationMinutes: 30,
      meetingMode: "video",
      availability: defaultBookingAvailability(),
      bufferMinutes: 0,
      cancellationPolicy: "24_hours"
    }
  };
}

/**
 * Defensive merge over a possibly stale or truncated persisted draft. Absent or
 * malformed branches fall back to defaults rather than crashing hydration —
 * the same posture every cached payload reader in this app takes.
 */
export function normalizeListingDraft(value: Partial<ListingDraft> | null | undefined): ListingDraft {
  const base = createListingDraft();
  if (!value || typeof value !== "object") return base;
  const listingType = LISTING_TYPES.includes(value.listingType as MarketplaceListingType)
    ? (value.listingType as MarketplaceListingType)
    : null;
  const step: ListingWizardStep =
    value.step === "details" || value.step === "preview" ? (listingType ? value.step : "type") : "type";
  const availability: BookingAvailability = defaultBookingAvailability();
  const storedAvailability = value.booking?.availability;
  if (storedAvailability && typeof storedAvailability === "object") {
    BOOKING_WEEKDAYS.forEach((day) => {
      const ranges = (storedAvailability as Record<string, unknown>)[day];
      if (Array.isArray(ranges)) {
        availability[day] = ranges
          .filter((range): range is { start: string; end: string } =>
            Boolean(range && typeof range === "object" && "start" in range && "end" in range)
          )
          .map((range) => ({ start: String(range.start || ""), end: String(range.end || "") }));
      }
    });
  }
  return {
    ...base,
    serverListingId: Number(value.serverListingId || 0),
    step,
    listingType,
    updatedAt: String(value.updatedAt || base.updatedAt),
    title: String(value.title || ""),
    description: String(value.description || ""),
    price: String(value.price || ""),
    currency: String(value.currency || base.currency),
    category: String(value.category || base.category),
    coverMediaId: Number(value.coverMediaId || 0),
    coverPreviewUri: String(value.coverPreviewUri || ""),
    galleryMediaIds: Array.isArray(value.galleryMediaIds)
      ? value.galleryMediaIds.map(Number).filter((id) => Number.isFinite(id) && id > 0)
      : [],
    physical: { ...base.physical, ...(value.physical || {}), variants: sanitizeRows(value.physical?.variants, ["name", "value"]) as DraftVariant[] },
    digital: {
      ...base.digital,
      ...(value.digital || {}),
      files: Array.isArray(value.digital?.files)
        ? value.digital!.files
            .map((file) => ({
              file_id: Number(file?.file_id || 0),
              name: String(file?.name || ""),
              size_bytes: Number(file?.size_bytes || 0)
            }))
            .filter((file) => file.file_id > 0)
        : []
    },
    service: {
      ...base.service,
      ...(value.service || {}),
      included: Array.isArray(value.service?.included) ? value.service!.included.map(String).filter(Boolean) : [],
      addons: sanitizeRows(value.service?.addons, ["title", "price"]) as DraftAddon[]
    },
    event: {
      ...base.event,
      ...(value.event || {}),
      tickets: (() => {
        const tickets = sanitizeRows(value.event?.tickets, ["name", "price", "capacity"]) as DraftTicket[];
        return tickets.length ? tickets : base.event.tickets;
      })()
    },
    booking: { ...base.booking, ...(value.booking || {}), availability }
  };
}

function sanitizeRows(rows: unknown, keys: string[]): Record<string, string>[] {
  if (!Array.isArray(rows)) return [];
  return rows
    .filter((row): row is Record<string, unknown> => Boolean(row && typeof row === "object"))
    .map((row) => {
      const out: Record<string, string> = {};
      keys.forEach((key) => {
        out[key] = String(row[key] ?? "");
      });
      return out;
    });
}

/** True when the draft carries anything a seller would mind losing. */
export function listingDraftHasContent(draft: ListingDraft): boolean {
  return Boolean(
    draft.listingType ||
      draft.title.trim() ||
      draft.description.trim() ||
      draft.price.trim() ||
      draft.coverMediaId > 0 ||
      draft.galleryMediaIds.length > 0
  );
}

/* ------------------------------------------------------------------ *
 * Validation
 * ------------------------------------------------------------------ */

/**
 * A validation failure, addressed by field id and by the i18n key of the
 * message. The screen renders `messageKey` through `t()`, so this module stays
 * free of user-facing English.
 */
export type ListingDraftIssue = { field: string; messageKey: string };

const ERROR = (field: string, key: string): ListingDraftIssue => ({
  field,
  messageKey: `commerce:listingWizard.${key}`
});

export function validateListingDraft(draft: ListingDraft): ListingDraftIssue[] {
  const issues: ListingDraftIssue[] = [];
  if (!draft.listingType) {
    issues.push(ERROR("listingType", "errorType"));
    return issues;
  }
  if (!draft.title.trim()) issues.push(ERROR("title", "errorTitle"));
  if (!draft.description.trim()) issues.push(ERROR("description", "errorDescription"));
  if (draft.coverMediaId <= 0) issues.push(ERROR("media", "errorCover"));
  const price = Number(draft.price);
  if (!draft.price.trim() || !Number.isFinite(price) || price < 0) issues.push(ERROR("price", "errorPrice"));

  switch (draft.listingType) {
    case "physical": {
      if (!Number.isFinite(draft.physical.quantity) || draft.physical.quantity < 1) {
        issues.push(ERROR("quantity", "errorQuantity"));
      }
      if (draft.physical.deliveryOption !== "shipping" && !draft.physical.location.trim()) {
        issues.push(ERROR("location", "errorLocation"));
      }
      break;
    }
    case "digital": {
      if (!draft.digital.files.length) issues.push(ERROR("files", "errorFiles"));
      if (draft.digital.downloadLimitMode === "limited") {
        const limit = Number(draft.digital.downloadLimit);
        if (!Number.isFinite(limit) || limit < 1) issues.push(ERROR("downloadLimit", "errorDownloadLimit"));
      }
      break;
    }
    case "service": {
      if (draft.service.serviceLocation !== "remote" && !draft.service.location.trim()) {
        issues.push(ERROR("location", "errorLocation"));
      }
      break;
    }
    case "event": {
      if (!draft.event.name.trim()) issues.push(ERROR("eventName", "errorEventName"));
      if (!/^\d{4}-\d{2}-\d{2}$/.test(draft.event.date.trim())) issues.push(ERROR("eventDate", "errorDate"));
      if (!draft.event.startTime || !draft.event.endTime) issues.push(ERROR("eventTimes", "errorTimes"));
      if (draft.event.venueMode === "in_person" && !draft.event.location.trim()) {
        issues.push(ERROR("location", "errorLocation"));
      }
      if (draft.event.venueMode === "online" && !draft.event.onlineUrl.trim()) {
        issues.push(ERROR("onlineUrl", "errorUrl"));
      }
      const validTickets = draft.event.tickets.filter((ticket) => ticket.name.trim() && Number(ticket.capacity) > 0);
      if (!validTickets.length) issues.push(ERROR("tickets", "errorTickets"));
      break;
    }
    case "booking": {
      if (!BOOKING_DURATIONS.includes(draft.booking.durationMinutes as (typeof BOOKING_DURATIONS)[number])) {
        issues.push(ERROR("duration", "errorDuration"));
      }
      const openDays = BOOKING_WEEKDAYS.filter((day) => draft.booking.availability[day].length > 0);
      if (!openDays.length) issues.push(ERROR("availability", "errorAvailability"));
      break;
    }
  }
  return issues;
}

export function listingDraftIssueFor(issues: ListingDraftIssue[], field: string): string {
  return issues.find((issue) => issue.field === field)?.messageKey || "";
}

/* ------------------------------------------------------------------ *
 * Payload assembly
 * ------------------------------------------------------------------ */

/**
 * "$25.00" for known currencies, "25 HTG"-style for the rest. Falls back to a
 * plain join when `Intl` lacks the currency — the label is display data, the
 * numeric truth stays in the metadata the backend stores.
 */
export function formatListingPriceLabel(price: string, currency: string): string {
  const amount = Number(price);
  if (!Number.isFinite(amount)) return price.trim();
  try {
    return new Intl.NumberFormat("en-US", { style: "currency", currency, currencyDisplay: "symbol" }).format(amount);
  } catch {
    return `${amount} ${currency}`;
  }
}

export function listingMetadataFor(draft: ListingDraft): ListingMetadata {
  switch (draft.listingType) {
    case "digital":
      return {
        files: draft.digital.files,
        delivery: "automatic",
        license: draft.digital.license === "commercial" ? "Commercial Use" : "Personal Use",
        download_limit:
          draft.digital.downloadLimitMode === "limited" && Number(draft.digital.downloadLimit) > 0
            ? Number(draft.digital.downloadLimit)
            : null
      };
    case "service":
      return {
        pricing_mode: draft.service.pricingMode,
        delivery_time_days: draft.service.deliveryTimeDays,
        service_location: draft.service.serviceLocation,
        location: draft.service.location.trim(),
        included: draft.service.included.map((item) => item.trim()).filter(Boolean),
        addons: draft.service.addons
          .filter((addon) => addon.title.trim())
          .map((addon) => ({
            title: addon.title.trim(),
            price_label: formatListingPriceLabel(addon.price, draft.currency)
          }))
      };
    case "event":
      return {
        event_date: draft.event.date.trim(),
        start_time: draft.event.startTime,
        end_time: draft.event.endTime,
        venue_mode: draft.event.venueMode,
        location: draft.event.venueMode === "in_person" ? draft.event.location.trim() : "",
        online_url: draft.event.venueMode === "online" ? draft.event.onlineUrl.trim() : "",
        tickets: draft.event.tickets
          .filter((ticket) => ticket.name.trim() && Number(ticket.capacity) > 0)
          .map((ticket) => ({
            name: ticket.name.trim(),
            price_label: formatListingPriceLabel(ticket.price || draft.price, draft.currency),
            capacity: Math.max(1, Math.floor(Number(ticket.capacity)))
          }))
      };
    case "booking":
      return {
        duration_minutes: draft.booking.durationMinutes,
        meeting_mode: draft.booking.meetingMode,
        availability: draft.booking.availability,
        buffer_minutes: draft.booking.bufferMinutes,
        cancellation_policy: draft.booking.cancellationPolicy
      };
    case "physical":
    default:
      return {
        condition: draft.physical.condition,
        variants: draft.physical.variants
          .filter((variant) => variant.name.trim() && variant.value.trim())
          .map((variant) => ({ name: variant.name.trim(), value: variant.value.trim() })),
        delivery_options: draft.physical.deliveryOption,
        location: draft.physical.location.trim(),
        return_policy: draft.physical.returnPolicy
      };
  }
}

export function buildListingCreatePayload(draft: ListingDraft): MarketplaceListingCreatePayload {
  const listingType = draft.listingType || "physical";
  const mediaIds = [draft.coverMediaId, ...draft.galleryMediaIds].filter((id) => id > 0);
  const payload: MarketplaceListingCreatePayload = {
    submission_action: "submit",
    title: draft.title.trim(),
    description: draft.description.trim(),
    short_description: draft.description.trim().slice(0, 140),
    category: draft.category,
    price_label: formatListingPriceLabel(draft.price, draft.currency),
    currency: draft.currency,
    product_type: listingType,
    listing_type: listingType,
    listing_metadata: listingMetadataFor(draft),
    media_ids: Array.from(new Set(mediaIds))
  };
  if (listingType === "physical") {
    payload.quantity = Math.max(1, Math.floor(draft.physical.quantity));
    payload.refund_policy = draft.physical.returnPolicy;
  }
  if (listingType === "event") {
    const capacity = draft.event.tickets.reduce((sum, ticket) => sum + Math.max(0, Math.floor(Number(ticket.capacity) || 0)), 0);
    if (capacity > 0) payload.quantity = capacity;
  }
  return payload;
}
