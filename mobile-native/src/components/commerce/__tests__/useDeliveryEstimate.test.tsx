/**
 * The three races a delivery line loses, and the guards that win them.
 *
 * The copy layer is pure and tested elsewhere. What is left in the hook is
 * timing, and timing is where a delivery estimate goes wrong in a way no static
 * check catches:
 *
 * 1. **A superseded answer.** The buyer taps `+`, a second request starts, and
 *    the first lands after it. Without a sequence guard the screen shows the
 *    window for one unit beside a quantity of two — a wrong promise, rendered
 *    confidently, with nothing on screen to suggest it is stale.
 * 2. **A burst.** `pulseApi` coalesces GETs; this is a POST, so five taps are
 *    five requests, four of which spend the supplier's rate limit on an answer
 *    nobody will see. §18 and §25.
 * 3. **A request with nothing to ask about.** The product screen renders before
 *    its listing loads, and a placeholder reference would spend a supplier call
 *    to be told the listing does not exist.
 *
 * Fake timers throughout, because the debounce is the mechanism under test and a
 * real 350ms wait in a unit test is 350ms of nothing.
 */

import { act, renderHook } from "@testing-library/react-native";

import type { DeliveryAnswer } from "../../../api/delivery";
import { useDeliveryEstimate } from "../DeliveryEstimateLine";

const mockFetch = jest.fn();
jest.mock("../../../api/delivery", () => {
  const actual = jest.requireActual("../../../api/delivery");
  return { ...actual, fetchDeliveryEstimate: (...args: unknown[]) => mockFetch(...args) };
});

function windowFor(earliest: string, latest: string): DeliveryAnswer {
  return {
    delivery: {
      state: "ESTIMATED",
      reason: null,
      earliest,
      latest,
      confidence: "PROVIDER_QUOTED",
      guaranteed: false,
      isEstimate: true,
      shippingPrice: "FREE"
    },
    destination: { country: "US", precision: "COUNTRY", tier: "SESSION", known: true }
  };
}

beforeEach(() => {
  mockFetch.mockReset();
  jest.useFakeTimers();
});
afterEach(() => jest.useRealTimers());

/** Run the debounce out and let the fetch promise settle. Both halves are needed:
 * advancing timers starts the request, and flushing microtasks delivers it. */
async function settle(ms = 0) {
  await act(async () => {
    jest.advanceTimersByTime(ms);
  });
}

describe("useDeliveryEstimate", () => {
  it("asks nothing when there is no reference to ask about", async () => {
    const { result } = renderHook(() => useDeliveryEstimate(null));
    await settle();
    expect(mockFetch).not.toHaveBeenCalled();
    expect(result.current.answer).toBeNull();
    expect(result.current.loading).toBe(false);
  });

  it("treats a blank reference the same as none", async () => {
    renderHook(() => useDeliveryEstimate("   "));
    await settle();
    expect(mockFetch).not.toHaveBeenCalled();
  });

  it("reports loading before the answer and the answer after it", async () => {
    mockFetch.mockResolvedValue(windowFor("2026-03-16", "2026-03-21"));
    const { result } = renderHook(() => useDeliveryEstimate("41"));
    // Loading is true from the first render, before the debounce has even
    // elapsed: the request is coming, and a line that showed "not available" for
    // 350ms and then a date would flicker a refusal the server never made.
    expect(result.current.loading).toBe(true);
    expect(result.current.answer).toBeNull();
    await settle();
    expect(result.current.loading).toBe(false);
    expect(result.current.answer?.delivery.earliest).toBe("2026-03-16");
  });

  it("collapses a burst of quantity changes into one request", async () => {
    // Five taps on `+`. §18's rule is about product cards, but the mechanism is
    // the same: the supplier gets asked once for the parcel the buyer settled on.
    mockFetch.mockResolvedValue(windowFor("2026-03-16", "2026-03-21"));
    const { rerender } = renderHook(
      ({ quantity }: { quantity: number }) =>
        useDeliveryEstimate("41", { quantity, debounceMs: 350 }),
      { initialProps: { quantity: 1 } }
    );
    for (const quantity of [2, 3, 4, 5]) {
      await act(async () => {
        jest.advanceTimersByTime(40);
        rerender({ quantity });
      });
    }
    expect(mockFetch).not.toHaveBeenCalled();
    await settle(350);
    expect(mockFetch).toHaveBeenCalledTimes(1);
    expect(mockFetch.mock.calls[0][0]).toMatchObject({ variantRef: "41", quantity: 5 });
  });

  it("discards an answer that arrives after a newer request", async () => {
    // The race that produces a wrong promise. Deliberately resolved out of
    // order: the first request answers *last*, which is exactly what a slow
    // corridor behind a fast one looks like.
    let releaseFirst: (value: DeliveryAnswer) => void = () => {};
    const first = new Promise<DeliveryAnswer>((resolve) => {
      releaseFirst = resolve;
    });
    mockFetch
      .mockReturnValueOnce(first)
      .mockResolvedValueOnce(windowFor("2026-04-01", "2026-04-06"));

    const { result, rerender } = renderHook(
      ({ quantity }: { quantity: number }) => useDeliveryEstimate("41", { quantity }),
      { initialProps: { quantity: 1 } }
    );
    await settle();
    expect(mockFetch).toHaveBeenCalledTimes(1);

    await act(async () => {
      rerender({ quantity: 2 });
    });
    await settle();
    expect(result.current.answer?.delivery.earliest).toBe("2026-04-01");

    // Now the stale one lands. It must change nothing — not the answer, and not
    // `loading`, which the newer request has already cleared.
    await act(async () => {
      releaseFirst(windowFor("2026-03-16", "2026-03-21"));
    });
    expect(result.current.answer?.delivery.earliest).toBe("2026-04-01");
    expect(result.current.loading).toBe(false);
  });

  it("clears the previous answer while a new one is in flight", async () => {
    // An estimate for the old quantity sitting under a spinner reads as the
    // answer to the new question.
    mockFetch.mockResolvedValue(windowFor("2026-03-16", "2026-03-21"));
    const { result, rerender } = renderHook(
      ({ quantity }: { quantity: number }) => useDeliveryEstimate("41", { quantity }),
      { initialProps: { quantity: 1 } }
    );
    await settle();
    expect(result.current.answer).not.toBeNull();

    mockFetch.mockReturnValue(new Promise(() => {}));
    await act(async () => {
      rerender({ quantity: 2 });
    });
    expect(result.current.answer).toBeNull();
    expect(result.current.loading).toBe(true);
  });

  it("asks again when told to, and only once per call", async () => {
    mockFetch.mockResolvedValue(windowFor("2026-03-16", "2026-03-21"));
    const { result } = renderHook(() => useDeliveryEstimate("41"));
    await settle();
    expect(mockFetch).toHaveBeenCalledTimes(1);
    await act(async () => {
      result.current.retry();
    });
    await settle();
    expect(mockFetch).toHaveBeenCalledTimes(2);
  });

  it("stops listening to a request whose inputs have already changed", async () => {
    // The cleanup bumps the sequence rather than only clearing the timer, so a
    // request in flight is disowned the moment its inputs change — not merely when
    // the *replacement* request registers a higher number.
    //
    // There is no test here for "resolved after unmount". Under React 19 a state
    // update on an unmounted tree is a silent no-op and the old console warning is
    // gone, so such a test passes whether the guard exists or not: it would be
    // green decoration over an untested branch. What this asserts instead is the
    // observable half — that the disowned answer never becomes the rendered one.
    let releaseFirst: (value: DeliveryAnswer) => void = () => {};
    mockFetch.mockReturnValueOnce(
      new Promise<DeliveryAnswer>((resolve) => {
        releaseFirst = resolve;
      })
    );
    mockFetch.mockResolvedValueOnce(windowFor("2026-04-01", "2026-04-06"));

    const { result, rerender } = renderHook(
      ({ country }: { country: string }) => useDeliveryEstimate("41", { country }),
      { initialProps: { country: "US" } }
    );
    await settle();
    await act(async () => {
      rerender({ country: "GB" });
    });
    await settle();
    await act(async () => {
      releaseFirst(windowFor("2026-03-16", "2026-03-21"));
    });
    // The US window never appears, in either order of arrival.
    expect(result.current.answer?.delivery.earliest).toBe("2026-04-01");
  });

  it("never starts a request for a listing that unmounted during the debounce", async () => {
    // The debounce window is a second place to leak a supplier call: the screen
    // is gone before the timer fires, and the request would be pure waste.
    mockFetch.mockResolvedValue(windowFor("2026-03-16", "2026-03-21"));
    const { unmount } = renderHook(() => useDeliveryEstimate("41", { debounceMs: 350 }));
    unmount();
    await settle(350);
    expect(mockFetch).not.toHaveBeenCalled();
  });
});
