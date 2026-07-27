type HomeReselectHandler = () => void | Promise<void>;

let handler: HomeReselectHandler | null = null;

export function registerHomeReselectHandler(nextHandler: HomeReselectHandler) {
  handler = nextHandler;
  return () => {
    if (handler === nextHandler) handler = null;
  };
}

export function triggerHomeReselect() {
  if (!handler) return false;
  Promise.resolve(handler()).catch(() => undefined);
  return true;
}
