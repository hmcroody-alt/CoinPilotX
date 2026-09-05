/**
 * What actually leaves the device when a member is discussing an asset.
 *
 * This file exists because of a specific bad test that could have been written
 * instead. "The chip says Discussing Bitcoin · BTC" is true of a screen that
 * sends nothing at all, which is precisely the failure mode the mission names:
 * a label that describes an intelligence context the request does not carry.
 * So the assertion is on the serialised request body — the last place the
 * client speaks before the server has to believe it.
 *
 * The seam is `pulseApi`, not `fetch`, because everything between them (auth
 * headers, base URL, refresh) is someone else's contract and already tested;
 * what is under test here is the shape of the payload and the fact that
 * `buildUndxSendContext` is the only thing that decides it.
 */

import { buildMarketContextEnvelope, buildUndxSendContext, clearMarketContext, parkMarketContext, resetMarketContextForTests } from "../../undx/marketContext";

// The `mock` prefix is required, not stylistic: `jest.mock` is hoisted above
// this declaration, so its factory may only close over names beginning with
// `mock`. Calling it `pulseApi` makes the whole file fail to compile — which
// jest reports as a failed suite with *zero* failed tests, so it reads like an
// infrastructure hiccup rather than three assertions that never ran.
const mockPulseApi = jest.fn();

jest.mock("../pulseApi", () => ({
  pulseApi: (...args: unknown[]) => mockPulseApi(...args),
  PulseApiError: class PulseApiError extends Error {}
}));
jest.mock("@react-native-async-storage/async-storage", () => ({
  getItem: jest.fn().mockResolvedValue(null),
  setItem: jest.fn().mockResolvedValue(undefined),
  removeItem: jest.fn().mockResolvedValue(undefined)
}));
jest.mock("expo-file-system", () => ({ File: class {} }));

// Imported after the mocks so the module under test binds to them.
// eslint-disable-next-line @typescript-eslint/no-var-requires
const { sendPulseAiMessage } = require("../messenger") as typeof import("../messenger");

const sentBody = () => JSON.parse(mockPulseApi.mock.calls[0][1].body as string);

beforeEach(() => {
  mockPulseApi.mockReset();
  mockPulseApi.mockResolvedValue({ conversation: { id: -9001001 }, messages: [] });
  resetMarketContextForTests();
});

const bitcoin = () =>
  buildMarketContextEnvelope({
    source: "asset_detail",
    symbol: "BTC",
    name: "Bitcoin",
    rank: 1,
    price: 90000,
    change24h: 1.4,
    selectedRange: "24H"
  });

describe("the UNDX request payload", () => {
  it("carries structured asset identity, not a sentence about Bitcoin", async () => {
    parkMarketContext(bitcoin());
    await sendPulseAiMessage({ body: "What do you think?", ui_context: buildUndxSendContext() });

    const payload = sentBody();
    // The transcript stays authentic: the context rides in ui_context, and the
    // member's own words are the only message. No synthesised "Discuss Bitcoin."
    expect(payload.message).toBe("What do you think?");
    expect(payload.ui_context.market_context.asset).toMatchObject({
      symbol: "BTC",
      name: "Bitcoin"
    });
    expect(payload.ui_context.market_context.source).toBe("asset_detail");
    // The price travels as an observation of a screen, never as a claim about
    // now — it is inside a snapshot the server can date and discount.
    expect(payload.ui_context.market_context.market_snapshot.price).toBe(90000);
  });

  it("says the topic ended when the chip is dismissed", async () => {
    parkMarketContext(bitcoin());
    buildUndxSendContext(); // the handoff turn
    clearMarketContext();
    await sendPulseAiMessage({ body: "Never mind.", ui_context: buildUndxSendContext() });

    const payload = sentBody();
    expect(payload.ui_context.market_context_cleared).toBe(true);
    expect(payload.ui_context.market_context).toBeUndefined();
  });

  it("sends no crypto context at all for an ordinary UNDX conversation", async () => {
    // Stage 11: the tab entry must not become a screen that assumes crypto.
    await sendPulseAiMessage({ body: "Summarise my week.", ui_context: buildUndxSendContext() });

    const payload = sentBody();
    expect(payload.ui_context).toEqual({});
  });
});
