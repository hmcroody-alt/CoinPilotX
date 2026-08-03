/**
 * Verification is the screen where a wrong control is a lie about somebody's
 * standing. It shipped with one: "Start verification request" rendered
 * unconditionally, so a person already approved was invited to begin the thing
 * they had finished, and a person mid-review could fire a duplicate.
 *
 * `verificationActions` is left real here, and only the network is mocked. The
 * point of these tests is that the screen renders the derivation faithfully —
 * mocking the derivation would let the screen pass while showing the wrong
 * buttons. The derivation's own rules are pinned separately, in
 * `api/__tests__/verificationActions.test.ts`.
 */
import React from "react";
import { render, waitFor } from "@testing-library/react-native";

const mockLoad = jest.fn();
const mockLoadCached = jest.fn();
const mockStartRequest = jest.fn();

jest.mock("../../api/verification", () => ({
  ...jest.requireActual("../../api/verification"),
  loadVerificationState: (...args: unknown[]) => mockLoad(...args),
  loadCachedVerificationState: (...args: unknown[]) => mockLoadCached(...args),
  startVerificationRequest: (...args: unknown[]) => mockStartRequest(...args),
  pickAndUploadVerificationDocument: jest.fn(),
  submitVerificationAppeal: jest.fn()
}));

import { verificationTracks, VerificationStatus, VerificationTrackKey } from "../../api/verification";
import { VerificationCenterScreen } from "../VerificationCenterScreen";

function stateFor(status: VerificationStatus, overrides: { requestId?: number; verificationType?: VerificationTrackKey } = {}) {
  return {
    status,
    score: 55,
    requestId: overrides.requestId ?? 42,
    verificationType: overrides.verificationType ?? ("identity" as VerificationTrackKey),
    // The server's own call-to-action string, kept deliberately wrong so a
    // regression that reinstates it is visible.
    primaryAction: "Continue Verification",
    recommendations: [],
    profilePreview: { displayName: "Ada Okafor", username: "ada", verifiedBadge: status === "approved", verificationStatus: status },
    premiumBadges: { premiumActive: false, founderActive: false, founderNumber: 0, plan: "free" },
    checklist: [],
    tracks: verificationTracks,
    loadedAt: "2026-08-03T09:14:00.000Z"
  };
}

const navigation = { setOptions: jest.fn(), navigate: jest.fn() } as never;

async function renderAt(status: VerificationStatus, overrides?: { requestId?: number; verificationType?: VerificationTrackKey }) {
  mockLoad.mockResolvedValue(stateFor(status, overrides));
  const view = render(<VerificationCenterScreen navigation={navigation} route={{ params: {} } as never} />);
  await waitFor(() => expect(view.queryByText("Loading Verification Center")).toBeNull());
  return view;
}

beforeEach(() => {
  jest.clearAllMocks();
  mockLoadCached.mockResolvedValue(null);
});

describe("VerificationCenterScreen status awareness", () => {
  it("invites someone with no request to start one", async () => {
    const view = await renderAt("not_started", { requestId: 0 });
    expect(view.getByText("Start Identity verification")).toBeTruthy();
  });

  /** The regression, asserted at the screen rather than at the derivation. */
  it("shows an approved person no invitation to start a request", async () => {
    const view = await renderAt("approved");
    expect(view.queryByText("Start verification request")).toBeNull();
    expect(view.queryByText(/^Start /)).toBeNull();
    expect(view.getByText("Update your Identity details")).toBeTruthy();
  });

  /**
   * `primaryAction` comes back from the server saying "Continue Verification"
   * whatever the status is. It used to be rendered directly under the status
   * heading, which is how an approved person was told to continue.
   */
  it("states where the request stands instead of echoing the server's call to action", async () => {
    const view = await renderAt("approved");
    expect(view.queryByText("Continue Verification")).toBeNull();
    expect(view.getByText(/^You are verified\./)).toBeTruthy();
  });

  it("offers nothing to press while a reviewer holds the request", async () => {
    const view = await renderAt("in_review");
    expect(view.queryByText(/^Start /)).toBeNull();
    expect(view.queryByText(/^Send your /)).toBeNull();
    expect(view.getByText("You cannot change or resend this while your request is with a reviewer.")).toBeTruthy();
  });

  /**
   * The document button used to render always and fail with "Start a
   * verification request before uploading private evidence." A control that
   * exists only to apologise is the dead control this work removes.
   */
  it("replaces the document button with the reason there is none", async () => {
    const view = await renderAt("not_started", { requestId: 0 });
    expect(view.queryByText("Choose a document")).toBeNull();
    expect(view.getByText("Send a request first. Once it exists, you can attach documents to it.")).toBeTruthy();
  });

  it("offers the appeal box only against a decision", async () => {
    const undecided = await renderAt("in_review");
    expect(undecided.queryByLabelText("What you would like the review team to reconsider")).toBeNull();
    expect(undecided.getByText("Your request has not been decided yet, so there is nothing to appeal.")).toBeTruthy();

    const refused = await renderAt("rejected");
    expect(refused.getByLabelText("What you would like the review team to reconsider")).toBeTruthy();
    expect(refused.getByText("Submit appeal")).toBeTruthy();
  });

  it("shows a request number worth quoting, and no number before there is one", async () => {
    const started = await renderAt("submitted", { requestId: 42 });
    expect(started.getByText(/Request #42, worth quoting if you contact support/)).toBeTruthy();

    const notStarted = await renderAt("not_started", { requestId: 0 });
    expect(notStarted.queryByText(/Request #/)).toBeNull();
  });
});

/**
 * Tier 0.3's copy class, asserted where a person would actually meet it. The
 * check walks every rendered string on the screen rather than the four lines
 * the review happened to name, so the same vocabulary cannot return through a
 * different one.
 */
describe("VerificationCenterScreen copy", () => {
  const BANNED = /server[- ]?(authoritative|owned|side)|endpoints?\b|\/api\/|\bbackend\b|\bpayloads?\b|\bschemas?\b|native does not/i;

  function renderedText(view: ReturnType<typeof render>): string[] {
    const nodes = view.root.findAll((node: { props?: { children?: unknown } }) => typeof node.props?.children === "string");
    return nodes.map((node: { props: { children: unknown } }) => String(node.props.children));
  }

  it.each(["not_started", "in_review", "needs_more_info", "approved", "rejected", "suspended"] as VerificationStatus[])(
    "names no route, endpoint or owner at status %s",
    async (status) => {
      const view = await renderAt(status, { requestId: status === "not_started" ? 0 : 42 });
      renderedText(view).forEach((line) => expect(line).not.toMatch(BANNED));
    }
  );

  /**
   * The one sentence the verdict record says is worth keeping — that the app
   * does not look at your documents — must survive the rewrite, because
   * deleting a reassurance is a quieter way of failing this than keeping the
   * jargon was.
   */
  it("still promises that the app does not open or keep your documents", async () => {
    const view = await renderAt("needs_more_info");
    expect(view.getByText(/never opens, keeps, or checks your documents/)).toBeTruthy();
  });
});
