/**
 * The management view's wire shape, pinned.
 *
 * `/api/pages/:id/manage` is built server-side as `public_view(...)` with the
 * management fields merged into that same dict, so `role`, `capabilities`,
 * `links`, `members`, `analytics` and `completeness` come back INSIDE `page`
 * rather than beside it. The client read them off the top level, where every
 * one of them is `undefined`.
 *
 * Nothing threw. `capabilities` fell back to `[]` and `role === "OWNER"`
 * evaluated false, so the Presence hub simply hid the owner status controls,
 * the verification request, the analytics card, the completeness meter, the
 * team list, and the Advertising / Marketplace / Payments entries — for the
 * actual owner of the page. A permission bug that fails closed is invisible
 * until someone asks why the product looks empty.
 *
 * The fixture below is a transcription of a real response, taken by calling
 * `pulsesoc_pages.manage_view()` against a freshly created ARTIST page, so the
 * nesting under test is the server's genuine layout and not an assumption
 * about it.
 */

const mockPulseApi = jest.fn();

jest.mock("../pulseApi", () => ({
  pulseApi: (...args: unknown[]) => mockPulseApi(...args)
}));

import { getPageManageView } from "../pages";

beforeEach(() => {
  mockPulseApi.mockReset();
});

/** A page row as the server serializes it, management fields merged in. */
const OWNER_RESPONSE = {
  ok: true,
  page: {
    id: 41,
    page_type: "ARTIST",
    category: "",
    subcategory: "",
    name: "Probe Artist",
    handle: "probeartist",
    avatar_url: "",
    cover_url: "",
    description: "",
    genre: "",
    website: "",
    email: "",
    location: "",
    hours: {},
    status: "ACTIVE",
    verification_status: "unverified",
    verified: false,
    followers_count: 0,
    posts_count: 0,
    videos_count: 0,
    shop_seller_id: 0,
    tabs: ["posts", "about"],
    modules: { music: false, videos: false, merch: false },
    viewer: { role: "OWNER", following: false },
    // Everything from here down is what the client was reading one level too high.
    role: "OWNER",
    capabilities: [
      "create_content",
      "edit_page",
      "manage_ads",
      "manage_links",
      "manage_marketplace",
      "manage_members",
      "manage_status",
      "transfer_ownership",
      "view_analytics"
    ],
    owner_user_id: 7,
    phone: "555-0100",
    links: [{ link_type: "store", ref_id: "88" }],
    members: [{ user_id: 7, role: "OWNER", status: "active" }],
    analytics: { followers: 0, posts: 0, team_members: 1 },
    completeness: { percent: 40, items: [{ key: "avatar", label: "Add a profile picture", done: false }] }
  }
};

describe("getPageManageView", () => {
  it("reads the management fields the server nests inside the page", async () => {
    mockPulseApi.mockResolvedValue(OWNER_RESPONSE);
    const view = await getPageManageView(41);

    expect(mockPulseApi).toHaveBeenCalledWith("/api/pages/41/manage");
    expect(view.role).toBe("OWNER");
    expect(view.capabilities).toContain("edit_page");
    expect(view.capabilities).toContain("manage_marketplace");
    expect(view.owner_user_id).toBe(7);
    expect(view.phone).toBe("555-0100");
    expect(view.links).toEqual([{ link_type: "store", ref_id: "88" }]);
    expect(view.members).toHaveLength(1);
    expect(view.analytics?.team_members).toBe(1);
    expect(view.completeness?.percent).toBe(40);
  });

  /**
   * The two reads that decide what an owner is allowed to see. Before the fix
   * both of these were false/empty for every caller, including OWNER.
   */
  it("lets an owner through the checks the hub gates its controls on", async () => {
    mockPulseApi.mockResolvedValue(OWNER_RESPONSE);
    const view = await getPageManageView(41);

    expect(view.role === "OWNER").toBe(true);
    expect(new Set(view.capabilities).has("manage_ads")).toBe(true);
  });

  it("keeps the public page fields separate from the management ones", async () => {
    mockPulseApi.mockResolvedValue(OWNER_RESPONSE);
    const view = await getPageManageView(41);

    expect(view.page.id).toBe(41);
    expect(view.page.handle).toBe("probeartist");
    expect(view.page.tabs).toEqual(["posts", "about"]);
    // A management field left on `page` would leak private data into anything
    // that renders a `PulsePage`, which is the whole reason the two are typed
    // apart.
    expect(view.page).not.toHaveProperty("capabilities");
    expect(view.page).not.toHaveProperty("members");
    expect(view.page).not.toHaveProperty("analytics");
    expect(view.page).not.toHaveProperty("owner_user_id");
    expect(view.page).not.toHaveProperty("phone");
  });

  /**
   * An ANALYST can read the page but may change nothing. The capability list
   * is the server's answer, and the client must not widen it.
   */
  it("carries a restricted role through without widening it", async () => {
    mockPulseApi.mockResolvedValue({
      ok: true,
      page: { ...OWNER_RESPONSE.page, role: "ANALYST", capabilities: ["view_analytics"], members: [] }
    });
    const view = await getPageManageView(41);

    expect(view.role).toBe("ANALYST");
    expect(view.capabilities).toEqual(["view_analytics"]);
    expect(new Set(view.capabilities).has("edit_page")).toBe(false);
    expect(view.role === "OWNER").toBe(false);
  });

  /**
   * Fail closed. A response missing the management block must grant nothing —
   * not fall back to a role, and not to a capability list that lets a button
   * render.
   */
  it("grants nothing when the server sends no management fields", async () => {
    mockPulseApi.mockResolvedValue({ ok: true, page: { id: 41, name: "Probe Artist", handle: "probeartist" } });
    const view = await getPageManageView(41);

    expect(view.capabilities).toEqual([]);
    expect(view.role === "OWNER").toBe(false);
    expect(view.links).toEqual([]);
    expect(view.owner_user_id).toBe(0);
  });
});
