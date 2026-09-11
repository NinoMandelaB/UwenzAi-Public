// Network and browser APIs can stay pending forever. A deadline bounds UI
// cleanup even when the underlying API ignores AbortSignal.
export const CLEANUP_TIMEOUT_MS = 2000;

export function boundedTask(task, timeout = CLEANUP_TIMEOUT_MS) {
  const controller = new AbortController();
  let timer;
  const deadline = new Promise((_, reject) => {
    timer = setTimeout(() => {
      controller.abort();
      reject(new DOMException("Cleanup timed out.", "TimeoutError"));
    }, timeout);
  });
  return Promise.race([
    Promise.resolve().then(() => task(controller.signal)),
    deadline,
  ]).finally(() => clearTimeout(timer));
}
