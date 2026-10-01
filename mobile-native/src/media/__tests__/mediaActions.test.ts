/**
 * Stage 7/8/39 regression tests for the shared media actions.
 *
 * The rule these guard is "never falsely report Saved". Every test below is a
 * path that a boolean-returning implementation would have collapsed into
 * success or into an indistinguishable "didn't work".
 */
/**
 * Each factory builds its own `jest.fn()`s rather than closing over consts
 * declared below. `jest.mock` is hoisted above the imports, so a factory that
 * referenced an outer `const` would read it before initialisation and silently
 * return `undefined` for that export — which looks exactly like the module under
 * test being broken. The handles are recovered from `requireMock` afterwards.
 */
jest.mock("expo-media-library", () => ({
  getPermissionsAsync: jest.fn(),
  requestPermissionsAsync: jest.fn(),
  saveToLibraryAsync: jest.fn()
}));
jest.mock("expo-sharing", () => ({
  isAvailableAsync: jest.fn(),
  shareAsync: jest.fn()
}));
jest.mock("../../sharing/nativeShare", () => ({ sharePulseObject: jest.fn() }));
jest.mock("../mediaDownloader", () => {
  // `MediaDownloadError` must keep its real identity: `saveMediaToGallery`
  // branches on `instanceof`, so a stubbed class would change what is tested.
  const actual = jest.requireActual("../mediaDownloader");
  return { ...actual, downloadMedia: jest.fn() };
});

import { MediaDownloadError, downloadMedia } from "../mediaDownloader";
import { sharePulseObject } from "../../sharing/nativeShare";
import * as MediaLibrary from "expo-media-library";
import * as Sharing from "expo-sharing";
import { MEDIA_ACTION_ORDER, saveMediaToGallery, shareMedia } from "../mediaActions";

const mockDownloadMedia = downloadMedia as jest.MockedFunction<typeof downloadMedia>;
const mockSharePulseObject = sharePulseObject as jest.MockedFunction<typeof sharePulseObject>;
const mockMediaLibrary = MediaLibrary as unknown as {
  getPermissionsAsync: jest.Mock;
  requestPermissionsAsync: jest.Mock;
  saveToLibraryAsync: jest.Mock;
};
const mockSharing = Sharing as unknown as { isAvailableAsync: jest.Mock; shareAsync: jest.Mock };

const PHOTO = { url: "https://cdn.pulsesoc.com/m/7.jpg", mediaId: 7, kind: "image" as const, surface: "messenger" };

beforeEach(() => {
  jest.clearAllMocks();
  mockDownloadMedia.mockResolvedValue({
    key: "id:7",
    fileUri: "file:///cache/pulsesoc-media/u1/abc.jpg",
    bytes: 4096,
    mimeType: "image/jpeg",
    createdAt: Date.now(),
    lastAccessAt: Date.now()
  });
  mockMediaLibrary.getPermissionsAsync.mockResolvedValue({ granted: true, accessPrivileges: "all", canAskAgain: true });
  mockMediaLibrary.saveToLibraryAsync.mockResolvedValue(undefined);
  mockSharing.isAvailableAsync.mockResolvedValue(true);
  mockSharing.shareAsync.mockResolvedValue(undefined);
  mockSharePulseObject.mockResolvedValue({ action: "sharedAction" });
});

describe("saveMediaToGallery", () => {
  it("reports saved only after the write actually completed", async () => {
    await expect(saveMediaToGallery(PHOTO)).resolves.toEqual({ status: "saved", limited: false });
    expect(mockMediaLibrary.saveToLibraryAsync).toHaveBeenCalledWith("file:///cache/pulsesoc-media/u1/abc.jpg");
  });

  it("asks for the narrowest entitlement that can do the job", async () => {
    mockMediaLibrary.getPermissionsAsync.mockResolvedValue({ granted: false, accessPrivileges: "none", canAskAgain: true });
    mockMediaLibrary.requestPermissionsAsync.mockResolvedValue({ granted: true, accessPrivileges: "all", canAskAgain: true });
    await saveMediaToGallery(PHOTO);
    expect(mockMediaLibrary.getPermissionsAsync).toHaveBeenCalledWith(true);
    expect(mockMediaLibrary.requestPermissionsAsync).toHaveBeenCalledWith(true);
  });

  it("downloads before prompting, so a doomed save never costs a permission prompt", async () => {
    mockDownloadMedia.mockRejectedValue(new MediaDownloadError("not_found", "gone"));
    const result = await saveMediaToGallery(PHOTO);
    expect(result.status).toBe("failed");
    expect(mockMediaLibrary.requestPermissionsAsync).not.toHaveBeenCalled();
    expect(mockMediaLibrary.saveToLibraryAsync).not.toHaveBeenCalled();
  });

  it("treats iOS limited access as a success, because add-only still writes", async () => {
    mockMediaLibrary.getPermissionsAsync.mockResolvedValue({ granted: false, accessPrivileges: "limited", canAskAgain: true });
    mockMediaLibrary.requestPermissionsAsync.mockResolvedValue({ granted: false, accessPrivileges: "limited", canAskAgain: true });
    await expect(saveMediaToGallery(PHOTO)).resolves.toEqual({ status: "saved", limited: true });
  });

  it("distinguishes a refusal we can re-ask about from one that needs Settings", async () => {
    mockMediaLibrary.getPermissionsAsync.mockResolvedValue({ granted: false, accessPrivileges: "none", canAskAgain: true });
    mockMediaLibrary.requestPermissionsAsync.mockResolvedValue({ granted: false, accessPrivileges: "none", canAskAgain: false });
    const result = await saveMediaToGallery(PHOTO);
    expect(result).toMatchObject({ status: "permission_denied" });
    expect((result as { message: string }).message).toMatch(/Settings/);
  });

  it("does not report saved when the library write itself throws", async () => {
    mockMediaLibrary.saveToLibraryAsync.mockRejectedValue(new Error("disk full"));
    const result = await saveMediaToGallery(PHOTO);
    expect(result.status).toBe("failed");
  });

  it("refuses file types Photos cannot accept, and says where to go instead", async () => {
    const result = await saveMediaToGallery({ ...PHOTO, kind: "file" });
    expect(result).toMatchObject({ status: "unsupported" });
    expect((result as { message: string }).message).toMatch(/Share/);
    expect(mockDownloadMedia).not.toHaveBeenCalled();
  });

  it("never puts a URL in a user-facing message", async () => {
    mockDownloadMedia.mockRejectedValue(new MediaDownloadError("forbidden", "https://cdn.pulsesoc.com/m/7.jpg denied"));
    const result = await saveMediaToGallery(PHOTO);
    expect((result as { message: string }).message).not.toMatch(/https?:/);
  });
});

describe("shareMedia", () => {
  it("shares the real file by default, so the recipient gets the picture", async () => {
    await expect(shareMedia(PHOTO)).resolves.toEqual({ status: "shared", mode: "file" });
    expect(mockSharing.shareAsync).toHaveBeenCalled();
    expect(mockSharePulseObject).not.toHaveBeenCalled();
  });

  it("shares the canonical link when the content is a post, not a file", async () => {
    const result = await shareMedia({ ...PHOTO, sourceUrl: "https://pulsesoc.com/p/9" }, { preferLink: true });
    expect(result).toEqual({ status: "shared", mode: "link" });
    expect(mockSharing.shareAsync).not.toHaveBeenCalled();
    expect(mockSharePulseObject).toHaveBeenCalledWith(expect.objectContaining({ url: "https://pulsesoc.com/p/9" }));
  });

  it("degrades to the link rather than to nothing when the file cannot be produced", async () => {
    mockDownloadMedia.mockRejectedValue(new MediaDownloadError("network", "offline"));
    await expect(shareMedia({ ...PHOTO, sourceUrl: "https://pulsesoc.com/p/9" })).resolves.toEqual({
      status: "shared",
      mode: "link"
    });
  });

  it("falls back to the link when the platform has no share sheet for files", async () => {
    mockSharing.isAvailableAsync.mockResolvedValue(false);
    await expect(shareMedia(PHOTO)).resolves.toEqual({ status: "shared", mode: "link" });
  });
});

describe("action order (Stage 39)", () => {
  it("is fixed as data so a new surface inherits it instead of re-deciding it", () => {
    expect([...MEDIA_ACTION_ORDER]).toEqual(["react", "reply", "forward", "share", "save"]);
  });
});
