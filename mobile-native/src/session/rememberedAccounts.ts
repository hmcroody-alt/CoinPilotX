import AsyncStorage from "@react-native-async-storage/async-storage";
import { PulseUser } from "../api/auth";

const REMEMBERED_ACCOUNTS_KEY = "pulsesoc.native.session.rememberedAccounts.v1";
const MAX_REMEMBERED_ACCOUNTS = 5;

export type RememberedAccount = {
  userId: number;
  username: string;
  displayName: string;
  avatarUrl: string;
  lastUsedAt: number;
};

export async function listRememberedAccounts(): Promise<RememberedAccount[]> {
  try {
    const raw = await AsyncStorage.getItem(REMEMBERED_ACCOUNTS_KEY);
    if (!raw) return [];
    const parsed = JSON.parse(raw);
    if (!Array.isArray(parsed)) return [];
    return parsed
      .filter((entry): entry is RememberedAccount => Number(entry?.userId || 0) > 0)
      .sort((a, b) => b.lastUsedAt - a.lastUsedAt);
  } catch {
    return [];
  }
}

export async function rememberAccount(user: PulseUser) {
  const userId = Number(user.user_id || 0);
  if (!userId) return;
  const existing = await listRememberedAccounts();
  const next: RememberedAccount[] = [
    {
      userId,
      username: String(user.username || ""),
      displayName: String(user.display_name || user.full_name || user.username || ""),
      avatarUrl: String(user.avatar_url || ""),
      lastUsedAt: Date.now()
    },
    ...existing.filter((entry) => entry.userId !== userId)
  ].slice(0, MAX_REMEMBERED_ACCOUNTS);
  await AsyncStorage.setItem(REMEMBERED_ACCOUNTS_KEY, JSON.stringify(next));
}

export async function forgetAccount(userId: number) {
  const existing = await listRememberedAccounts();
  const next = existing.filter((entry) => entry.userId !== userId);
  await AsyncStorage.setItem(REMEMBERED_ACCOUNTS_KEY, JSON.stringify(next));
}
