import React from "react";
import { fireEvent, render, waitFor } from "@testing-library/react-native";

jest.mock("expo-image-picker", () => ({
  requestMediaLibraryPermissionsAsync: jest.fn(),
  launchImageLibraryAsync: jest.fn(),
  MediaTypeOptions: { Images: "Images" }
}));

const mockGetMyProfile = jest.fn();
const mockUpdateProfile = jest.fn();
const mockUpdateProfileTheme = jest.fn();

jest.mock("../../api/profile", () => ({
  getMyProfile: (...args: unknown[]) => mockGetMyProfile(...args),
  updateProfile: (...args: unknown[]) => mockUpdateProfile(...args),
  updateProfileTheme: (...args: unknown[]) => mockUpdateProfileTheme(...args),
  uploadProfileAvatar: jest.fn(),
  uploadProfileCover: jest.fn(),
  removeProfileAvatar: jest.fn(),
  removeProfileCover: jest.fn()
}));

import { ProfileEditScreen } from "../ProfileEditScreen";

const profile = {
  user_id: 7,
  display_name: "Ada Pulse",
  username: "ada",
  bio: "Builder",
  profile_visibility: "public" as const,
  theme: {
    theme_key: "deep_space",
    accent_color: "#32e6b3",
    layout_key: "classic",
    motion_level: "balanced" as const
  }
};

beforeEach(() => {
  jest.clearAllMocks();
  mockGetMyProfile.mockResolvedValue(profile);
  mockUpdateProfile.mockResolvedValue(profile);
  mockUpdateProfileTheme.mockImplementation(async (theme) => theme);
});

describe("ProfileEditScreen customization lifecycle", () => {
  it("persists the selected theme, layout and motion then returns to Profile", async () => {
    const navigation = { goBack: jest.fn() };
    const view = render(<ProfileEditScreen navigation={navigation as never} />);

    await waitFor(() => expect(view.getByText("Solar Pulse")).toBeTruthy());
    fireEvent.press(view.getByText("Solar Pulse"));
    fireEvent.press(view.getByText("Creator"));
    fireEvent.press(view.getByText("Reduced"));
    fireEvent.press(view.getByText("Save"));

    await waitFor(() => expect(mockUpdateProfileTheme).toHaveBeenCalledWith(expect.objectContaining({
      theme_key: "solar_pulse",
      accent_color: "#ff9f43",
      layout_key: "creator",
      motion_level: "reduced"
    })));
    await waitFor(() => expect(navigation.goBack).toHaveBeenCalledTimes(1));
  });

  it("discards unsaved choices and returns to Profile on Cancel", async () => {
    const navigation = { goBack: jest.fn() };
    const view = render(<ProfileEditScreen navigation={navigation as never} />);

    await waitFor(() => expect(view.getByText("Solar Pulse")).toBeTruthy());
    fireEvent.press(view.getByText("Solar Pulse"));
    fireEvent.press(view.getByText("Cancel"));

    expect(mockUpdateProfileTheme).not.toHaveBeenCalled();
    expect(navigation.goBack).toHaveBeenCalledTimes(1);
  });

  it("stays in the editor and reports a theme failure after profile fields save", async () => {
    mockUpdateProfileTheme.mockRejectedValueOnce(new Error("Theme could not be saved."));
    const navigation = { goBack: jest.fn() };
    const view = render(<ProfileEditScreen navigation={navigation as never} />);

    await waitFor(() => expect(view.getByText("Save")).toBeTruthy());
    fireEvent.press(view.getByText("Save"));

    await waitFor(() => expect(view.getByText("Theme could not be saved.")).toBeTruthy());
    expect(navigation.goBack).not.toHaveBeenCalled();
  });
});
