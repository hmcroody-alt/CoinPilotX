import { pulseApi } from "../pulseApi";
import { resolveRoomConversation } from "../groups";

jest.mock("../pulseApi", () => ({ pulseApi: jest.fn() }));

const mockPulseApi = pulseApi as jest.MockedFunction<typeof pulseApi>;

describe("room conversation API", () => {
  it("uses the room lifecycle id only in the resolver route", async () => {
    mockPulseApi.mockResolvedValue({ ok: true, room_id: "27", conversation_id: 804, can_message: true });
    await expect(resolveRoomConversation("27")).resolves.toMatchObject({ conversation_id: 804 });
    expect(mockPulseApi).toHaveBeenCalledWith("/api/pulse/communications/rooms/27/conversation");
  });

  it("resolves seeded room keys without treating them as conversation ids", async () => {
    mockPulseApi.mockResolvedValue({ ok: true, room_id: "cybersecurity", conversation_id: 805, can_message: true });
    await expect(resolveRoomConversation("cybersecurity")).resolves.toMatchObject({ conversation_id: 805 });
    expect(mockPulseApi).toHaveBeenCalledWith("/api/pulse/communications/rooms/cybersecurity/conversation");
  });
});
