import { isContentOwner, resolveContentId, resolveContentOwnerId } from "../contentOwnership";

describe("resolveContentOwnerId", () => {
  it("resolves the current top-level user_id field", () => {
    expect(resolveContentOwnerId({ id: 1, user_id: 42 })).toBe(42);
  });

  it("resolves legacy owner field aliases", () => {
    expect(resolveContentOwnerId({ author_id: 7 })).toBe(7);
    expect(resolveContentOwnerId({ owner_id: 8 })).toBe(8);
    expect(resolveContentOwnerId({ creator_id: 9 })).toBe(9);
    expect(resolveContentOwnerId({ uploader_id: 10 })).toBe(10);
    expect(resolveContentOwnerId({ posted_by: 11 })).toBe(11);
    expect(resolveContentOwnerId({ created_by: 12 })).toBe(12);
  });

  it("falls back to nested author/user/owner/creator objects", () => {
    expect(resolveContentOwnerId({ author: { user_id: 55 } })).toBe(55);
    expect(resolveContentOwnerId({ user: { id: 66 } })).toBe(66);
    expect(resolveContentOwnerId({ owner: { owner_id: 77 } })).toBe(77);
    expect(resolveContentOwnerId({ creator: { author_id: 88 } })).toBe(88);
  });

  it("returns 0 when no owner id can be found", () => {
    expect(resolveContentOwnerId({})).toBe(0);
    expect(resolveContentOwnerId(null)).toBe(0);
    expect(resolveContentOwnerId(undefined)).toBe(0);
    expect(resolveContentOwnerId({ user_id: 0 })).toBe(0);
    expect(resolveContentOwnerId({ user_id: -5 })).toBe(0);
  });

  it("ignores non-numeric owner id values", () => {
    expect(resolveContentOwnerId({ user_id: "not-a-number" })).toBe(0);
  });
});

describe("resolveContentId", () => {
  it("resolves id and legacy id aliases", () => {
    expect(resolveContentId({ id: 5 })).toBe(5);
    expect(resolveContentId({ post_id: 6 })).toBe(6);
    expect(resolveContentId({ reel_id: 7 })).toBe(7);
    expect(resolveContentId({ comment_id: 8 })).toBe(8);
  });

  it("returns 0 for content with no resolvable id", () => {
    expect(resolveContentId({})).toBe(0);
  });
});

describe("isContentOwner", () => {
  it("honors an explicit server-computed can_manage flag over id comparison", () => {
    expect(isContentOwner({ can_manage: true, user_id: 999 }, 1)).toBe(true);
    expect(isContentOwner({ can_manage: false, user_id: 1 }, 1)).toBe(false);
  });

  it("honors can_delete / is_owner / is_mine / is_author flags", () => {
    expect(isContentOwner({ can_delete: true }, 1)).toBe(true);
    expect(isContentOwner({ is_owner: true }, 1)).toBe(true);
    expect(isContentOwner({ is_mine: true }, 1)).toBe(true);
    expect(isContentOwner({ is_author: true }, 1)).toBe(true);
  });

  it("falls back to comparing resolved owner id against current user id", () => {
    expect(isContentOwner({ user_id: 42 }, 42)).toBe(true);
    expect(isContentOwner({ user_id: 42 }, 7)).toBe(false);
    expect(isContentOwner({ author: { id: 42 } }, 42)).toBe(true);
  });

  it("returns false when there is no current user or no owner can be resolved", () => {
    expect(isContentOwner({ user_id: 42 }, null)).toBe(false);
    expect(isContentOwner({ user_id: 42 }, undefined)).toBe(false);
    expect(isContentOwner({}, 42)).toBe(false);
  });
});
