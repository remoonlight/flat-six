/** Await the IPC result before checking it. Playwright waitForFunction treats a Promise as truthy. */
import { performance } from "node:perf_hooks";
export async function waitForIpc(page, predicate, argument, timeoutMs = 15000) {
  const deadline = performance.now() + timeoutMs;
  do {
    let timer;
    try {
      const value = await Promise.race([page.evaluate(predicate, argument), new Promise((_, reject) => {
        timer = setTimeout(() => reject(new Error(`IPC condition not met within ${timeoutMs} ms`)), Math.max(1, deadline - performance.now()));
      })]);
      if (value) return;
    } finally { clearTimeout(timer); }
    await new Promise((resolve) => setTimeout(resolve, Math.min(100, Math.max(1, deadline - performance.now()))));
  } while (performance.now() < deadline);
  throw new Error(`IPC condition not met within ${timeoutMs} ms`);
}
