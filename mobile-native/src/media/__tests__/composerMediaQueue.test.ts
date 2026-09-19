/**
 * The composer's upload queue had no tests at all, which is how the behaviour
 * this file pins went unnoticed: media starts uploading the instant it is
 * picked, so by the time anyone presses Post the item is almost always
 * mid-flight, and that is the state nothing exercised.
 *
 * What matters here is that a second caller *joins* the run instead of starting
 * another pass over it. The bytes were never duplicated -- `MediaUploadManager`
 * keys its own in-flight table on `uri|size|contextType|contextId` -- so an
 * assertion on upload counts alone would have passed before the fix too. The
 * discriminating assertions are that `uploadNativeMedia` is not re-entered and
 * that the item's progress does not walk backwards to "Preparing media." while
 * the bytes it describes keep going forwards.
 */
import { act, renderHook, waitFor } from "@testing-library/react-native";
import { useComposerMediaQueue } from "../useComposerMediaQueue";
import type { NativeMediaAsset, NativeMediaUploadResult, UploadProgress } from "../nativeMediaUpload";

jest.mock("../nativeMediaUpload", () => {
  const actual = jest.requireActual("../nativeMediaUpload");
  return {
    ...actual,
    validateNativeMedia: jest.fn(() => ""),
    uploadNativeMedia: jest.fn(),
    pollNativeMediaProcessing: jest.fn(async () => null)
  };
});

const { uploadNativeMedia, pollNativeMediaProcessing, validateNativeMedia } = jest.requireMock("../nativeMediaUpload");

const VIDEO: NativeMediaAsset = {
  uri: "file:///var/mobile/long-video.mov",
  name: "long-video.mov",
  mimeType: "video/quicktime",
  mediaType: "video",
  size: 3 * 1024 * 1024 * 1024,
  duration: 89 * 60 * 1000
};

const READY: NativeMediaUploadResult = { ok: true, media_id: 4242, processing_status: "ready" };

/** A single upload whose completion this test decides, plus its progress sink. */
function deferredUpload() {
  let settle: (value: NativeMediaUploadResult) => void = () => undefined;
  let fail: (reason: Error) => void = () => undefined;
  let emit: (progress: UploadProgress) => void = () => undefined;
  const cancel = jest.fn();
  (uploadNativeMedia as jest.Mock).mockImplementation((_asset, _options, onProgress) => {
    emit = (progress) => act(() => { onProgress?.(progress); });
    return {
      promise: new Promise<NativeMediaUploadResult>((resolve, reject) => { settle = resolve; fail = reject; }),
      controller: { cancel }
    };
  });
  return {
    cancel,
    progress: (progress: UploadProgress) => emit(progress),
    finish: async (result: NativeMediaUploadResult = READY) => { await act(async () => { settle(result); }); },
    reject: async (reason: Error) => { await act(async () => { fail(reason); }); }
  };
}

const OPTIONS = { contextType: "pulse", contextId: "native-draft", target: "feed", destination: "feed", mode: "post" };

async function queueWithVideo() {
  const upload = deferredUpload();
  const view = renderHook(() => useComposerMediaQueue(OPTIONS));
  await act(async () => { view.result.current.addAssets([VIDEO]); });
  // The auto-start effect fires on the item landing in "selected".
  await waitFor(() => expect(uploadNativeMedia).toHaveBeenCalledTimes(1));
  return { view, upload };
}

beforeEach(() => {
  jest.clearAllMocks();
  (validateNativeMedia as jest.Mock).mockReturnValue("");
  (pollNativeMediaProcessing as jest.Mock).mockResolvedValue(null);
});

it("starts uploading as soon as media is selected", async () => {
  const { view } = await queueWithVideo();
  expect(uploadNativeMedia).toHaveBeenCalledWith(VIDEO, expect.objectContaining({ contextType: "pulse" }), expect.any(Function));
  expect(view.result.current.uploading).toBe(true);
});

it("joins the upload already in flight instead of starting a second pass", async () => {
  const { view, upload } = await queueWithVideo();
  upload.progress({ stage: "uploading", percent: 61, message: "Uploading media 61%." });
  await waitFor(() => expect(view.result.current.items[0].progress.percent).toBe(61));

  // This is the press of Post that used to be refused outright.
  let published: Promise<{ mediaIds: Array<number | undefined> }>;
  await act(async () => { published = view.result.current.uploadAll({ mode: "post" }); });

  // The discriminating assertion: no re-entry. Before this behaviour existed,
  // `uploadAll` called back into the upload body, which reset the item to
  // "Preparing media." at 1% even though the transport deduped the bytes.
  expect(uploadNativeMedia).toHaveBeenCalledTimes(1);
  expect(view.result.current.items[0].progress.percent).toBe(61);
  expect(view.result.current.items[0].progress.stage).toBe("uploading");

  await upload.finish();
  await expect(published!).resolves.toEqual(expect.objectContaining({ mediaIds: [4242] }));
  // The post-upload processing poll must not run once per caller either.
  expect(pollNativeMediaProcessing).toHaveBeenCalledTimes(1);
});

it("returns the finished result without re-uploading once media is ready", async () => {
  const { view, upload } = await queueWithVideo();
  await upload.finish();
  await waitFor(() => expect(view.result.current.items[0].progress.stage).toBe("ready"));

  let published: Promise<{ mediaIds: Array<number | undefined> }>;
  await act(async () => { published = view.result.current.uploadAll(); });
  await expect(published!).resolves.toEqual(expect.objectContaining({ mediaIds: [4242] }));
  expect(uploadNativeMedia).toHaveBeenCalledTimes(1);
});

it("fails the publish and keeps the item retryable when the joined upload fails", async () => {
  const { view, upload } = await queueWithVideo();
  let published: Promise<unknown>;
  await act(async () => {
    published = view.result.current.uploadAll().catch((error: Error) => error);
  });
  await upload.reject(new Error("Upload transport was interrupted."));

  await expect(published!).resolves.toEqual(expect.objectContaining({ message: "Upload transport was interrupted." }));
  await waitFor(() => expect(view.result.current.items[0].progress.stage).toBe("failed"));

  // A failure must release the join, or the item could never be retried.
  const second = deferredUpload();
  await act(async () => { void view.result.current.retry(view.result.current.items[0].id).catch(() => undefined); });
  await waitFor(() => expect(uploadNativeMedia).toHaveBeenCalledTimes(2));
  await second.finish();
  await waitFor(() => expect(view.result.current.items[0].progress.stage).toBe("ready"));
});

it("reports a rejected asset without reaching the transport", async () => {
  (validateNativeMedia as jest.Mock).mockReturnValue("Choose an MP4, MOV, or WEBM video.");
  const view = renderHook(() => useComposerMediaQueue(OPTIONS));
  await act(async () => { view.result.current.addAssets([{ ...VIDEO, name: "clip.avi" }]); });
  await waitFor(() => expect(view.result.current.items[0].progress.stage).toBe("failed"));
  expect(uploadNativeMedia).not.toHaveBeenCalled();
  expect(view.result.current.items[0].error).toBe("Choose an MP4, MOV, or WEBM video.");
});
