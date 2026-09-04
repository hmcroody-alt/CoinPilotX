/**
 * The UNDX intelligence row on Asset Detail, and the dead tap it used to be.
 *
 * ## What actually broke
 *
 * The press always fired. The request was always made, with the right symbol.
 * What failed was everything after: the fetch effect listed `loading` in its
 * dependency array *and* called `setLoading(true)` in its body, so starting the
 * request immediately invalidated the deps, React tore down the run that had
 * just begun, and the teardown flipped the `active` flag on a request that was
 * still in the air. `.then` no-opped, `.finally` no-opped, `loading` never
 * cleared. The panel sat on an ActivityIndicator forever.
 *
 * That is why this reads as "tapping does nothing" rather than as a spinner
 * bug: the row is replaced by a box of near-identical height whose only content
 * is a small spinner in the panel's own accent colour. Nothing arrives, and the
 * thing you tapped is gone. So the assertions here are deliberately about the
 * *verdict reaching the screen*, not about the handler being called — a test
 * that only checked `onPress` fired would have passed against the broken build.
 *
 * ## Why the fixture goes through the real normalizer
 *
 * `AssetIntelligenceDetail` is a large nested type and a hand-built literal
 * would encode this test's idea of the shape rather than the API layer's. The
 * fixture is a minimal server-shaped payload pushed through the real
 * `normalizeIntelligenceDetail`, so if the contract moves, these tests move
 * with it instead of quietly testing a shape nobody ships.
 *
 * The verdict label carries the symbol (`BTC ACCUMULATE`) so that "the panel
 * opened" and "the panel opened *for the asset in view*" are one assertion.
 * Hardcoding BTC in the component would fail the ETH cases rather than silently
 * passing them.
 */

const mockGetAssetIntelligence = jest.fn();

jest.mock("../../../api/marketIntelligence", () => {
  const actual = jest.requireActual("../../../api/marketIntelligence");
  return {
    ...actual,
    getAssetIntelligence: (...args: unknown[]) => mockGetAssetIntelligence(...args)
  };
});

import { Pressable, Text } from "react-native";
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react-native";

import { normalizeIntelligenceDetail } from "../../../api/marketIntelligence";
import { AssetIntelligencePanel } from "../AssetIntelligencePanel";

const HINT = "Tap to analyse ›";
const TITLE = "UNDX intelligence";

function detailFor(symbol: string) {
  return normalizeIntelligenceDetail({
    ok: true,
    symbol,
    action: { label: `${symbol} ACCUMULATE`, tone: "POSITIVE", perspective: "non_holder" },
    opportunity: { score: 71, band: "STRONG", confidence: "KNOWN" },
    entry: { score: 44, band: "FAIR", confidence: "KNOWN" },
    risk: { level: "MEDIUM", confidence: "KNOWN" },
    disclaimer: "Nothing here is financial advice."
  });
}

/**
 * A promise the test resolves by hand, for asserting on the in-flight frame.
 */
function deferred<T>() {
  let settle: (value: T) => void = () => undefined;
  const promise = new Promise<T>((resolve) => {
    settle = resolve;
  });
  return { promise, settle };
}

// RNTL's first render pays a one-off cost for host-component detection that is
// otherwise billed to whichever test runs first, which under CI load is enough
// to push that test alone past its timeout. Warm it here instead.
render(<Text>warm</Text>);

beforeEach(() => {
  mockGetAssetIntelligence.mockReset();
  mockGetAssetIntelligence.mockResolvedValue(detailFor("BTC"));
});

describe("The UNDX intelligence row opens the analysis", () => {
  it("opens the verdict when the hint is tapped", async () => {
    // The regression. Against the self-cancelling effect this hung on the
    // spinner and the verdict never arrived.
    render(<AssetIntelligencePanel symbol="BTC" />);
    fireEvent.press(screen.getByText(HINT));
    expect(await screen.findByText("BTC ACCUMULATE")).toBeTruthy();
  });

  it("opens from anywhere on the row, not only the hint", async () => {
    // The row is one Pressable spanning title and hint with space between them.
    // Pressing the title has to reach the same handler, or the tappable area is
    // the eight characters somebody happened to aim at.
    render(<AssetIntelligencePanel symbol="BTC" />);
    fireEvent.press(screen.getByText(TITLE));
    expect(await screen.findByText("BTC ACCUMULATE")).toBeTruthy();
  });

  it("opens on the first tap", async () => {
    render(<AssetIntelligencePanel symbol="BTC" />);
    fireEvent.press(screen.getByText(HINT));
    await waitFor(() => expect(mockGetAssetIntelligence).toHaveBeenCalledTimes(1));
    expect(await screen.findByText("BTC ACCUMULATE")).toBeTruthy();
    expect(screen.queryByText(HINT)).toBeNull();
  });
});

describe("The analysis is for the asset in view", () => {
  it.each([
    ["BTC", "BTC ACCUMULATE"],
    ["ETH", "ETH ACCUMULATE"],
    ["SOL", "SOL ACCUMULATE"]
  ])("asks for %s and renders its verdict", async (symbol, verdict) => {
    // BTC must never be baked in. An ETH screen that requests BTC is the worst
    // failure available here: it is not blank, it is confidently wrong.
    mockGetAssetIntelligence.mockResolvedValue(detailFor(symbol));
    render(<AssetIntelligencePanel symbol={symbol} />);
    fireEvent.press(screen.getByText(HINT));
    expect(await screen.findByText(verdict)).toBeTruthy();
    expect(mockGetAssetIntelligence).toHaveBeenCalledWith(symbol);
  });

  it("drops a previous asset's verdict when the symbol changes", async () => {
    // Re-keying the same mounted panel must not leave BTC's analysis on screen
    // above Ethereum's price.
    mockGetAssetIntelligence.mockResolvedValue(detailFor("BTC"));
    const view = render(<AssetIntelligencePanel symbol="BTC" />);
    fireEvent.press(screen.getByText(HINT));
    expect(await screen.findByText("BTC ACCUMULATE")).toBeTruthy();

    mockGetAssetIntelligence.mockResolvedValue(detailFor("ETH"));
    view.rerender(<AssetIntelligencePanel symbol="ETH" />);

    expect(screen.queryByText("BTC ACCUMULATE")).toBeNull();
    fireEvent.press(await screen.findByText(HINT));
    expect(await screen.findByText("ETH ACCUMULATE")).toBeTruthy();
    expect(mockGetAssetIntelligence).toHaveBeenLastCalledWith("ETH");
  });
});

describe("One tap is one request", () => {
  it("does not fire a second request when tapped twice in flight", async () => {
    const gate = deferred<ReturnType<typeof detailFor>>();
    mockGetAssetIntelligence.mockReturnValue(gate.promise);

    render(<AssetIntelligencePanel symbol="BTC" />);
    const row = screen.getByText(HINT);
    fireEvent.press(row);
    fireEvent.press(row);

    await waitFor(() => expect(mockGetAssetIntelligence).toHaveBeenCalledTimes(1));
    await act(async () => {
      gate.settle(detailFor("BTC"));
      await gate.promise;
    });
    expect(await screen.findByText("BTC ACCUMULATE")).toBeTruthy();
    expect(mockGetAssetIntelligence).toHaveBeenCalledTimes(1);
  });

  it("reuses the loaded verdict after hide and reopen", async () => {
    // The panel holds its payload for the life of the screen. Reopening must
    // not spend a second request on an answer it already has.
    render(<AssetIntelligencePanel symbol="BTC" />);
    fireEvent.press(screen.getByText(HINT));
    expect(await screen.findByText("BTC ACCUMULATE")).toBeTruthy();

    fireEvent.press(screen.getByLabelText("Hide intelligence"));
    fireEvent.press(await screen.findByText(HINT));

    expect(await screen.findByText("BTC ACCUMULATE")).toBeTruthy();
    expect(mockGetAssetIntelligence).toHaveBeenCalledTimes(1);
  });

  it("does not hand the press to the surrounding screen", async () => {
    // The quick-action bar sits directly above this row. A press that also ran
    // a parent handler would fire an unrelated action on every analyse tap.
    const onParentPress = jest.fn();
    render(
      <Pressable accessibilityRole="button" onPress={onParentPress}>
        <AssetIntelligencePanel symbol="BTC" />
      </Pressable>
    );
    fireEvent.press(screen.getByText(HINT));
    expect(await screen.findByText("BTC ACCUMULATE")).toBeTruthy();
    expect(onParentPress).not.toHaveBeenCalled();
  });
});

describe("States other than a verdict", () => {
  it("shows a loading state while the request is in flight", async () => {
    const gate = deferred<ReturnType<typeof detailFor>>();
    mockGetAssetIntelligence.mockReturnValue(gate.promise);

    render(<AssetIntelligencePanel symbol="BTC" />);
    fireEvent.press(screen.getByText(HINT));

    // Named after the asset, so the in-flight frame cannot be mistaken for a
    // generic screen-level spinner.
    expect(await screen.findByLabelText("Loading UNDX intelligence for BTC")).toBeTruthy();
    // The pre-request frame must not read as failure.
    expect(screen.queryByText(/unavailable for this asset/i)).toBeNull();

    await act(async () => {
      gate.settle(detailFor("BTC"));
      await gate.promise;
    });
    expect(await screen.findByText("BTC ACCUMULATE")).toBeTruthy();
  });

  it("shows the unavailable state when the analysis has nothing", async () => {
    mockGetAssetIntelligence.mockResolvedValue(null);
    render(<AssetIntelligencePanel symbol="BTC" />);
    fireEvent.press(screen.getByText(HINT));
    expect(await screen.findByText(/unavailable for this asset/i)).toBeTruthy();
  });

  it("surfaces a failed request rather than spinning on it", async () => {
    // The old defect's signature was an indicator that never resolved. An
    // error has to terminate the loading state, not extend it.
    mockGetAssetIntelligence.mockRejectedValue(new Error("Intelligence is offline."));
    render(<AssetIntelligencePanel symbol="BTC" />);
    fireEvent.press(screen.getByText(HINT));
    expect(await screen.findByText("Intelligence is offline.")).toBeTruthy();
    expect(screen.queryByLabelText("Loading UNDX intelligence for BTC")).toBeNull();
  });

  it.each([
    ["unavailable", null, /unavailable for this asset/i],
    ["failed", new Error("Intelligence is offline."), /offline/i]
  ])("can be dismissed from the %s state", async (_label, outcome, pattern) => {
    // Hide used to exist only once a verdict had arrived, so opening the panel
    // on an asset the analysis cannot cover was a one-way trip.
    if (outcome instanceof Error) mockGetAssetIntelligence.mockRejectedValue(outcome);
    else mockGetAssetIntelligence.mockResolvedValue(outcome);

    render(<AssetIntelligencePanel symbol="BTC" />);
    fireEvent.press(screen.getByText(HINT));
    expect(await screen.findByText(pattern)).toBeTruthy();

    fireEvent.press(screen.getByLabelText("Hide intelligence"));
    expect(await screen.findByText(HINT)).toBeTruthy();
  });

  it("retries when reopened after a failure", async () => {
    mockGetAssetIntelligence.mockRejectedValueOnce(new Error("Intelligence is offline."));
    render(<AssetIntelligencePanel symbol="BTC" />);
    fireEvent.press(screen.getByText(HINT));
    expect(await screen.findByText("Intelligence is offline.")).toBeTruthy();

    mockGetAssetIntelligence.mockResolvedValue(detailFor("BTC"));
    fireEvent.press(screen.getByLabelText("Hide intelligence"));
    fireEvent.press(await screen.findByText(HINT));
    expect(await screen.findByText("BTC ACCUMULATE")).toBeTruthy();
  });
});
