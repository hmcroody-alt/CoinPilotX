/**
 * The Events tab's wire shape, pinned.
 *
 * `/api/pages/:id/events` answers three things at once and the screen branches
 * on all three: whether the events domain is switched on for this environment,
 * whether this presence has been pointed at a business, and the dates
 * themselves. Two booleans and a list, which is only interesting because of
 * what happens when they are missing.
 *
 * They default to `false`, deliberately. A server old enough not to send
 * `enabled` is a server that cannot serve events, and reading a missing flag as
 * "on" would put the client into the branch that says "this presence has no
 * dates" — a claim about the artist, made because of a deployment. Absent means
 * off, in the direction where being wrong is harmless.
 *
 * The event shape itself is the server's visitor projection: no organiser id,
 * no owning business id, no attendee list, no sales counts. This file does not
 * re-assert that (the server's own tests own it) but it does pin that the
 * client passes rows through rather than reshaping them, so a field the server
 * decides to withhold is actually gone rather than reconstructed here.
 */

const mockPulseApi = jest.fn();

jest.mock("../pulseApi", () => ({
  pulseApi: (...args: unknown[]) => mockPulseApi(...args)
}));

import { listPageEvents } from "../pages";

beforeEach(() => {
  mockPulseApi.mockReset();
});

describe("listPageEvents", () => {
  it("asks the presence's own events endpoint and carries the limit", async () => {
    mockPulseApi.mockResolvedValue({ ok: true, enabled: true, linked: true, events: [] });
    await listPageEvents(41, 5);
    expect(mockPulseApi).toHaveBeenCalledWith("/api/pages/41/events?limit=5");
  });

  it("defaults the limit rather than asking for everything", async () => {
    mockPulseApi.mockResolvedValue({ ok: true, enabled: true, linked: true, events: [] });
    await listPageEvents(41);
    expect(mockPulseApi).toHaveBeenCalledWith("/api/pages/41/events?limit=12");
  });

  it("hands back the rows the server sent, unreshaped", async () => {
    const event = {
      event_id: "ev_1",
      title: "Vault Session",
      venue: "The Vault",
      starts_at: "2099-09-14T20:00:00Z",
      currency: "GBP",
      ticket_types: [
        { ticket_type_id: "t1", name: "Standard", price_cents: 1800, sold_out: false }
      ]
    };
    mockPulseApi.mockResolvedValue({ ok: true, enabled: true, linked: true, events: [event] });
    const result = await listPageEvents(41);
    expect(result).toEqual({ enabled: true, linked: true, events: [event] });
  });

  it("reads a missing flag as off rather than as on", async () => {
    // A server that does not send these cannot serve events. Defaulting to
    // `true` would send the screen into "this presence has no dates", which
    // blames the page for our deployment.
    mockPulseApi.mockResolvedValue({ ok: true });
    expect(await listPageEvents(41)).toEqual({ enabled: false, linked: false, events: [] });
  });

  it("does not invent a list when the server omits one", async () => {
    mockPulseApi.mockResolvedValue({ ok: true, enabled: true, linked: true });
    const result = await listPageEvents(41);
    expect(result.events).toEqual([]);
    expect(result.linked).toBe(true);
  });

  it("lets a failure reach the caller instead of reporting an empty calendar", async () => {
    // The screen distinguishes "we could not load this" from "there is nothing
    // on". Swallowing the rejection here would erase that distinction before
    // the screen ever sees it.
    mockPulseApi.mockRejectedValue(new Error("503"));
    await expect(listPageEvents(41)).rejects.toThrow("503");
  });
});
