/**
 * Does the alert form offer only what the alert engine can actually decide?
 *
 * The defect this file exists to catch has no error message. `WINDOW_CHOICES`
 * says what the rule vocabulary can *express*; `coverage()` says what has
 * actually been *observed*. A form that renders the first as though it were the
 * second happily sells a 24h window to an asset with forty minutes of history —
 * the rule saves, looks healthy in the list, and can never fire. Nothing on
 * screen says so.
 *
 * So the assertions here are rendered ones. Checking that the screen called
 * `getCryptoAlertOptions` would pass just as happily if it ignored the answer,
 * which is the failure worth guarding. What matters is which buttons a member
 * can see and press, and what reaches the one canonical create call when they
 * do.
 *
 * Only the network edge of the alerts module is mocked. The form state, the
 * window-invalidation effect, and the validation under test are the real ones.
 */
import React from "react";
import { render, screen, fireEvent, act, waitFor } from "@testing-library/react-native";

const mockGetCryptoAlertOptions = jest.fn();

jest.mock("../../api/alerts", () => ({
  ...jest.requireActual("../../api/alerts"),
  getAlertManagementState: jest.fn().mockResolvedValue({
    alerts: [],
    cryptoAlerts: [],
    events: [],
    channel_readiness: {},
    worker: {}
  }),
  loadCachedAlertManagementState: jest.fn().mockResolvedValue(null),
  getAlertChannelReadiness: jest.fn().mockResolvedValue({}),
  getCryptoAlertHistory: jest.fn().mockResolvedValue({ events: [] }),
  loadCachedCryptoAlertHistory: jest.fn().mockResolvedValue(null),
  getCryptoAlertOptions: (...args: unknown[]) => mockGetCryptoAlertOptions(...args),
  createCryptoAlert: jest.fn().mockResolvedValue({ ok: true, alert_id: 1, message: "Alert created." })
}));

import {
  createCryptoAlert,
  normalizeAlertOptions,
  type AlertOptions,
  type AlertWatchlistOption
} from "../../api/alerts";
import { activateLocale } from "../../i18n/engine";
import { AlertManagementScreen } from "../AlertManagementScreen";

type ScreenProps = React.ComponentProps<typeof AlertManagementScreen>;

// This screen's copy lives in the `premium` namespace, which is extended-tier and
// so is not in memory by default. Loading the real English catalog keeps the
// assertions below written in the words a member actually reads; without it every
// label degrades to a humanized key and the queries silently stop matching.
beforeAll(async () => {
  await activateLocale("en");
});

/**
 * An options payload shaped exactly as the endpoint builds one, run through the
 * real normalizer so a test can never assert against a shape the client would
 * reject in production.
 */
function optionsFor({
  windows = [15, 30, 60],
  locked = false,
  windowMessage = "",
  watchlists = []
}: {
  windows?: number[];
  locked?: boolean;
  windowMessage?: string;
  watchlists?: Partial<AlertWatchlistOption>[];
} = {}): AlertOptions {
  return normalizeAlertOptions({
    ok: true,
    premium: !locked,
    capability: "premium.crypto.advanced_alerts",
    basic: {
      conditions: ["above", "below", "moves_up_percent", "moves_down_percent", "volatility_above"],
      locked: false
    },
    advanced: {
      locked,
      logic_modes: ["and", "or"],
      max_clauses: 4,
      max_watchlist_symbols: 25,
      metrics: [
        { key: "price", label: "price", percent: false, windowable: true },
        { key: "change_24h", label: "24h change", percent: true, windowable: false },
        { key: "volume_24h", label: "24h volume", percent: false, windowable: true }
      ],
      comparators: [
        { key: "above", kind: "level" },
        { key: "below", kind: "level" },
        { key: "crosses_above", kind: "crossing" },
        { key: "crosses_below", kind: "crossing" }
      ],
      window_comparators: ["above", "below"]
    },
    windows: windows.map((minutes) => ({
      minutes,
      label: minutes % 60 === 0 ? `${minutes / 60}h` : `${minutes}m`
    })),
    window_coverage: [],
    window_reason: windows.length ? "" : "too_new",
    window_limited_by: windows.length ? "" : "BTC",
    window_message: windowMessage,
    watchlists: watchlists as AlertWatchlistOption[]
  });
}

/**
 * A `getCryptoAlertOptions` whose answer this file releases by hand.
 *
 * The screen debounces the request by 400ms, so "asked but not yet answered" is
 * a real state the form sits in, and the entitlement case below is about
 * precisely that window. Waiting for the window to open and shut again means
 * racing a real timer plus two renders against `waitFor`'s budget, which is
 * what made that case fail on a loaded machine. Holding the promise makes both
 * edges explicit instead: `asked()` settles once the debounced call has
 * actually gone out, and `answer()` is the moment the reply lands.
 */
function heldOptions() {
  const pending: Array<(options: AlertOptions) => void> = [];
  let announce: (() => void) | null = null;
  mockGetCryptoAlertOptions.mockImplementation(
    () =>
      new Promise<AlertOptions>((resolve) => {
        pending.push(resolve);
        announce?.();
      })
  );
  return {
    asked: () =>
      new Promise<void>((resolve) => {
        if (pending.length) {
          resolve();
          return;
        }
        announce = resolve;
      }),
    // Answers every outstanding request; the screen discards the replies to any
    // it has already superseded.
    answer: (options: AlertOptions) => pending.splice(0).forEach((resolve) => resolve(options))
  };
}

function renderScreen(params: Record<string, unknown> | undefined = undefined) {
  const props = {
    route: { params, key: "advanced-alert-test", name: "AlertManagement" },
    navigation: { navigate: jest.fn(), setOptions: jest.fn(), goBack: jest.fn(), addListener: jest.fn(() => jest.fn()) }
  } as unknown as ScreenProps;
  return render(<AlertManagementScreen {...props} />);
}

/**
 * Open the compound-rule builder, once the options answer has actually arrived.
 *
 * The wait is not incidental. The request is debounced, so for the first few
 * hundred milliseconds the screen knows nothing — no metrics, no windows, no
 * entitlement. Acting inside that gap tests the unanswered state rather than the
 * behaviour under test, so every case here waits for a metric button that only
 * the server's vocabulary can produce.
 */
async function openAdvanced() {
  fireEvent.press(await screen.findByRole("button", { name: "Advanced" }));
  await screen.findByText("Condition 1");
  return screen.findByRole("button", { name: "24h change" });
}

beforeEach(() => {
  jest.clearAllMocks();
  mockGetCryptoAlertOptions.mockResolvedValue(optionsFor());
});

describe("The form offers only the windows the series can answer", () => {
  it("shows exactly the offered windows and nothing from the wider vocabulary", async () => {
    // 15/30/60 were observed. 2h, 4h, 24h are all valid *rule vocabulary* and
    // must not appear: an asset sampled for an hour cannot answer any of them.
    mockGetCryptoAlertOptions.mockResolvedValue(optionsFor({ windows: [15, 30, 60] }));
    renderScreen();
    await openAdvanced();

    expect(await screen.findByRole("button", { name: "15m" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "30m" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "1h" })).toBeTruthy();
    expect(screen.queryByRole("button", { name: "4h" })).toBeNull();
    expect(screen.queryByRole("button", { name: "24h" })).toBeNull();
  });

  it("offers no window at all when nothing has been sampled long enough, and says why", async () => {
    // The honest answer is a sentence, not an empty row of buttons the member
    // is left to interpret.
    mockGetCryptoAlertOptions.mockResolvedValue(optionsFor({
      windows: [],
      windowMessage: "BTC has not been sampled long enough yet for any time window."
    }));
    renderScreen();
    await openAdvanced();

    expect(await screen.findByText("BTC has not been sampled long enough yet for any time window.")).toBeTruthy();
    expect(screen.queryByRole("button", { name: "No window" })).toBeNull();
    expect(screen.queryByRole("button", { name: "15m" })).toBeNull();
  });

  it("hides the window row for a metric that cannot carry one", async () => {
    // A 24h change is already a window. Offering to measure it over another one
    // would be offering a rule the server refuses.
    renderScreen();
    await openAdvanced();
    expect(await screen.findByRole("button", { name: "1h" })).toBeTruthy();

    fireEvent.press(screen.getByRole("button", { name: "24h change" }));

    await waitFor(() => expect(screen.queryByRole("button", { name: "1h" })).toBeNull());
    expect(screen.queryByRole("button", { name: "No window" })).toBeNull();
  });

  it("clears a window the next asset cannot answer instead of saving a rule that never fires", async () => {
    // The whole defect class in one test. BTC can answer 1h; the member picks
    // it; they then retarget the rule at an asset with fifteen minutes of
    // history. Left alone, the 1h clause saves and is undecidable forever.
    mockGetCryptoAlertOptions.mockResolvedValue(optionsFor({ windows: [15, 30, 60] }));
    renderScreen();
    await openAdvanced();
    fireEvent.press(await screen.findByRole("button", { name: "1h" }));

    mockGetCryptoAlertOptions.mockResolvedValue(optionsFor({
      windows: [15],
      windowMessage: "NEW has not been sampled long enough yet for any time window."
    }));
    await act(async () => {
      fireEvent.changeText(screen.getByLabelText("Alert symbol"), "NEW");
    });

    // Wait on the notice rather than on the button disappearing: it is raised by
    // the invalidation itself, so seeing it proves the new coverage answer landed
    // and was acted on. The budget is generous because the request is debounced
    // by 400ms and the library's default is only a second.
    expect(await screen.findByText(
      "NEW has not been sampled long enough yet for any time window.", {}, { timeout: 4000 }
    )).toBeTruthy();
    expect(screen.queryByRole("button", { name: "1h" })).toBeNull();
    // Cleared, not merely hidden: the selection falls back to "No window", so the
    // rule that gets saved is the one the member can now see.
    expect(screen.getByRole("button", { name: "No window" }).props.accessibilityState.selected).toBe(true);
  });
});

describe("A window and a crossing cannot be combined", () => {
  it("drops the crossing comparators once a window is chosen", async () => {
    // A window's baseline advances with every sample, so a crossing over one
    // fires on the baseline moving rather than on the market moving. The server
    // refuses that pairing; offering it here would earn the member a 400.
    renderScreen();
    await openAdvanced();
    expect(await screen.findByRole("button", { name: "Crosses above" })).toBeTruthy();

    fireEvent.press(screen.getByRole("button", { name: "1h" }));

    await waitFor(() => expect(screen.queryByRole("button", { name: "Crosses above" })).toBeNull());
    expect(screen.queryByRole("button", { name: "Crosses below" })).toBeNull();
    expect(screen.getByRole("button", { name: "Above" })).toBeTruthy();
  });

  it("moves an already-chosen crossing to a level test rather than leaving it stranded", async () => {
    // Otherwise the clause holds a comparator that no longer appears among its
    // own buttons, and nothing on screen shows what the rule now says.
    renderScreen();
    await openAdvanced();
    fireEvent.press(await screen.findByRole("button", { name: "Crosses below" }));
    fireEvent.press(screen.getByRole("button", { name: "1h" }));

    await waitFor(() => expect(screen.getByRole("button", { name: "Above" }).props.accessibilityState.selected).toBe(true));
  });
});

describe("The compound rule reaches the one canonical create call", () => {
  it("sends the clauses, the logic mode and the chosen window", async () => {
    renderScreen();
    await openAdvanced();
    fireEvent.press(await screen.findByRole("button", { name: "Any of these" }));
    fireEvent.changeText(screen.getByLabelText("Condition 1 value"), "61000");
    fireEvent.press(screen.getByRole("button", { name: "1h" }));

    await act(async () => {
      fireEvent.press(screen.getByRole("button", { name: "Create alert" }));
    });

    expect(createCryptoAlert).toHaveBeenCalledWith(expect.objectContaining({
      mode: "advanced",
      logic: "or",
      assetSymbol: "BTC",
      clauses: [{ metric: "price", comparator: "above", value: "61000", windowMinutes: 60 }]
    }));
  });

  it("refuses a clause with no value instead of posting an empty threshold", async () => {
    renderScreen();
    await openAdvanced();

    await act(async () => {
      fireEvent.press(screen.getByRole("button", { name: "Create alert" }));
    });

    expect(await screen.findByText("Condition 1: add a value.")).toBeTruthy();
    expect(createCryptoAlert).not.toHaveBeenCalled();
  });

  it("refuses a negative price while allowing a negative percentage", async () => {
    // A price below zero would arm forever; a 24h change below zero is half of
    // all days. One validation rule cannot cover both, so the metric decides.
    renderScreen();
    await openAdvanced();
    fireEvent.changeText(await screen.findByLabelText("Condition 1 value"), "-5");

    await act(async () => {
      fireEvent.press(screen.getByRole("button", { name: "Create alert" }));
    });
    expect(await screen.findByText("Condition 1: price must be greater than zero.")).toBeTruthy();

    fireEvent.press(screen.getByRole("button", { name: "24h change" }));
    await act(async () => {
      fireEvent.press(screen.getByRole("button", { name: "Create alert" }));
    });
    expect(createCryptoAlert).toHaveBeenCalledWith(expect.objectContaining({
      clauses: [{ metric: "change_24h", comparator: "above", value: "-5", windowMinutes: 0 }]
    }));
  });

  it("stops offering another condition once the server's limit is reached", async () => {
    mockGetCryptoAlertOptions.mockResolvedValue(normalizeAlertOptions({
      ...optionsFor(),
      advanced: { ...optionsFor().advanced, max_clauses: 2 }
    }));
    renderScreen();
    await openAdvanced();

    fireEvent.press(await screen.findByRole("button", { name: "Add condition" }));
    await waitFor(() => expect(screen.getByText("Condition 2")).toBeTruthy());
    expect(screen.queryByRole("button", { name: "Add condition" })).toBeNull();
    expect(screen.getByText("One alert can hold up to 2 conditions.")).toBeTruthy();
  });
});

describe("A free account sees what Premium adds, and cannot use it", () => {
  it("will not open the advanced builder, and says what it is part of", async () => {
    mockGetCryptoAlertOptions.mockResolvedValue(optionsFor({ locked: true }));
    renderScreen();
    // Wait for the entitlement answer: the hint only renders once it is known.
    await screen.findByText("Advanced alerts — several conditions in one rule, whole-watchlist rules, and time windows — are part of PulseSoc Premium.");

    fireEvent.press(screen.getByRole("button", { name: "Advanced" }));

    expect(await screen.findByText("Multi-condition alerts, watchlist alerts and time windows are part of PulseSoc Premium.")).toBeTruthy();
    expect(screen.queryByText("Condition 1")).toBeNull();
  });

  it("closes the builder again if the entitlement answer arrives after the tap", async () => {
    // The request is debounced, so Advanced is pressable before the account is
    // known. Landing in a builder the server refuses every field of is worse
    // than a moment's delay, so the late answer has to take it back.
    const options = heldOptions();
    renderScreen();
    await screen.findByRole("button", { name: "Advanced" });
    // The tap has to land after the request goes out and before it is answered.
    // That gap is the whole subject here, so it is arranged rather than hoped
    // for: the builder opening below proves the entitlement was still unknown.
    await act(async () => {
      await options.asked();
    });
    fireEvent.press(screen.getByRole("button", { name: "Advanced" }));
    expect(screen.getByText("Condition 1")).toBeTruthy();

    // The late answer, delivered on this test's terms. Everything it sets off —
    // the options state, the entitlement effect, the re-render that drops the
    // builder — settles inside this act.
    await act(async () => {
      options.answer(optionsFor({ locked: true }));
    });

    expect(screen.queryByText("Condition 1")).toBeNull();
    expect(screen.getByText("Multi-condition alerts, watchlist alerts and time windows are part of PulseSoc Premium.")).toBeTruthy();
  });

  it("keeps the free basic alert working exactly as before", async () => {
    // The mission's hard line: nothing here may break the existing free alert.
    mockGetCryptoAlertOptions.mockResolvedValue(optionsFor({ locked: true }));
    renderScreen();
    fireEvent.changeText(await screen.findByLabelText("Alert target value"), "61000");

    await act(async () => {
      fireEvent.press(screen.getByRole("button", { name: "Create alert" }));
    });

    expect(createCryptoAlert).toHaveBeenCalledWith(expect.objectContaining({
      mode: "basic",
      assetSymbol: "BTC",
      condition: "above",
      targetValue: "61000"
    }));
  });

  it("keeps the basic form working when the options call fails outright", async () => {
    // No answer must fail towards the free path, not towards a blocked form.
    mockGetCryptoAlertOptions.mockRejectedValue(new Error("offline"));
    renderScreen();
    fireEvent.changeText(await screen.findByLabelText("Alert target value"), "61000");

    await act(async () => {
      fireEvent.press(screen.getByRole("button", { name: "Create alert" }));
    });

    expect(createCryptoAlert).toHaveBeenCalledWith(expect.objectContaining({ targetValue: "61000" }));
  });
});

describe("A watchlist rule offers only lists creation would accept", () => {
  it("shows an ineligible list with the server's own reason and will not select it", async () => {
    // The reason comes from the very preflight creation runs. Offering a list
    // creation would refuse is worse than offering none: the member picks it,
    // fills the form in, and only then is turned away.
    mockGetCryptoAlertOptions.mockResolvedValue(optionsFor({
      watchlists: [
        { id: 4, name: "Majors", symbols: ["BTC", "ETH"], eligible: true, reason: "", message: "" },
        { id: 9, name: "Empty", symbols: [], eligible: false, reason: "empty_watchlist", message: "That watchlist has no assets in it yet." }
      ]
    }));
    renderScreen();

    fireEvent.press(await screen.findByRole("button", { name: "A watchlist" }));
    expect(await screen.findByText("That watchlist has no assets in it yet.")).toBeTruthy();

    // The eligible list was chosen for them; pressing the refused one changes nothing.
    fireEvent.press(screen.getByText("Empty"));
    fireEvent.changeText(screen.getByLabelText("Alert target value"), "61000");
    await act(async () => {
      fireEvent.press(screen.getByRole("button", { name: "Create alert" }));
    });
    expect(createCryptoAlert).toHaveBeenCalledWith(expect.objectContaining({ watchlistId: 4 }));
  });

  it("stops asking for a symbol once a list is watched", async () => {
    // A rule is about one asset or one list, never both.
    mockGetCryptoAlertOptions.mockResolvedValue(optionsFor({
      watchlists: [{ id: 4, name: "Majors", symbols: ["BTC", "ETH"], eligible: true, reason: "", message: "" }]
    }));
    renderScreen();

    fireEvent.press(await screen.findByRole("button", { name: "A watchlist" }));

    await waitFor(() => expect(screen.queryByLabelText("Alert symbol")).toBeNull());
  });
});
