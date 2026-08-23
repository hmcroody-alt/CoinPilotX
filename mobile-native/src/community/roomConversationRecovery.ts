import { resolveRoomConversation } from "../api/groups";

export async function recoverRoomConversation(roomId: string, currentConversationId: number) {
  const resolved = await resolveRoomConversation(roomId);
  const conversationId = Number(resolved.conversation_id || 0);
  if (!conversationId) throw new Error("Room chat could not be repaired.");
  return { conversationId, changed: conversationId !== currentConversationId };
}
