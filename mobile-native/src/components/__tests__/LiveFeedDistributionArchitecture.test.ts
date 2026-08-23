import { readFileSync } from "fs";
import { join } from "path";

const postCard = readFileSync(join(__dirname, "..", "PostCard.tsx"), "utf8");
const home = readFileSync(join(__dirname, "..", "..", "screens", "HomeScreen.tsx"), "utf8");
const profileViewer = readFileSync(join(__dirname, "..", "..", "screens", "ProfilePostViewerScreen.tsx"), "utf8");
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
    expect(postCard).toContain("Preparing replay…");
    expect(postCard).toContain("Replay unavailable");
    expect(postCard).toContain('liveStatus === "processing"');
    expect(postCard).toContain("Join Live");
    expect(profileViewer).toContain("getPostDetail(activePostId)");
  });

  it("fills the active Agora canvas while preserving the replay footprint", () => {
    expect(postCard).toContain('agoraPresentation="cover"');
    expect(liveSurface).toContain('objectFit="cover"');
    expect(liveSurface).toContain("resizeMode={ResizeMode.COVER}");
    expect(postCard).toContain("aspectRatio: 9 / 16");
    expect(postCard).not.toContain("maxHeight: 620");
  });

  it("starts receive-only Feed Live with remote audio enabled and keeps truthful mute controls", () => {
    expect(postCard).toContain("const [liveMuted, setLiveMuted] = useState(false)");
    expect(postCard).toContain("muted={liveMuted}");
    expect(postCard).toContain('liveMuted ? "Unmute Live" : "Mute Live"');
  });
});
