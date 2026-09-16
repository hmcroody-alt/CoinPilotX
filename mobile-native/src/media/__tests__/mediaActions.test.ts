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
jest.mock("expo-file-system/legacy", () => ({
  cacheDirectory: "file:///cache/",
  makeDirectoryAsync: jest.fn(async () => undefined),
  copyAsync: jest.fn(async () => undefined),
  deleteAsync: jest.fn(async () => undefined)
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
import * as FileSystem from "expo-file-system/legacy";
import { MEDIA_ACTION_ORDER, openDocument, saveMediaToGallery, shareMedia } from "../mediaActions";

const mockDownloadMedia = downloadMedia as jest.MockedFunction<typeof downloadMedia>;
const mockSharePulseObject = sharePulseObject as jest.MockedFunction<typeof sharePulseObject>;
const mockMediaLibrary = MediaLibrary as unknown as {
  getPermissionsAsync: jest.Mock;
  requestPermissionsAsync: jest.Mock;
  saveToLibraryAsync: jest.Mock;
};
const mockSharing = Sharing as unknown as { isAvailableAsync: jest.Mock; shareAsync: jest.Mock };
const mockFileSystem = FileSystem as unknown as {
  makeDirectoryAsync: jest.Mock;
  copyAsync: jest.Mock;
  deleteAsync: jest.Mock;
};

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

  /**
   * Photos decides image-versus-movie from the file name and rejects a file with
   * no extension at all — even a valid JPEG. The download engine names its files
   * now, but entries cached before that fix are still sitting on disk unnamed,
   * and a user whose photo will not save does not care which release wrote it.
   */
  describe("an extensionless cached file", () => {
    const UNNAMED = "file:///cache/pulsesoc-media/anon/cm58uqq";

    beforeEach(() => {
      mockDownloadMedia.mockResolvedValue({
        key: "id:87",
        fileUri: UNNAMED,
        bytes: 803426,
        mimeType: "image/jpeg",
        createdAt: Date.now(),
        lastAccessAt: Date.now()
      });
    });

    it("is copied to a name Photos accepts before the write", async () => {
      await expect(saveMediaToGallery({ ...PHOTO, mimeType: "image/jpeg" })).resolves.toEqual({
        status: "saved",
        limited: false
      });
      expect(mockFileSystem.copyAsync).toHaveBeenCalledWith({ from: UNNAMED, to: expect.stringMatching(/\.jpg$/) });
      expect(mockMediaLibrary.saveToLibraryAsync).toHaveBeenCalledWith(expect.stringMatching(/\.jpg$/));
    });

    it("falls back to the kind when the MIME type is missing too", async () => {
      await expect(saveMediaToGallery({ ...PHOTO, kind: "video", mimeType: undefined })).resolves.toMatchObject({
        status: "saved"
      });
      expect(mockMediaLibrary.saveToLibraryAsync).toHaveBeenCalledWith(expect.stringMatching(/\.mp4$/));
    });

    it("deletes the copy afterwards — the cache still owns the real bytes", async () => {
      await saveMediaToGallery({ ...PHOTO, mimeType: "image/jpeg" });
      expect(mockFileSystem.deleteAsync).toHaveBeenCalledWith(expect.stringMatching(/\.jpg$/), { idempotent: true });
    });

    it("deletes the copy even when the write fails", async () => {
      // Otherwise every failed save leaks a full-size duplicate into the cache,
      // and the failure users hit most is the one that fills their disk.
      mockFileSystem.deleteAsync.mockClear();
      mockMediaLibrary.saveToLibraryAsync.mockRejectedValue(new Error("write failed"));
      await expect(saveMediaToGallery({ ...PHOTO, mimeType: "image/jpeg" })).resolves.toMatchObject({
        status: "failed"
      });
      expect(mockFileSystem.deleteAsync).toHaveBeenCalledWith(expect.stringMatching(/\.jpg$/), { idempotent: true });
    });
  });

  it("copies nothing when the cached file already has an extension", async () => {
    await saveMediaToGallery(PHOTO);
    expect(mockFileSystem.copyAsync).not.toHaveBeenCalled();
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

  it("can re-mint an expired access URL, exactly as Save can", async () => {
    // Share is reached whenever the user taps, which can be long after the
    // fifteen-minute grant that painted the picture. Without the hook, sharing a
    // photo that is on screen degrades to sharing a link the recipient cannot
    // open — a silent downgrade that looks like it worked.
    const refreshUrl = jest.fn(async () => "https://pulsesoc.com/api/messages/media/601/download?mt=fresh");
    await shareMedia({ ...PHOTO, refreshUrl });
    expect(mockDownloadMedia).toHaveBeenCalledWith(expect.objectContaining({ refreshUrl }));
  });

  describe("an extensionless cached file", () => {
    const UNNAMED = "file:///cache/pulsesoc-media/anon/cm58uqq";

    beforeEach(() => {
      mockDownloadMedia.mockResolvedValue({
        key: "id:87",
        fileUri: UNNAMED,
        bytes: 803426,
        mimeType: "image/jpeg",
        createdAt: Date.now(),
        lastAccessAt: Date.now()
      });
    });

    it("is shared under a name the recipient can open", async () => {
      // Observed on device: the sheet showed "cm58uqq — File · 803 KB" and
      // offered only Copy/Print/Save to Files. Declaring `mimeType` and `UTI`
      // does not rescue it; iOS types a file URL by its extension.
      await expect(shareMedia(PHOTO)).resolves.toEqual({ status: "shared", mode: "file" });
      expect(mockSharing.shareAsync).toHaveBeenCalledWith(
        expect.stringMatching(/\.jpg$/),
        expect.objectContaining({ mimeType: "image/jpeg" })
      );
    });

    it("deletes the shared copy once the sheet closes", async () => {
      await shareMedia(PHOTO);
      expect(mockFileSystem.deleteAsync).toHaveBeenCalledWith(expect.stringMatching(/\.jpg$/), { idempotent: true });
    });

    it("deletes it even when the share is dismissed or fails", async () => {
      mockSharing.shareAsync.mockRejectedValue(new Error("dismissed"));
      await shareMedia({ ...PHOTO, sourceUrl: "https://pulsesoc.com/p/9" });
      expect(mockFileSystem.deleteAsync).toHaveBeenCalledWith(expect.stringMatching(/\.jpg$/), { idempotent: true });
    });
  });

  it("copies nothing when the cached file already has an extension", async () => {
    await shareMedia(PHOTO);
    expect(mockFileSystem.copyAsync).not.toHaveBeenCalled();
    expect(mockSharing.shareAsync).toHaveBeenCalledWith(
      "file:///cache/pulsesoc-media/u1/abc.jpg",
      expect.anything()
    );
  });
});

describe("openDocument", () => {
  const PDF = {
    url: "https://pulsesoc.com/api/messages/media/44/download",
    mediaId: 44,
    mimeType: "application/pdf",
    surface: "messenger",
    title: "contract.pdf"
  };

  beforeEach(() => {
    mockDownloadMedia.mockResolvedValue({
      key: "id:44",
      fileUri: "file:///cache/pulsesoc-media/u1/contract.pdf",
      bytes: 91_233,
      mimeType: "application/pdf",
      createdAt: Date.now(),
      lastAccessAt: Date.now()
    });
  });

  it("opens the downloaded file, which is the whole point of the tap", async () => {
    await expect(openDocument(PDF)).resolves.toEqual({ status: "opened" });
    expect(mockSharing.shareAsync).toHaveBeenCalledWith(
      "file:///cache/pulsesoc-media/u1/contract.pdf",
      expect.objectContaining({ mimeType: "application/pdf" })
    );
  });

  it("asks for the document's exact UTI, because public.data opens nothing on iOS", async () => {
    await openDocument(PDF);
    expect(mockSharing.shareAsync).toHaveBeenCalledWith(expect.any(String), expect.objectContaining({ UTI: "com.adobe.pdf" }));
  });

  it("knows the Office types too, not just PDF", async () => {
    const docx = "application/vnd.openxmlformats-officedocument.wordprocessingml.document";
    mockDownloadMedia.mockResolvedValue({
      key: "id:45",
      fileUri: "file:///cache/pulsesoc-media/u1/brief.docx",
      bytes: 2048,
      mimeType: docx,
      createdAt: Date.now(),
      lastAccessAt: Date.now()
    });
    await openDocument({ ...PDF, mediaId: 45, mimeType: docx });
    expect(mockSharing.shareAsync).toHaveBeenCalledWith(
      expect.any(String),
      expect.objectContaining({ UTI: "org.openxmlformats.wordprocessingml.document" })
    );
  });

  it("tolerates a charset on the declared type", async () => {
    mockDownloadMedia.mockResolvedValue({
      key: "id:46",
      fileUri: "file:///cache/pulsesoc-media/u1/notes.txt",
      bytes: 12,
      mimeType: "text/plain; charset=utf-8",
      createdAt: Date.now(),
      lastAccessAt: Date.now()
    });
    await openDocument({ ...PDF, mediaId: 46, mimeType: "text/plain; charset=utf-8" });
    expect(mockSharing.shareAsync).toHaveBeenCalledWith(expect.any(String), expect.objectContaining({ UTI: "public.plain-text" }));
  });

  it("reports a real failure instead of substituting a share sheet for a link", async () => {
    // The user asked to read this file. Handing them a URL their recipient would
    // hit a login wall on is a silent substitution, not a fallback.
    mockDownloadMedia.mockRejectedValue(new MediaDownloadError("network", "offline"));
    const result = await openDocument({ ...PDF, sourceUrl: "https://pulsesoc.com/p/9" } as never);
    expect(result).toMatchObject({ status: "failed", reason: "network" });
    expect(mockSharePulseObject).not.toHaveBeenCalled();
    expect(mockSharing.shareAsync).not.toHaveBeenCalled();
  });

  it("says so when the device has no viewer at all, rather than failing silently", async () => {
    mockSharing.isAvailableAsync.mockResolvedValue(false);
    const result = await openDocument(PDF);
    expect(result.status).toBe("unsupported");
    expect(mockDownloadMedia).not.toHaveBeenCalled();
  });

  it("never puts a URL in a user-facing message", async () => {
    mockDownloadMedia.mockRejectedValue(new MediaDownloadError("forbidden", PDF.url));
    const result = (await openDocument(PDF)) as { message: string };
    expect(result.message).not.toContain("http");
  });

  it("downloads through the shared cache, so opening twice costs one transfer", async () => {
    await openDocument(PDF);
    expect(mockDownloadMedia).toHaveBeenCalledWith(
      expect.objectContaining({ mediaId: 44, kind: "file", surface: "messenger" })
    );
  });
});

describe("action order (Stage 39)", () => {
  it("is fixed as data so a new surface inherits it instead of re-deciding it", () => {
    expect([...MEDIA_ACTION_ORDER]).toEqual(["react", "reply", "forward", "share", "save"]);
  });
});

/**
 * Progress has to reach the caller, or the surface has nothing honest to render.
 *
 * The downloader has emitted progress to a listener set since it was written, and
 * every one of these actions simply never passed a listener through — so on
 * device an 8.6 MB save showed the word "Saving" and nothing else for minutes,
 * which is indistinguishable from a hang and was in fact mistaken for one. The
 * gap was wiring, not mechanism, so what is pinned here is the wiring.
 */
describe("transfer progress reaches the caller", () => {
  const onProgress = jest.fn();

  it("forwards a progress listener when saving to the library", async () => {
    mockDownloadMedia.mockResolvedValue({
      key: "id:9", fileUri: "file:///cache/9.mp4", bytes: 10, mimeType: "video/mp4", createdAt: 0, lastAccessAt: 0
    } as never);
    await saveMediaToGallery({ url: "https://cdn.pulsesoc.com/m/9.mp4", mediaId: 9, kind: "video" }, { onProgress });
    expect(mockDownloadMedia).toHaveBeenCalledWith(expect.objectContaining({ onProgress }));
  });

  it("forwards a progress listener when opening a document", async () => {
    mockDownloadMedia.mockResolvedValue({
      key: "id:44", fileUri: "file:///cache/44.pdf", bytes: 10, mimeType: "application/pdf", createdAt: 0, lastAccessAt: 0
    } as never);
    await openDocument({ url: "https://cdn.pulsesoc.com/m/44.pdf", mediaId: 44, kind: "file" }, { onProgress });
    expect(mockDownloadMedia).toHaveBeenCalledWith(expect.objectContaining({ onProgress }));
  });
});
