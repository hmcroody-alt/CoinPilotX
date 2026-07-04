import AsyncStorage from "@react-native-async-storage/async-storage";

export async function readJsonCache<T>(key: string, normalize: (value: T) => T): Promise<T | null> {
  try {
    const cached = await AsyncStorage.getItem(key);
    if (!cached) return null;
    return normalize(JSON.parse(cached) as T);
  } catch {
    await AsyncStorage.removeItem(key).catch(() => undefined);
    return null;
  }
}

export async function writeJsonCache<T>(key: string, value: T) {
  await AsyncStorage.setItem(key, JSON.stringify(value));
}
