import { setCommunityCreateIntent, takeCommunityCreateIntent } from "../communityCreateIntent";

describe("community create intent", () => {
  it("hands the requested creation surface to Groups exactly once", () => {
    setCommunityCreateIntent("group");
    expect(takeCommunityCreateIntent()).toBe("group");
    expect(takeCommunityCreateIntent()).toBeNull();

    setCommunityCreateIntent("room");
    expect(takeCommunityCreateIntent()).toBe("room");
    expect(takeCommunityCreateIntent()).toBeNull();
  });
});
