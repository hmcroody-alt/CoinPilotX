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
    completeness: { percent: 40, items: [{ key: "avatar", label: "Add a profile picture", done: false }] },
    sections: [
      { key: "overview", label: "Overview", hint: "How this presence is doing", permission: "view_analytics",
        permitted: true, ready: true, setup: "" },
      { key: "posts", label: "Posts", hint: "Publish as this presence", permission: "create_content",
        permitted: true, ready: false, setup: "Write the first post", count: 0 },
      { key: "store", label: "Marketplace", hint: "Sell from this presence", permission: "manage_marketplace",
        permitted: true, ready: true, setup: "" }
    ],
    overview: {
      status: "Live",
      verification: "Not verified",
      metrics: [
        { key: "followers", label: "Followers", value: 0, delta: 0, window: "30 days" },
        { key: "posts", label: "Posts", value: 0, delta: 0, window: "30 days" },
        { key: "team", label: "Team", value: 1 }
      ],
      pending: ["Posts"],
      completeness_percent: 40,
      note: "Reach and engagement are not measured yet."
    }
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
   * `sections` is the entire management surface — the hub renders one tile per
   * entry — and it was added to the server and to the type but never to the
   * destructure above. It stayed inside the rest element, arrived on `page`,
   * and read as `undefined` at `view.sections`, so the hub drew no tiles at
   * all. The hub's own tests mock this module, so they could not see it.
   */
  it("carries the management sections the hub draws its tiles from", async () => {
    mockPulseApi.mockResolvedValue(OWNER_RESPONSE);
    const view = await getPageManageView(41);

    expect(view.sections).toBeDefined();
    expect(view.sections?.map((s) => s.key)).toEqual(["overview", "posts", "store"]);
    const posts = view.sections?.find((s) => s.key === "posts");
    // The three separate facts, none collapsed: may this caller act, is
    // anything behind it, and how much.
    expect(posts?.permitted).toBe(true);
    expect(posts?.ready).toBe(false);
    expect(posts?.setup).toBe("Write the first post");
    expect(posts?.count).toBe(0);
  });

  it("carries the Overview the server measured", async () => {
    mockPulseApi.mockResolvedValue(OWNER_RESPONSE);
    const view = await getPageManageView(41);

    expect(view.overview?.status).toBe("Live");
    expect(view.overview?.verification).toBe("Not verified");
    expect(view.overview?.pending).toEqual(["Posts"]);
    expect(view.overview?.completeness_percent).toBe(40);
    expect(view.overview?.note).toBe("Reach and engagement are not measured yet.");
  });

  /**
   * A measured zero has to survive the trip. Every value in this fixture is 0
   * or absent, which is exactly the shape a `|| fallback` anywhere on the path
   * would quietly rewrite.
   */
  it("keeps a measured zero, and keeps an absent delta absent", async () => {
    mockPulseApi.mockResolvedValue(OWNER_RESPONSE);
    const view = await getPageManageView(41);

    const followers = view.overview?.metrics.find((m) => m.key === "followers");
    expect(followers?.value).toBe(0);
    expect(followers).toHaveProperty("delta", 0);
    expect(followers?.window).toBe("30 days");

    // Nothing records when a member joined, so Team is a total with no window.
    // An invented `delta: 0` here would read as "no one joined this month",
    // which is a claim the server never made.
    const team = view.overview?.metrics.find((m) => m.key === "team");
    expect(team?.value).toBe(1);
    expect(team).not.toHaveProperty("delta");
    expect(team).not.toHaveProperty("window");
  });

  it("keeps the section and overview blocks off the public page object", async () => {
    mockPulseApi.mockResolvedValue(OWNER_RESPONSE);
    const view = await getPageManageView(41);

    // Both describe private management state — pending work, completeness,
    // what the team has not done yet — and `page` is the object handed to
    // anything that renders a `PulsePage`, visitor-facing screens included.
    expect(view.page).not.toHaveProperty("sections");
    expect(view.page).not.toHaveProperty("overview");
  });

  /**
   * An older server sends neither block. The client must show nothing rather
   * than an empty tile list that looks like a decision, or a zeroed Overview
   * that puts numbers nobody measured on screen.
   */
  it("leaves both absent rather than inventing them", async () => {
    mockPulseApi.mockResolvedValue({ ok: true, page: { id: 41, name: "Probe Artist", handle: "probeartist" } });
    const view = await getPageManageView(41);

    expect(view.sections).toBeUndefined();
    expect(view.overview).toBeUndefined();
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
