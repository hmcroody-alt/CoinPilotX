import {
  buildSequentialOrder,
  buildShuffledOrder,
  nextOrderPosition,
  previousOrderPosition,
  nextRepeatMode,
  reindexOrderAfterMove,
  reindexOrderAfterRemoval
} from "../pulseRadioQueueOrder";

describe("pulseRadioQueueOrder", () => {
  it("builds a sequential order", () => {
    expect(buildSequentialOrder(4)).toEqual([0, 1, 2, 3]);
    expect(buildSequentialOrder(0)).toEqual([]);
  });

  it("shuffles while pinning the currently playing index first", () => {
    const order = buildShuffledOrder(6, 2, () => 0);
    expect(order[0]).toBe(2);
    expect(order.sort((a, b) => a - b)).toEqual([0, 1, 2, 3, 4, 5]);
  });

  it("shuffles the whole list when nothing is currently playing", () => {
    const order = buildShuffledOrder(4, -1, () => 0.99);
    expect(order.sort((a, b) => a - b)).toEqual([0, 1, 2, 3]);
  });

  it("advances sequentially and stops at the end when repeat is off", () => {
    expect(nextOrderPosition(3, 0, "off")).toBe(1);
    expect(nextOrderPosition(3, 1, "off")).toBe(2);
    expect(nextOrderPosition(3, 2, "off")).toBeNull();
  });

  it("wraps to the start when repeat queue is enabled", () => {
    expect(nextOrderPosition(3, 2, "queue")).toBe(0);
  });

  it("repeats the same position when repeat one is enabled", () => {
    expect(nextOrderPosition(3, 1, "one")).toBe(1);
    expect(previousOrderPosition(3, 1, "one")).toBe(1);
  });

  it("returns null for an empty queue", () => {
    expect(nextOrderPosition(0, 0, "queue")).toBeNull();
    expect(previousOrderPosition(0, 0, "queue")).toBeNull();
  });

  it("moves backward through the queue and wraps with repeat queue", () => {
    expect(previousOrderPosition(3, 1, "off")).toBe(0);
    expect(previousOrderPosition(3, 0, "off")).toBeNull();
    expect(previousOrderPosition(3, 0, "queue")).toBe(2);
  });

  it("cycles repeat mode off -> queue -> one -> off", () => {
    expect(nextRepeatMode("off")).toBe("queue");
    expect(nextRepeatMode("queue")).toBe("one");
    expect(nextRepeatMode("one")).toBe("off");
  });

  it("reindexes an order array after a queue item moves forward", () => {
    // queue [A,B,C,D] -> move index 0 to index 2 -> [B,C,A,D]
    const order = [0, 1, 2, 3];
    expect(reindexOrderAfterMove(order, 0, 2)).toEqual([2, 0, 1, 3]);
  });

  it("reindexes an order array after a queue item moves backward", () => {
    // queue [A,B,C,D] -> move index 3 to index 1 -> [A,D,B,C]
    const order = [0, 1, 2, 3];
    expect(reindexOrderAfterMove(order, 3, 1)).toEqual([0, 2, 3, 1]);
  });

  it("reindexes an order array after removal", () => {
    const order = [3, 1, 2, 0];
    expect(reindexOrderAfterRemoval(order, 1)).toEqual([2, 1, 0]);
  });
});
