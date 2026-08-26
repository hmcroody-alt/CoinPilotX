/**
 * Which endpoint each invite answer actually hits.
 *
 * The screen test mocks this module, so it proves the Decline button calls
 * `declinePageInvite` — it cannot prove `declinePageInvite` doesn't POST to
 * `/invites/accept`. Those two URLs differ by one word and do opposite things,
 * and a transposition there is invisible in review, passes typecheck, and
 * silently joins a team the user just refused.
 *
 * The token is also pinned as a *body* field. Moving it into the path or a
 * query string would put a live credential into request logs and analytics.
 */

const mockPulseApi = jest.fn();

jest.mock("../pulseApi", () => ({
  pulseApi: (...args: unknown[]) => mockPulseApi(...args)
}));

import { acceptPageInvite, declinePageInvite, listMyPageInvites, pageRoleLabel } from "../pages";

beforeEach(() => {
  mockPulseApi.mockReset();
});

const INVITE = {
  token: "tok-abc",
  role: "ADMIN",
  expires_at: "2099-01-01T00:00:00+00:00",
  expired: false,
  invited_at: "2026-01-01T00:00:00+00:00",
  page_id: 41,
  page_name: "Night Signal",
  page_handle: "nightsignal",
  page_avatar_url: "",
  page_type: "ARTIST",
  invited_by_name: "Roody"
};

describe("page invite endpoints", () => {
  it("reads the inbox without asking for a page", async () => {
    // There is no page id to pass: the server scopes this to the caller, and a
    // page-scoped variant would be a token-disclosure endpoint.
    mockPulseApi.mockResolvedValue({ ok: true, invites: [INVITE] });
    const invites = await listMyPageInvites();

    expect(mockPulseApi).toHaveBeenCalledWith("/api/pages/invites");
    expect(invites).toHaveLength(1);
    expect(invites[0].token).toBe("tok-abc");
    expect(invites[0].page_name).toBe("Night Signal");
    expect(invites[0].expired).toBe(false);
  });

  it("reads an empty inbox as no invites, not as a failure", async () => {
    mockPulseApi.mockResolvedValue({ ok: true });
    await expect(listMyPageInvites()).resolves.toEqual([]);
  });

  it("accepts against the accept endpoint", async () => {
    mockPulseApi.mockResolvedValue({ ok: true, membership: { page_id: 41, role: "ADMIN" } });
    await acceptPageInvite("tok-abc");

    expect(mockPulseApi).toHaveBeenCalledWith("/api/pages/invites/accept", {
      method: "POST",
      body: JSON.stringify({ token: "tok-abc" })
    });
  });

  it("declines against the decline endpoint, which is not the accept one", async () => {
    mockPulseApi.mockResolvedValue({ ok: true, page_id: 41, status: "declined" });
    await declinePageInvite("tok-abc");

    const [url, init] = mockPulseApi.mock.calls[0];
    expect(url).toBe("/api/pages/invites/decline");
    expect(url).not.toContain("accept");
    expect(init.method).toBe("POST");
    expect(JSON.parse(init.body)).toEqual({ token: "tok-abc" });
  });

  it("keeps the token out of the URL", async () => {
    // A token in the path or query is a credential in every access log,
    // referrer header and analytics pipeline it passes through.
    mockPulseApi.mockResolvedValue({ ok: true });
    await acceptPageInvite("tok-abc");
    await declinePageInvite("tok-abc");
    for (const [url] of mockPulseApi.mock.calls) {
      expect(String(url)).not.toContain("tok-abc");
    }
  });
});

describe("pageRoleLabel", () => {
  it("reads as a job, not as a product feature", async () => {
    expect(pageRoleLabel("CONTENT_MANAGER")).toBe("Content manager");
    expect(pageRoleLabel("ADMIN")).toBe("Admin");
  });

  it("renders a role the server adds without a client change", async () => {
    // Derived rather than mapped, so a new role is words rather than
    // SOME_NEW_ROLE shouted at the user.
    expect(pageRoleLabel("SOME_NEW_ROLE")).toBe("Some new role");
  });

  it("does not render the word undefined", async () => {
    expect(pageRoleLabel(undefined)).toBe("");
  });
});
