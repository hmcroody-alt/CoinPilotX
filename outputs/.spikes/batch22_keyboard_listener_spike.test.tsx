import { Keyboard } from "react-native";
it("keyboard listener is capturable by spy", () => {
  const handlers: Record<string, () => void> = {};
  const spy = jest.spyOn(Keyboard, "addListener").mockImplementation(((event: string, cb: () => void) => {
    handlers[event] = cb;
    return { remove: () => undefined };
  }) as never);
  Keyboard.addListener("keyboardWillShow", () => undefined);
  expect(typeof handlers.keyboardWillShow).toBe("function");
  spy.mockRestore();
});
