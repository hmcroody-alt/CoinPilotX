const mockPulseApi = jest.fn<Promise<any>, [string?, RequestInit?]>(async (_path?: string, _options?: RequestInit) => ({ posts: [], next_offset: 0, has_more: false }));

jest.mock("../pulseApi", () => ({
  pulseApi: (path: string, options?: RequestInit) => mockPulseApi(path, options)
}));

import { mutePostAuthor, normalizePost, PulsePost, toggleFollowAuthor } from "../feed";
import { listPublicProfilePosts, profileTargetFromPost } from "../profile";

beforeEach(() => {
  mockPulseApi.mockClear();
});

describe("new-user profile identity", () => {
  it("routes feed authors by canonical user id when no legacy handle exists", () => {
    const post = normalizePost({
      id: 77,
      post_id: 77,
      user_id: 901,
      body: "first signal",
      author: {
        display_name: "Fresh Member",
        avatar_url: "https://cdn.example/avatar.png"
      }
    } as PulsePost);

    const target = profileTargetFromPost(post);

    expect(target?.userId).toBe(901);
    expect(target?.profileKey).toBe("901");
    expect(target?.nativePath).toBe("/pulse/id/901");
  });

  it("uses numeric canonical profile keys for profile post retrieval", async () => {
    await listPublicProfilePosts({ profileKey: "901" });

    expect(mockPulseApi).toHaveBeenCalledWith(expect.stringContaining("/api/pulse/profile/901/posts?"), undefined);
  });

  it("prefers the resolved user id over display name for future new users", async () => {
    await listPublicProfilePosts({ userId: 902, display_name: "Future User" });

    expect(mockPulseApi).toHaveBeenCalledWith(expect.stringContaining("/api/pulse/profile/902/posts?"), undefined);
  });

  it("falls back to the deployed feed profile contract with canonical user id when the profile posts route is empty", async () => {
    mockPulseApi
      .mockResolvedValueOnce({ posts: [], next_offset: 0, has_more: false })
      .mockResolvedValueOnce({ posts: [{ id: 81, post_id: 81, user_id: 902, body: "visible profile post" } as PulsePost], next_offset: 1, has_more: false });

    const result = await listPublicProfilePosts({ userId: 902, username: "fresh902" });

    expect(mockPulseApi).toHaveBeenNthCalledWith(1, expect.stringContaining("/api/pulse/profile/902/posts?"), undefined);
    expect(mockPulseApi).toHaveBeenNthCalledWith(2, expect.stringContaining("/api/pulse/feed?"), undefined);
    expect(mockPulseApi.mock.calls[1][0]).toContain("profile=902");
    expect(result.posts).toHaveLength(1);
    expect(result.posts[0].id).toBe(81);
  });

  it("falls back to pulse id only when no canonical user id is available", async () => {
    mockPulseApi
      .mockResolvedValueOnce({ posts: [], next_offset: 0, has_more: false })
      .mockResolvedValueOnce({ posts: [{ id: 82, post_id: 82, user_id: 903, body: "pulse id fallback" } as PulsePost], next_offset: 1, has_more: false });

    const result = await listPublicProfilePosts({ profileKey: "PLS-000903", publicPlayerId: "PLS-000903" });

    expect(mockPulseApi).toHaveBeenNthCalledWith(1, expect.stringContaining("/api/pulse/profile/PLS-000903/posts?"), undefined);
    expect(mockPulseApi).toHaveBeenNthCalledWith(2, expect.stringContaining("/api/pulse/feed?"), undefined);
    expect(mockPulseApi.mock.calls[1][0]).toContain("profile=PLS-000903");
    expect(result.posts).toHaveLength(1);
    expect(result.posts[0].id).toBe(82);
  });

  it("keeps pagination on the canonical profile posts route", async () => {
    await listPublicProfilePosts({ userId: 903, username: "fresh_user" }, { limit: 12, offset: 24 });

    expect(mockPulseApi).toHaveBeenCalledWith(expect.stringContaining("limit=12"), undefined);
    expect(mockPulseApi).toHaveBeenCalledWith(expect.stringContaining("offset=24"), undefined);
  });

  it("uses the shared author resolver for feed follow actions", async () => {
    const post = normalizePost({
      id: 78,
      post_id: 78,
      user_id: 904,
      body: "followable signal",
      author: { display_name: "TestMeNow", username: "TestMeNow" }
    } as PulsePost);

    await toggleFollowAuthor(post);

    const body = JSON.parse(String(mockPulseApi.mock.calls[0][1]?.body || "{}"));
    expect(mockPulseApi).toHaveBeenCalledWith(
      "/api/pulse/follows/toggle",
      expect.objectContaining({
        method: "POST"
      })
    );
    expect(body).toEqual({
      followed_user_id: 904,
      public_player_id: "TestMeNow",
      followed_public_player_id: "TestMeNow"
    });
  });

  it("does not poison public-id follow resolution with a zero user id", async () => {
    const post = normalizePost({
      id: 80,
      post_id: 80,
      body: "public id only",
      author_public_player_id: "PLS-000080",
      author: { display_name: "Public Only" }
    } as PulsePost);

    await toggleFollowAuthor(post);

    const body = JSON.parse(String(mockPulseApi.mock.calls[0][1]?.body || "{}"));
    expect(mockPulseApi).toHaveBeenCalledWith(
      "/api/pulse/follows/toggle",
      expect.objectContaining({
        method: "POST"
      })
    );
    expect(body).toEqual({
      public_player_id: "PLS-000080",
      followed_public_player_id: "PLS-000080"
    });
  });

  it("keeps feed mute actions on the same canonical author target", async () => {
    const post = normalizePost({
      id: 79,
      post_id: 79,
      user_id: 905,
      body: "mute target",
      author_public_player_id: "PLS-000905",
      author: { display_name: "Future Member" }
    } as PulsePost);

    await mutePostAuthor(post);

    const body = JSON.parse(String(mockPulseApi.mock.calls[0][1]?.body || "{}"));
    expect(mockPulseApi).toHaveBeenCalledWith(
      "/api/pulse/users/mute",
      expect.objectContaining({
        method: "POST"
      })
    );
    expect(body).toEqual({
      muted_user_id: 905,
      user_id: 905,
      public_player_id: "PLS-000905",
      muted_public_player_id: "PLS-000905",
      reason: "Muted from Home"
    });
  });
});
