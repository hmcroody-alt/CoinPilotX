jest.mock("../../native/haptics", () => ({ haptic: jest.fn() }));

jest.mock("expo-image-picker", () => ({
  requestMediaLibraryPermissionsAsync: jest.fn(),
  launchImageLibraryAsync: jest.fn(),
  MediaTypeOptions: { Images: "Images" }
}));

jest.mock("../../api/profile", () => ({
  uploadProfileAvatar: jest.fn(),
  uploadProfileCover: jest.fn()
}));

jest.mock("../../core/eventSync", () => ({ invalidateNativeSync: jest.fn() }));

import * as ImagePicker from "expo-image-picker";
import { uploadProfileAvatar, uploadProfileCover } from "../../api/profile";
import { invalidateNativeSync } from "../../core/eventSync";
import {
  announceProfileMediaChange,
  describeProfilePick,
  pickProfileImage,
  profileImageLabel,
  saveProfileImage
} from "../profileMediaEdit";

const requestPermission = ImagePicker.requestMediaLibraryPermissionsAsync as jest.Mock;
const launchLibrary = ImagePicker.launchImageLibraryAsync as jest.Mock;
const uploadAvatar = uploadProfileAvatar as jest.Mock;
const uploadCover = uploadProfileCover as jest.Mock;
const invalidate = invalidateNativeSync as jest.Mock;

function pickerReturns(asset: { uri: string; mimeType?: string | null; fileName?: string }) {
  launchLibrary.mockResolvedValue({ canceled: false, assets: [asset] });
}

beforeEach(() => {
  jest.clearAllMocks();
  requestPermission.mockResolvedValue({ granted: true });
  invalidate.mockResolvedValue(undefined);
  uploadAvatar.mockResolvedValue({ user_id: 7, avatar_url: "https://cdn/a.jpg?v=2" });
  uploadCover.mockResolvedValue({ user_id: 7, cover_url: "https://cdn/c.jpg?v=2" });
});

describe("crop ratio", () => {
  it("crops a cover to what the hero displays, not to a panorama", async () => {
    pickerReturns({ uri: "file:///c.jpg", mimeType: "image/jpeg" });
    await pickProfileImage("cover");
    expect(launchLibrary).toHaveBeenCalledWith(expect.objectContaining({ allowsEditing: true, aspect: [3, 2] }));
  });

  it("crops an avatar square", async () => {
    pickerReturns({ uri: "file:///a.jpg", mimeType: "image/jpeg" });
    await pickProfileImage("avatar");
    expect(launchLibrary).toHaveBeenCalledWith(expect.objectContaining({ allowsEditing: true, aspect: [1, 1] }));
  });
});

describe("filename", () => {
  // iOS returns the pre-crop HEIC filename beside the post-crop JPEG bytes, and
  // the endpoint screens the extension before it reads a byte.
  it("names the upload from the MIME type, not the asset filename", async () => {
    pickerReturns({ uri: "file:///IMG_0001.HEIC", mimeType: "image/jpeg", fileName: "IMG_0001.HEIC" });
    const pick = await pickProfileImage("avatar");
    expect(pick).toMatchObject({ status: "picked", name: "avatar.jpg", mimeType: "image/jpeg" });
  });

  it("keeps png and webp rather than relabelling them jpeg", async () => {
    pickerReturns({ uri: "file:///c.png", mimeType: "image/png" });
    expect(await pickProfileImage("cover")).toMatchObject({ name: "cover.png", mimeType: "image/png" });
    pickerReturns({ uri: "file:///c.webp", mimeType: "image/webp" });
    expect(await pickProfileImage("cover")).toMatchObject({ name: "cover.webp", mimeType: "image/webp" });
  });

  it("treats a silent picker as jpeg but a declared unsupported type as unsupported", async () => {
    pickerReturns({ uri: "file:///a.jpg", mimeType: null });
    expect(await pickProfileImage("avatar")).toMatchObject({ status: "picked", name: "avatar.jpg" });
    pickerReturns({ uri: "file:///a.gif", mimeType: "image/gif" });
    expect(await pickProfileImage("avatar")).toEqual({ status: "unsupported", mimeType: "image/gif" });
  });
});

describe("states that upload nothing", () => {
  it("reports denied permission and never opens the library", async () => {
    requestPermission.mockResolvedValue({ granted: false });
    const pick = await pickProfileImage("cover");
    expect(pick).toEqual({ status: "denied" });
    expect(launchLibrary).not.toHaveBeenCalled();
    expect(describeProfilePick("cover", pick)).toContain("photo access");
  });

  it("says nothing at all when the user cancels", async () => {
    launchLibrary.mockResolvedValue({ canceled: true });
    const pick = await pickProfileImage("avatar");
    expect(pick).toEqual({ status: "cancelled" });
    expect(describeProfilePick("avatar", pick)).toBe("");
  });
});

describe("save", () => {
  it("sends an avatar to the avatar endpoint and a cover to the cover endpoint", async () => {
    const asset = { uri: "file:///x.jpg", name: "avatar.jpg", mimeType: "image/jpeg" };
    await saveProfileImage("avatar", asset);
    expect(uploadAvatar).toHaveBeenCalledWith(asset);
    expect(uploadCover).not.toHaveBeenCalled();

    await saveProfileImage("cover", { ...asset, name: "cover.jpg" });
    expect(uploadCover).toHaveBeenCalledTimes(1);
    expect(uploadAvatar).toHaveBeenCalledTimes(1);
  });

  // Without this the global header keeps whatever avatar it fetched at launch.
  it("invalidates the profile subsystem after a successful upload", async () => {
    await saveProfileImage("avatar", { uri: "file:///x.jpg", name: "avatar.jpg", mimeType: "image/jpeg" });
    expect(invalidate).toHaveBeenCalledWith(
      ["profile"],
      "profile_avatar_updated",
      [expect.objectContaining({ event_type: "pulse_profile_avatar_updated", invalidates: ["profile"] })]
    );
  });

  it("does not invalidate when the upload fails", async () => {
    uploadCover.mockRejectedValue(new Error("upload rejected"));
    await expect(saveProfileImage("cover", { uri: "file:///x.jpg", name: "cover.jpg", mimeType: "image/jpeg" }))
      .rejects.toThrow("upload rejected");
    expect(invalidate).not.toHaveBeenCalled();
  });

  // A broadcast is a side effect, not the point of the save.
  it("still resolves when the invalidation broadcast fails", async () => {
    invalidate.mockRejectedValue(new Error("offline"));
    await expect(saveProfileImage("avatar", { uri: "file:///x.jpg", name: "avatar.jpg", mimeType: "image/jpeg" }))
      .resolves.toMatchObject({ avatar_url: "https://cdn/a.jpg?v=2" });
  });

  it("announces a removal through the same channel as an upload", async () => {
    await announceProfileMediaChange("cover");
    expect(invalidate).toHaveBeenCalledWith(["profile"], "profile_cover_updated", expect.any(Array));
  });
});

describe("labels", () => {
  it("names each surface the way the product does", () => {
    expect(profileImageLabel("avatar")).toBe("Profile photo");
    expect(profileImageLabel("cover")).toBe("Cover photo");
  });
});
