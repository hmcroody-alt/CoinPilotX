import { addAgoraRemoteUid, removeAgoraRemoteUid } from "../useAgoraCallRoom";

describe("Agora group call participant tracking", () => {
  it("keeps every unique remote participant in join order", () => {
    expect(addAgoraRemoteUid([101, 202], 303)).toEqual([101, 202, 303]);
    expect(addAgoraRemoteUid([101, 202], 202)).toEqual([101, 202]);
  });

  it("removes only the participant who left", () => {
    expect(removeAgoraRemoteUid([101, 202, 303], 202)).toEqual([101, 303]);
    expect(removeAgoraRemoteUid([101, 303], 999)).toEqual([101, 303]);
  });
});
