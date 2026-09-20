/**
 * The hook that turns a network outcome into a verdict.
 *
 * `sellerAccess.ts` decides what a *payload* means; this decides what a
 * *failure* means, which is the harder half. There are three failures and they
 * must not be confused:
 *
 *   - The server answered no. Gate, and route them somewhere useful.
 *   - The server could not be reached. Keep the last known answer and offer a
 *     retry — a blip must not eject an approved seller from their own store.
 *   - The server has no such route. There is no gate on this deployment, so
 *     there is nothing to honour and the surfaces fall through to the checks
 *     that were already there.
 *
 * The third one is the reason this file exists. It was missing, so a build that
 * shipped ahead of its backend read every 404 as "the check failed" and put an
 * unclearable wall in front of every seller.
 */

const mockFetch = jest.fn();
const mockLoadCached = jest.fn();

jest.mock("../../api/sellerAccess", () => {
  const actual = jest.requireActual("../../api/sellerAccess");
  return {
    ...actual,
    fetchSellerAccessState: (...args: unknown[]) => mockFetch(...args),
    loadCachedSellerAccessState: (...args: unknown[]) => mockLoadCached(...args)
  };
});

// `useFocusEffect` is what drives the fetch. Outside a navigator it throws, so
// it is reduced here to the one property this hook depends on: run the effect
// on mount. The focus *re-run* is navigation's behaviour, not this hook's, and
// mocking it any more elaborately would be testing React Navigation.
jest.mock("@react-navigation/native", () => {
  const React = require("react");
  return {
    useFocusEffect: (effect: () => void | (() => void)) => React.useEffect(effect, [effect])
  };
});

import { act, renderHook, waitFor } from "@testing-library/react-native";
import { PulseApiError } from "../../api/pulseApi";
import { parseSellerAccessState } from "../../api/sellerAccess";
import { useSellerAccess } from "../useSellerAccess";

const APPROVED = parseSellerAccessState({ seller_application_status: "APPROVED" });

beforeEach(() => {
  mockFetch.mockReset();
  mockLoadCached.mockReset();
  mockLoadCached.mockResolvedValue(null);
});

describe("the verdict the server gave", () => {
  it("starts denied and loading, then takes the server's answer", async () => {
    // The initial state is not a neutral blank: a screen that renders seller
    // tools before the answer arrives has the original bug, narrowed to a few
    // hundred milliseconds.
    mockFetch.mockResolvedValue(APPROVED);
    const { result } = renderHook(() => useSellerAccess());
    expect(result.current.loading).toBe(true);
    expect(result.current.state.store_access).toBe(false);

    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(result.current.state.store_access).toBe(true);
    expect(result.current.failed).toBe(false);
    expect(result.current.unsupported).toBe(false);
  });
});

describe("the server could not be reached", () => {
  it("reports a failure and keeps the door shut, offering a retry", async () => {
    mockFetch.mockRejectedValue(
      new PulseApiError("unreachable", 503, "request_unreachable")
    );
    const { result } = renderHook(() => useSellerAccess());
    await waitFor(() => expect(result.current.loading).toBe(false));

    expect(result.current.failed).toBe(true);
    expect(result.current.stale).toBe(true);
    expect(result.current.unsupported).toBe(false);
  });

  it("does not flip a cached approval to denied on a blip", async () => {
    // A network hiccup must not take a seller's store away. The server
    // re-checks every mutation, so a stale "approved" costs a refused request,
    // not an escape.
    mockLoadCached.mockResolvedValue(APPROVED);
    mockFetch.mockRejectedValue(new PulseApiError("boom", 500));
    const { result } = renderHook(() => useSellerAccess());

    await waitFor(() => expect(result.current.state.store_access).toBe(true));
    expect(result.current.failed).toBe(true);
    expect(result.current.stale).toBe(true);
  });
});

describe("the server has no such route", () => {
  it("reports unsupported rather than failed", async () => {
    // The distinction the whole fix rests on. `failed` drives a retry the user
    // can press; a 404 will 404 again forever, so presenting it as a failure
    // is offering a button that cannot work.
    mockFetch.mockRejectedValue(new PulseApiError("Not Found", 404, "not_found"));
    const { result } = renderHook(() => useSellerAccess());
    await waitFor(() => expect(result.current.loading).toBe(false));

    expect(result.current.unsupported).toBe(true);
    expect(result.current.failed).toBe(false);
    expect(result.current.stale).toBe(false);
  });

  it("leaves the state denied, because callers must not gate on it", async () => {
    // `unsupported` is not an approval and does not pretend to be one. The
    // state stays denied; it is the *gate* that steps aside, so any caller
    // that reads `state` without checking `unsupported` still fails closed.
    mockFetch.mockRejectedValue(new PulseApiError("Not Found", 404));
    const { result } = renderHook(() => useSellerAccess());
    await waitFor(() => expect(result.current.unsupported).toBe(true));

    expect(result.current.state.store_access).toBe(false);
    expect(result.current.state.seller_approved).toBe(false);
  });

  it("clears unsupported once the route ships", async () => {
    // Deployments catch up. A hook that latched `unsupported` would keep every
    // seller ungated on a server that had since grown the gate — the fix for
    // the wall turning permanently into a hole.
    mockFetch.mockRejectedValue(new PulseApiError("Not Found", 404));
    const { result } = renderHook(() => useSellerAccess());
    await waitFor(() => expect(result.current.unsupported).toBe(true));

    mockFetch.mockResolvedValue(parseSellerAccessState({ seller_application_status: "DRAFT" }));
    await act(async () => {
      await result.current.refresh();
    });

    expect(result.current.unsupported).toBe(false);
    expect(result.current.state.store_access).toBe(false);
  });

  it("clears unsupported when a later attempt fails for a real reason", async () => {
    // The other direction of the same latch. Going 404 → 503 must land on
    // "could not check", not stay on "there is no check".
    mockFetch.mockRejectedValue(new PulseApiError("Not Found", 404));
    const { result } = renderHook(() => useSellerAccess());
    await waitFor(() => expect(result.current.unsupported).toBe(true));

    mockFetch.mockRejectedValue(new PulseApiError("unreachable", 503, "request_unreachable"));
    await act(async () => {
      await result.current.refresh();
    });

    expect(result.current.unsupported).toBe(false);
    expect(result.current.failed).toBe(true);
  });
});
