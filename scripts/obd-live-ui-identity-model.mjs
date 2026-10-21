/** Explicit renderer-only identity/connection model. Other stream IPC keeps the
 * real handler, including the acceptance process's physical-I/O deny gate. */
export async function installRealtimeUiIdentityModel(app, profiles, { model = "vLinker", generation = "981" } = {}) {
  await app.evaluate(({ ipcMain }, { profiles, model, generation }) => {
    const stream = ipcMain._invokeHandlers.get("diagnostics:manufacturerStream");
    if (typeof stream !== "function") throw new Error("manufacturer stream handler missing");
    const state = globalThis.__realtimeUiIdentity = { profiles, model, generation, connected: true, calls: [], jobs: new Map(), next: 0 };
    ipcMain.removeHandler("diagnostics:connection");
    ipcMain.handle("diagnostics:connection", (_event, request) => request.action === "status"
      ? { ok: true, purpose: "diagnostic", connected: state.connected, model: state.model,
        selectedDeviceId: "explicit-ui-identity-model", linkState: state.connected ? "connected" : "idle" }
      : { ok: false, error: "ui-identity-model-status-only" });
    ipcMain.removeHandler("diagnostics:manufacturerStream");
    ipcMain.handle("diagnostics:manufacturerStream", (event, request) => {
      if (["identify", "identify-vehicle"].includes(request.action)) {
        state.calls.push(request);
        const profileId = state.profiles[request.action === "identify-vehicle" ? 9 : request.ecuId];
        const id = "ui-identity-model-" + ++state.next;
        state.jobs.set(id, profileId ? { ok: true, identityOnly: true, identifiedProfileId: profileId,
          ...(request.action === "identify-vehicle" ? { vehicleGeneration: state.generation, vehicleIdentitySha256: "c".repeat(64) } : {}) }
          : { ok: false, error: "identity-mismatch" });
        return { ok: true, sessionId: id };
      }
      if (request.action === "status" && state.jobs.has(request.sessionId)) return {
        ok: true, state: "finished", released: true, events: [], cursor: 0, connectionEvents: [], connectionCursor: 0,
        hasMore: false, hasMoreConnectionEvents: false, result: state.jobs.get(request.sessionId),
      };
      return globalThis.__realtimeUiReadHandler ? globalThis.__realtimeUiReadHandler(event, request) : stream(event, request);
    });
  }, { profiles, model, generation });
}

export async function selectIdentifiedRealtimeUnit(app, page, { systemId, ecuId, profileId }) {
  await page.waitForFunction(() => !document.querySelector('[data-testid="eng-system"]')?.disabled);
  // Remount even when selecting another version of the same control unit.
  await page.getByTestId("eng-system").selectOption("");
  await app.evaluate((_electron, { ecuId, profileId }) => { globalThis.__realtimeUiIdentity.profiles[ecuId] = profileId; }, { ecuId, profileId });
  await page.getByTestId("eng-system").selectOption(systemId);
  await page.waitForFunction(profileId => document.querySelector('[data-testid="offline-realtime"]')?.dataset.profileId === profileId, profileId);
  await page.waitForFunction(() => document.querySelector('[data-testid="offline-parameters"]')?.getAttribute("aria-busy") === "false");
}
