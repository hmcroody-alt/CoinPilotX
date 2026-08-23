import { fireEvent, render, waitFor } from "@testing-library/react-native";
import React from "react";
import { GroupsScreen } from "../GroupsScreen";

const mockSetBottomNavHidden = jest.fn();

jest.mock("@react-navigation/native", () => ({
  useFocusEffect: (callback: () => void | (() => void)) => require("react").useEffect(callback, [callback])
}));

jest.mock("../../navigation/BottomNavVisibility", () => ({
  useBottomNavSurface: () => ({
    contentPadding: {},
    handlers: { onScroll: jest.fn(), onScrollBeginDrag: jest.fn(), scrollEventThrottle: 16 }
  }),
  useBottomNavVisibility: () => ({ setBottomNavHidden: mockSetBottomNavHidden })
}));

jest.mock("react-native-safe-area-context", () => ({
  useSafeAreaInsets: () => ({ top: 0, right: 0, bottom: 34, left: 0 })
}));

jest.mock("../../community/communityCreateIntent", () => ({
  takeCommunityCreateIntent: jest.fn(() => null)
}));

jest.mock("../../api/groups", () => ({
  archiveGroup: jest.fn(),
  createGroup: jest.fn(),
  createRoom: jest.fn(),
  deleteGroup: jest.fn(),
  deleteRoom: jest.fn(),
  getGroupDetail: jest.fn(),
  joinGroup: jest.fn(),
  joinRoom: jest.fn(),
  leaveGroup: jest.fn(),
  listGroups: jest.fn(async () => ({ groups: [], rooms: [], has_more: false, next_offset: 0 })),
  listRooms: jest.fn(async () => []),
  loadCachedGroupDetail: jest.fn(),
  loadCachedGroups: jest.fn(),
  manageRoom: jest.fn(),
  openGroupChat: jest.fn(),
  removeGroupMember: jest.fn(),
  reportGroup: jest.fn(),
  setGroupMemberRole: jest.fn()
}));

describe("GroupsScreen creation sheet navigation clearance", () => {
  beforeEach(() => mockSetBottomNavHidden.mockClear());

  it("hides the dock for Group and Room creation and restores it whenever the sheet closes", async () => {
    const screen = render(<GroupsScreen />);

    const createGroup = await screen.findByText("Create Group");
    fireEvent.press(createGroup);
    await waitFor(() => expect(mockSetBottomNavHidden).toHaveBeenLastCalledWith(true));

    fireEvent.press(screen.getByText("Close"));
    await waitFor(() => expect(mockSetBottomNavHidden).toHaveBeenLastCalledWith(false));

    fireEvent.press(screen.getByText("Start Room"));
    await waitFor(() => expect(mockSetBottomNavHidden).toHaveBeenLastCalledWith(true));

    fireEvent.press(screen.getByText("Close"));
    await waitFor(() => expect(mockSetBottomNavHidden).toHaveBeenLastCalledWith(false));
  });

  it("restores the dock if the screen unmounts while creation is open", async () => {
    const screen = render(<GroupsScreen />);
    fireEvent.press(await screen.findByText("Create Group"));
    await waitFor(() => expect(mockSetBottomNavHidden).toHaveBeenLastCalledWith(true));

    screen.unmount();
    expect(mockSetBottomNavHidden).toHaveBeenLastCalledWith(false);
  });

  it("restores the dock after successful creation", async () => {
    const screen = render(<GroupsScreen />);
    fireEvent.press(await screen.findByText("Create Group"));
    fireEvent.changeText(screen.getByLabelText("Group name"), "Trail Crew");
    fireEvent.press(screen.getAllByText("Create Group").at(-1)!);

    await waitFor(() => expect(mockSetBottomNavHidden).toHaveBeenLastCalledWith(false));
  });
});
