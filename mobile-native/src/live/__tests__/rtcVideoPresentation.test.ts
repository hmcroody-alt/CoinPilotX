import { agoraRenderModeForPresentation } from "../RtcVideoView";

describe("Agora video presentation", () => {
  it("uses proportional crop-to-fill only when a surface opts into cover", () => {
    expect(agoraRenderModeForPresentation("cover")).toBe(1);
    expect(agoraRenderModeForPresentation("fit")).toBe(2);
    expect(agoraRenderModeForPresentation()).toBeUndefined();
  });
});
