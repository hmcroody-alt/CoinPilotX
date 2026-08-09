const mockPulseApi = jest.fn(async (_path?: string, _options?: RequestInit) => ({ posts: [], next_offset: 0, has_more: false }));

jest.mock("../pulseApi", () => ({
  pulseApi: (path: string, options?: RequestInit) => mockPulseApi(path, options)
}));

import { normalizePost, PulsePost } from "../feed";
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

    expect(mockPulseApi).toHaveBeenCalledWith(expect.stringContaining("profile=901"), undefined);
  });

  it("prefers the resolved user id over display name for future new users", async () => {
    await listPublicProfilePosts({ userId: 902, display_name: "Future User" });

    expect(mockPulseApi).toHaveBeenCalledWith(expect.stringContaining("profile=902"), undefined);
  });
});
