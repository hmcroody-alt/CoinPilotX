import { readFileSync } from "fs";
import { join } from "path";

const postCard = readFileSync(join(__dirname, "..", "PostCard.tsx"), "utf8");
const home = readFileSync(join(__dirname, "..", "..", "screens", "HomeScreen.tsx"), "utf8");
const liveSurface = readFileSync(join(__dirname, "..", "reels", "ReelLiveViewerSurface.tsx"), "utf8");

describe("Live Feed distribution architecture", () => {
  it("renders authenticated receive-only Live media only for the active Feed card", () => {
    expect(postCard).toContain("EmbeddedLiveViewerSurface");
    expect(postCard).toContain("active={active}");
    expect(liveSurface).toContain('getLiveRtcToken(liveId, "viewer")');
    expect(liveSurface).toContain("publish: false");
    expect(liveSurface).toContain('disconnect("left_feed_item")');
    expect(home).toContain("itemVisiblePercentThreshold: 72");
    expect(home).toContain("active={activePostId === item.id}");
  });

  it("opens canonical Live and polls only the visible Live post for ended/replay state", () => {
    expect(home).toContain('navigation.navigate("LiveDetail", { liveId');
    expect(home).toContain("getPostDetail(activeLivePost.id)");
    expect(postCard).toContain("Replay processing");
    expect(postCard).toContain("Join Live");
  });

  it("fills the active Agora canvas while preserving the replay footprint", () => {
    expect(postCard).toContain('agoraPresentation="cover"');
    expect(liveSurface).toContain('objectFit="cover"');
    expect(liveSurface).toContain("resizeMode={ResizeMode.COVER}");
    expect(postCard).toContain("aspectRatio: 9 / 16");
  });
});
