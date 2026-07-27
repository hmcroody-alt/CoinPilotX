// Pure, dependency-free helpers for computing play order across a queue.
// Kept separate from pulseRadio.ts so the ordering/shuffle math can be unit
// tested without touching expo-av or native playback state.

export function buildSequentialOrder(length: number): number[] {
  return Array.from({ length }, (_, index) => index);
}

// Fisher-Yates shuffle that pins `keepFirst` (the currently playing index,
// if any) to the front of the resulting order so shuffling never interrupts
// the track already playing.
export function buildShuffledOrder(
  length: number,
  keepFirst: number,
  random: () => number = Math.random
): number[] {
  const rest = buildSequentialOrder(length).filter((index) => index !== keepFirst);
  for (let i = rest.length - 1; i > 0; i -= 1) {
    const j = Math.floor(random() * (i + 1));
    const tmp = rest[i];
    rest[i] = rest[j];
    rest[j] = tmp;
  }
  if (keepFirst >= 0 && keepFirst < length) return [keepFirst, ...rest];
  return rest;
}

export type RepeatMode = "off" | "queue" | "one";

// Given the current position within `order`, compute the next order
// position to play. Returns null when playback should stop (end of queue
// reached with repeat disabled).
export function nextOrderPosition(
  orderLength: number,
  currentPosition: number,
  repeatMode: RepeatMode
): number | null {
  if (orderLength === 0) return null;
  if (repeatMode === "one") return currentPosition;
  const next = currentPosition + 1;
  if (next < orderLength) return next;
  if (repeatMode === "queue") return 0;
  return null;
}

export function previousOrderPosition(
  orderLength: number,
  currentPosition: number,
  repeatMode: RepeatMode
): number | null {
  if (orderLength === 0) return null;
  if (repeatMode === "one") return currentPosition;
  const prev = currentPosition - 1;
  if (prev >= 0) return prev;
  if (repeatMode === "queue") return orderLength - 1;
  return null;
}

export function nextRepeatMode(mode: RepeatMode): RepeatMode {
  if (mode === "off") return "queue";
  if (mode === "queue") return "one";
  return "off";
}

// Re-map an order array after an item at `fromIndex` in the underlying
// queue moves to `toIndex`. Also used after removal (toIndex omitted).
export function reindexOrderAfterMove(order: number[], fromIndex: number, toIndex: number): number[] {
  return order.map((index) => {
    if (index === fromIndex) return toIndex;
    if (fromIndex < toIndex && index > fromIndex && index <= toIndex) return index - 1;
    if (fromIndex > toIndex && index >= toIndex && index < fromIndex) return index + 1;
    return index;
  });
}

export function reindexOrderAfterRemoval(order: number[], removedIndex: number): number[] {
  return order
    .filter((index) => index !== removedIndex)
    .map((index) => (index > removedIndex ? index - 1 : index));
}
