import { resolveRoomConversation } from "../../api/groups";
import { recoverRoomConversation } from "../roomConversationRecovery";

jest.mock("../../api/groups", () => ({ resolveRoomConversation: jest.fn() }));

const mockResolveRoomConversation = resolveRoomConversation as jest.MockedFunction<typeof resolveRoomConversation>;

describe("room conversation recovery", () => {
  it("returns the repaired canonical id when the room mapping changed", async () => {
    mockResolveRoomConversation.mockResolvedValue({ room_id: "41", conversation_id: 912, can_message: true });
    await expect(recoverRoomConversation("41", 41)).resolves.toEqual({ conversationId: 912, changed: true });
    expect(mockResolveRoomConversation).toHaveBeenCalledWith("41");
  });

  it("reloads the current thread when reconciliation confirms the same id", async () => {
    mockResolveRoomConversation.mockResolvedValue({ room_id: "41", conversation_id: 912, can_message: true });
    await expect(recoverRoomConversation("41", 912)).resolves.toEqual({ conversationId: 912, changed: false });
  });

  it("does not accept an unusable repair response", async () => {
    mockResolveRoomConversation.mockResolvedValue({ room_id: "41", conversation_id: 0 });
    await expect(recoverRoomConversation("41", 41)).rejects.toThrow("Room chat could not be repaired.");
  });
});
