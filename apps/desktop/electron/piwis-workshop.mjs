import fs from "node:fs/promises";
import { randomUUID } from "node:crypto";
import { buildWorkshopPreview, WORKSHOP_PROGRAMMING_ECUS } from "@porsche981/domain";

const text = (v) => typeof v === "string" && v.length > 0 && v.length <= 4096;
const list = (v, limit) => Array.isArray(v) && v.length <= limit;
const knownFields = (v, fields) => v !== null && typeof v === "object" && !Array.isArray(v)
  && Object.keys(v).every(key => fields.includes(key));

/** Maintenance flow coordinator. A trusted application recipe must supply every
 * vehicle operation and release proof; menu descriptions are never recipes.
 * The production default has no qualified recipe and cannot dispatch a step. */
export function createWorkshopExecution({ connection, gate, getRecipe = () => null, isVehicleBusy = () => false } = {}) {
  let current = null;
  const active = job => job && !job.released;
  const fail = error => ({ ok: false, error });
  const businessFailure = (result, fallback = "本次操作未完成。") => ({
    cause: text(result?.cause) ? result.cause : fallback,
    remedy: text(result?.remedy) ? result.remedy : "请检查车辆条件和诊断头连接，再决定是否重新执行。",
  });
  function recipeFor(functionId, generation) {
    const recipe = getRecipe(functionId, generation);
    if (!recipe) return null;
    if (recipe.id !== functionId || recipe.sourceQualified !== true || !text(recipe.title)
      || !/^[a-f0-9]{64}$/.test(recipe.sourceHash || "") || !Array.isArray(recipe.generations)
      || !recipe.generations.every(value => ["981", "982"].includes(value)) || !recipe.generations.includes(generation)
      || !Array.isArray(recipe.steps) || !recipe.steps.length || recipe.steps.length > 512
      || ![recipe.open, recipe.preflight, recipe.close].every(fn => typeof fn === "function")
      || recipe.resumeSupported === true && typeof recipe.checkResume !== "function") throw new Error("workshop-recipe-invalid");
    if (recipe.id === "psm-roller" && (!Array.isArray(recipe.allowedPersistence) || !recipe.allowedPersistence.length
      || !recipe.allowedPersistence.every(value => ["ignition-cycle", "persistent"].includes(value)))) throw new Error("workshop-recipe-invalid");
    const ids = new Set();
    const steps = recipe.steps.map(step => {
      if (!text(step.id) || ids.has(step.id) || !text(step.label) || !["software", "manual"].includes(step.kind)
        || typeof step.safeStopBefore !== "boolean" || typeof step.safeStopAfter !== "boolean"
        || step.kind === "software" && typeof step.execute !== "function"
        || step.kind === "manual" && step.execute !== undefined
        || step.verify !== undefined && typeof step.verify !== "function"
        || (step.safeStopBefore || step.safeStopAfter) && typeof recipe.stop !== "function") throw new Error("workshop-recipe-invalid");
      ids.add(step.id); return { ...step };
    });
    return { ...recipe, steps };
  }
  function snapshot(job = current) {
    if (!job) return { ok: true, busy: false, job: null };
    return { ok: true, busy: active(job), job: {
      id: job.id, functionId: job.functionId, generation: job.generation, title: job.recipe.title,
      phase: job.phase, currentIndex: job.index, cancelRequested: job.cancelRequested,
      cancelNotApplied: job.cancelNotApplied, released: job.released, failure: job.failure,
      releaseFailure: job.releaseFailure, resumeFailure: job.resumeFailure, connectionWarning: job.connectionWarning,
      resumeSupported: job.recipe.resumeSupported === true && job.failedIndex !== null,
      steps: job.steps.map(({ id, label, kind, status }) => ({ id, label, kind, status })),
      liveVerified: false,
    } };
  }
  async function admit(job) {
    if (isVehicleBusy()) throw new Error("vehicle-busy");
    const before = connection.snapshot();
    if (before.purpose !== "diagnostic" || !before.connected || !before.selectedDeviceId) throw new Error("diagnostic-connection-required");
    if (!connection.reserveDispatch()) throw new Error("vehicle-busy");
    job.reserved = true;
    const handoff = await connection.suspendForSession();
    if (handoff?.ok === false) throw new Error("workshop-handoff-failed");
    job.suspended = true;
    const after = connection.snapshot();
    if (after.selectedDeviceId !== before.selectedDeviceId || after.model !== before.model || after.purpose !== "diagnostic")
      throw new Error("workshop-device-changed");
    if (!gate.tryAcquire("workshop-execution")) throw new Error("vehicle-busy");
    job.lease = true; job.openAttempted = true;
    const opened = await job.recipe.open({ functionId: job.functionId, generation: job.generation,
      deviceId: before.selectedDeviceId, model: before.model, persistence: job.persistence });
    job.context = opened?.context;
    if (opened?.qualified !== true) throw new Error("workshop-client-unqualified");
  }
  async function finishRelease(job) {
    if (job.lease) { gate.release("workshop-execution"); job.lease = false; }
    if (job.suspended) {
      try {
        const resumed = await connection.resumeAfterSession({ nativeReleaseVerified: true });
        if (resumed?.ok === false) job.connectionWarning = "诊断头连接未恢复，请返回连接设置重新连接。";
      } catch { job.connectionWarning = "诊断头连接未恢复，请返回连接设置重新连接。"; }
    } else if (job.reserved) connection.releaseDispatch();
    job.reserved = false; job.suspended = false; job.openAttempted = false;
    job.released = true; job.phase = job.exitPhase;
    job.cancelNotApplied = job.cancelRequested && job.exitPhase === "completed";
  }
  async function end(job, phase) {
    if (phase === "cancelled" && job.steps[job.index]?.status === "waiting-manual") job.steps[job.index].status = "incomplete";
    job.exitPhase = phase; job.phase = "releasing";
    if (job.openAttempted) {
      try {
        const closed = await job.recipe.close(job.context);
        if (closed?.released !== true) throw new Error("workshop-release-unconfirmed");
      } catch (error) {
        job.rawError = error; job.phase = "release-pending";
        job.releaseFailure = { cause: "本次任务的诊断头释放尚未确认。", remedy: "请等待释放确认；当前不能开始另一项车辆操作。" };
        return;
      }
    }
    await finishRelease(job);
  }
  async function failJob(job, result, rawError) {
    job.rawError = rawError;
    const reason = { "vehicle-busy": "另一项车辆操作尚未结束。", "diagnostic-connection-required": "诊断头未连接或未选择诊断用途。",
      "workshop-handoff-failed": "诊断头尚未准备好执行本次操作。", "workshop-device-changed": "本次诊断头选择已变化。",
      "workshop-client-unqualified": "当前车辆客户端未通过身份与适用性核对。",
      "workshop-resume-identity-unqualified": "未能确认本次车辆与参与控制单元的完整身份。" }[rawError?.message];
    job.failure = businessFailure(result, reason || "本次操作未完成。");
    if (job.steps[job.index]?.status === "running") {
      job.steps[job.index].status = rawError || result?.uncertain === true ? "unconfirmed" : "failed";
      job.failedIndex = job.index;
    }
    await end(job, "failed");
  }
  async function stopAt(job, boundary) {
    job.phase = "stopping";
    const result = await job.recipe.stop(job.context, boundary);
    if (result?.ok !== true) { await failJob(job, result); return; }
    await end(job, "cancelled");
  }
  function checkResumeSource(job) {
    let available;
    try { available = recipeFor(job.functionId, job.generation); }
    catch (error) { throw new Error("workshop-resume-source-changed", { cause: error }); }
    if (!available || available.sourceHash !== job.recipe.sourceHash || available.resumeSupported !== true)
      throw new Error("workshop-resume-source-changed");
  }
  async function drive(job, { opening = false, checkingResume = false, resuming = false } = {}) {
    if (job.working) return;
    job.working = true;
    let readyToRun = !opening;
    try {
      if (opening) {
        if (checkingResume || resuming) checkResumeSource(job);
        await admit(job);
        const checked = checkingResume || resuming
          ? await job.recipe.checkResume(job.context, { stepId: job.steps[job.failedIndex]?.id })
          : await job.recipe.preflight(job.context);
        if (job.cancelRequested) { await end(job, "cancelled"); return; }
        if (checked?.ok !== true) {
          if (checkingResume || resuming) { job.resumeFailure = businessFailure(checked); await end(job, "failed"); }
          else await failJob(job, checked);
          return;
        }
        if (job.recipe.resumeSupported === true) {
          // A source-owned fingerprint of the vehicle and participating units,
          // captured by the fresh preflight/check. Procedure values and read
          // timestamps belong to condition checks, not this stable identity.
          if (typeof checked.resumeIdentity !== "string" || !/^[a-f0-9]{64}$/.test(checked.resumeIdentity))
            throw new Error("workshop-resume-identity-unqualified");
          if (checkingResume || resuming) {
            if (checked.resumeIdentity !== job.resumeIdentity) throw new Error("workshop-resume-identity-changed");
          } else job.resumeIdentity = checked.resumeIdentity;
        }
        if (checkingResume || resuming) checkResumeSource(job);
        if (checkingResume) { await end(job, "resume-ready"); return; }
        if (resuming) { job.failure = null; job.resumeFailure = null; }
      }
      readyToRun = true;
      while (job.index < job.steps.length) {
        const step = job.recipe.steps[job.index], state = job.steps[job.index];
        if (job.cancelRequested && step.safeStopBefore && state.status !== "completed") { await stopAt(job, { stepId: step.id, position: "before" }); return; }
        if (step.kind === "manual" && state.status !== "completed") {
          job.phase = "waiting-manual"; state.status = "waiting-manual"; return;
        }
        job.phase = "running";
        if (step.kind === "software") {
          state.status = "running";
          const result = await step.execute(job.context, { resuming: resuming && job.index === job.failedIndex });
          if (result?.ok !== true) { await failJob(job, result); return; }
          state.status = "completed";
        }
        job.index++;
        if (job.cancelRequested && step.safeStopAfter) { await stopAt(job, { stepId: step.id, position: "after" }); return; }
      }
      await end(job, "completed");
    } catch (error) {
      if ((checkingResume || resuming) && !readyToRun) {
        const cause = { "workshop-resume-source-changed": "原厂作业已变化或不再支持续执行，请重新开始本次流程。",
          "workshop-resume-identity-unqualified": "未能确认本次车辆与参与控制单元的完整身份。",
          "workshop-resume-identity-changed": "本次车辆或参与控制单元与原流程不一致，不能从中断处继续。" }[error?.message];
        job.rawError = error; job.resumeFailure = businessFailure(null, cause || "未能完成续执行条件核对。"); await end(job, "failed");
      } else await failJob(job, null, error);
    }
    finally { job.working = false; }
  }
  async function manualContinue(job) {
    if (job.working) return;
    job.working = true;
    try {
      const step = job.recipe.steps[job.index];
      if (step.verify) {
        job.phase = "running"; job.steps[job.index].status = "running";
        const checked = await step.verify(job.context);
        if (checked?.ok !== true) {
          job.steps[job.index].status = "running"; await failJob(job, checked); return;
        }
      }
      job.steps[job.index].status = "completed";
    } catch (error) { job.steps[job.index].status = "running"; await failJob(job, null, error); return; }
    finally { job.working = false; }
    void drive(job);
  }
  async function reconcile(job) {
    if (job.phase !== "release-pending" || job.working || typeof job.recipe.observeRelease !== "function") return;
    job.working = true;
    try {
      if (await job.recipe.observeRelease(job.context) === true) { job.releaseFailure = null; await finishRelease(job); }
    } catch (error) { job.rawError = error; }
    finally { job.working = false; }
  }
  return {
    isIdle: () => !active(current),
    isOwnedActive: owner => active(current) && current.owner === owner,
    snapshot: () => snapshot(),
    async handle(request, owner) {
      if (!Number.isInteger(owner) || owner < 1 || !knownFields(request, ["action", "functionId", "generation", "jobId", "stepId", "persistence"])) return fail("workshop-request-invalid");
      const action = request.action;
      if (["capability", "start"].includes(action)) {
        if (Object.keys(request).some(key => !["action", "functionId", "generation", "persistence"].includes(key))
          || !text(request.functionId) || !["981", "982"].includes(request.generation)
          || (request.functionId === "psm-roller" ? !["ignition-cycle", "persistent"].includes(request.persistence) : request.persistence !== undefined)) return fail("workshop-request-invalid");
        let recipe;
        try { recipe = recipeFor(request.functionId, request.generation); }
        catch { return fail("workshop-recipe-invalid"); }
        if (recipe && request.functionId === "psm-roller" && !recipe.allowedPersistence?.includes(request.persistence)) recipe = null;
        if (action === "capability") return { ok: true, ready: !!recipe, busy: active(current) || isVehicleBusy(),
          connectedForDiagnostics: connection?.snapshot().purpose === "diagnostic" && connection?.snapshot().connected === true,
          reason: recipe ? null : "缺少已核实的完整原厂作业及车辆客户端绑定，当前不能执行。" };
        if (!recipe) return fail("workshop-recipe-unavailable");
        if (active(current) || isVehicleBusy()) return fail("vehicle-busy");
        const job = { id: randomUUID(), owner, functionId: request.functionId, generation: request.generation, persistence: request.persistence, recipe,
          phase: "preparing", index: 0, failedIndex: null, resumeIdentity: null, working: false, released: false, reserved: false, lease: false,
          suspended: false, openAttempted: false, cancelRequested: false, cancelNotApplied: false, failure: null,
          releaseFailure: null, resumeFailure: null, connectionWarning: null, steps: recipe.steps.map(step => ({ id: step.id, label: step.label, kind: step.kind, status: "pending" })) };
        current = job; void drive(job, { opening: true }); return snapshot(job);
      }
      if (!["status", "cancel", "continue", "check-resume", "resume", "dismiss"].includes(action)
        || Object.keys(request).some(key => !(action === "continue" ? ["action", "jobId", "stepId"] : ["action", "jobId"]).includes(key))) return fail("workshop-request-invalid");
      const job = current;
      if (!job) return action === "status" ? snapshot(null) : fail("workshop-job-unknown");
      if (job.owner !== owner) return action === "status" ? { ok: true, busy: active(job), job: null } : fail("workshop-owner-mismatch");
      if (request.jobId !== undefined && request.jobId !== job.id) return fail("workshop-job-unknown");
      if (action !== "status" && request.jobId !== job.id) return fail("workshop-job-unknown");
      if (action === "status") { await reconcile(job); return snapshot(job); }
      if (action === "dismiss") {
        if (active(job)) return fail("vehicle-busy"); current = null; return snapshot(null);
      }
      if (action === "cancel") {
        if (!active(job)) return fail("workshop-not-running");
        job.cancelRequested = true;
        if (job.phase === "waiting-manual" && !job.working && job.recipe.steps[job.index].safeStopBefore) void drive(job);
        return snapshot(job);
      }
      if (action === "continue") {
        if (job.phase !== "waiting-manual" || job.working || request.stepId !== job.steps[job.index].id) return fail("workshop-not-waiting-manual");
        void manualContinue(job); return snapshot(job);
      }
      if (action === "check-resume") {
        if (job.phase !== "failed" || !job.released || job.failedIndex === null || job.recipe.resumeSupported !== true) return fail("workshop-resume-unavailable");
        job.phase = "checking-resume"; job.released = false; job.cancelRequested = false; job.resumeFailure = null;
        void drive(job, { opening: true, checkingResume: true }); return snapshot(job);
      }
      if (action === "resume") {
        if (job.phase !== "resume-ready" || !job.released) return fail("workshop-resume-unavailable");
        job.index = job.failedIndex; job.phase = "preparing"; job.released = false; job.cancelRequested = false; job.resumeFailure = null;
        void drive(job, { opening: true, resuming: true }); return snapshot(job);
      }
      return fail("workshop-request-invalid");
    },
  };
}
/** OEM linked-job execution, reusing the maintenance transport/stop/release
 * coordinator. No production binding is supplied by default. Only source-owned
 * callbacks are accepted; the renderer cannot supply a step, file or payload. */
export function createFirmwareExecution({ selection, connection, gate, getClient = () => null, isVehicleBusy = () => false } = {}) {
  let flow = null, prepared = null;
  const deny = error => ({ ok: false, error });
  const bindingFor = plan => {
    const client = getClient(plan.job.id);
    if (!knownFields(client, ["sourceQualified", "sourceHash", "open", "preflight", "readState", "close", "observeRelease", "stop", "steps"])
      || client.sourceQualified !== true || client.sourceHash !== plan.job.sourceHash
      || ![client.open, client.preflight, client.readState, client.close].every(fn => typeof fn === "function")
      || client.observeRelease !== undefined && typeof client.observeRelease !== "function"
      || client.stop !== undefined && typeof client.stop !== "function"
      || !plan.job.participants.every(unit => typeof unit.targetSoftware === "string" && unit.targetSoftware.length > 0)
      || !Array.isArray(client.steps) || !client.steps.length || client.steps.length > 510) return null;
    const units = new Set(plan.job.participants.map(unit => unit.ecu)), ids = new Set();
    for (const step of client.steps) {
      if (!knownFields(step, ["id", "label", "kind", "ecu", "phase", "safeStopBefore", "safeStopAfter", "execute", "verify"])
        || !text(step.id) || ids.has(step.id) || step.id === "app-final-full-readback" || !text(step.label) || !["software", "manual"].includes(step.kind)
        || !["prepare", "flash", "verify", "finish"].includes(step.phase)
        || step.ecu !== undefined && !units.has(step.ecu)
        || step.phase === "flash" && (!units.has(step.ecu) || step.kind !== "software")
        || typeof step.safeStopBefore !== "boolean" || typeof step.safeStopAfter !== "boolean"
        || step.kind === "software" && typeof step.execute !== "function"
        || step.kind === "manual" && step.execute !== undefined
        || step.verify !== undefined && typeof step.verify !== "function"
        || (step.safeStopBefore || step.safeStopAfter) && typeof client.stop !== "function") return null;
      ids.add(step.id);
    }
    if (plan.job.participants.some(unit => !client.steps.some(step => step.ecu === unit.ecu && step.phase === "flash"))) return null;
    return { ...client, steps: client.steps.map(step => ({ ...step })) };
  };
  const executor = createWorkshopExecution({ connection, gate, isVehicleBusy,
    getRecipe: id => prepared?.id === id ? prepared.recipe : null });
  const checkMessage = error => ({ ok: false, cause: ({
    "firmware-current-changed": "车辆、诊断头或当前版本已变化，本次刷写未开始。",
    "firmware-source-changed": "原厂作业资料已变化，本次刷写未开始。",
    "firmware-file-changed": "原厂文件已变化，本次刷写未开始。",
  })[error] || "刷写前核对尚未通过，本次未开始。", remedy: "请重新核对全部版本、原厂作业及连接条件。" });
  function result(owner) {
    const state = executor.snapshot();
    if (!flow || flow.owner !== owner || !state.job) return { ok: true, busy: !executor.isIdle(), job: null };
    const terminal = state.job.released;
    return { ...state, job: { ...state.job, ecu: flow.ecu,
      units: flow.units.map(unit => ({ ecu: unit.ecu, label: unit.label, originalVersion: unit.originalVersion,
        targetVersion: unit.targetVersion, actualVersion: unit.actualVersion,
        status: terminal && !unit.attempted ? "unexecuted" : terminal && !["completed", "failed", "unconfirmed"].includes(unit.status) ? "unconfirmed" : unit.status })) } };
  }
  function recordReadback(jobFlow, inspected, completed = true) {
    for (const unit of jobFlow.units) {
      const actual = inspected.rows.find(row => row.ecu === unit.ecu);
      unit.actualVersion = actual?.actualVersion ?? null;
      if (unit.attempted) {
        if (completed) unit.status = actual?.matched ? "completed" : "unconfirmed";
        else if (unit.status !== "failed") unit.status = "unconfirmed";
      }
    }
  }
  return {
    isIdle: () => executor.isIdle(), isOwnedActive: owner => executor.isOwnedActive(owner),
    canBind(plan) { try { return !!plan && !!bindingFor(plan); } catch { return false; } },
    async handle(request, owner) {
      if (!Number.isInteger(owner) || owner < 1 || !knownFields(request, ["action", "ecu", "listId", "targetId", "reviewId", "jobId", "stepId"])) return deny("firmware-execution-invalid");
      if (request.action !== "start") {
        if (!["status", "cancel", "continue", "dismiss"].includes(request.action)
          || Object.keys(request).some(key => !(request.action === "continue" ? ["action", "jobId", "stepId"] : ["action", "jobId"]).includes(key))) return deny("firmware-execution-invalid");
        const state = await executor.handle(request, owner);
        if (!state.ok) return state;
        if (request.action === "dismiss") { flow = null; prepared = null; }
        return result(owner);
      }
      if (Object.keys(request).some(key => !["action", "ecu", "listId", "targetId", "reviewId"].includes(key))
        || ![request.ecu, request.listId, request.targetId, request.reviewId].every(text)) return deny("firmware-execution-invalid");
      if (!executor.isIdle() || isVehicleBusy()) return deny("vehicle-busy");
      const plan = selection?.takeReviewed(request, owner);
      if (!plan) return deny("firmware-selection-stale");
      let client; try { client = bindingFor(plan); } catch { /* Unqualified source remains disabled. */ }
      if (!client) return deny("firmware-client-unavailable");
      const jobFlow = { owner, ecu: request.ecu, readbackAttempted: false, units: plan.rows.map(row => ({ ecu: row.ecu, label: row.label,
        originalVersion: row.currentVersion, targetVersion: row.targetVersion, actualVersion: null, status: "waiting", attempted: false })) };
      const recipe = { id: "oem-flash:" + randomUUID(), title: "原厂固件刷写", generations: [plan.job.conditions.generation],
        sourceQualified: true, sourceHash: plan.job.sourceHash,
        open: async info => {
          const opened = await client.open({ ...info, job: structuredClone(plan.job) });
          return { ...opened, qualified: opened?.qualified === true && opened?.payloadsVerified === true };
        },
        preflight: async context => {
          const checked = await plan.verifyCurrent(await client.readState(context));
          if (!checked.ok) return checkMessage(checked.error);
          return client.preflight(context);
        },
        close: async context => {
          // A failed source step can have changed an ECU. Attempt one read-only
          // reconciliation before release, retaining the failed/unknown status.
          // Never repeat a final read already attempted by the normal/stop path.
          if (!jobFlow.readbackAttempted && jobFlow.units.some(unit => unit.attempted)) {
            jobFlow.readbackAttempted = true;
            try { recordReadback(jobFlow, plan.inspectResult(await client.readState(context)), false); }
            catch (error) { jobFlow.rawReadbackError = error; /* Release still runs. */ }
          }
          return client.close(context);
        },
        ...(typeof client.observeRelease === "function" ? { observeRelease: context => client.observeRelease(context) } : {}),
        ...(typeof client.stop === "function" ? { stop: async (context, boundary) => {
          const stopped = await client.stop(context, boundary);
          if (stopped?.ok === true) {
            jobFlow.readbackAttempted = true;
            try { recordReadback(jobFlow, plan.inspectResult(await client.readState(context))); }
            catch (error) { jobFlow.rawReadbackError = error; /* Unknown versions remain null. */ }
          }
          return stopped;
        } } : {}),
        steps: client.steps.map(step => {
          const wrapped = { ...step };
          if (step.kind === "software") wrapped.execute = async context => {
            const unit = jobFlow.units.find(unit => unit.ecu === step.ecu);
            if (unit && step.phase === "flash") { unit.attempted = true; unit.actualVersion = null; unit.status = "flashing"; }
            if (unit && step.phase === "verify") unit.status = "checking";
            try {
              const response = await step.execute(context);
              if (unit && response?.ok !== true) unit.status = response?.uncertain === true ? "unconfirmed" : "failed";
              else if (unit && unit.attempted) unit.status = "waiting";
              return response;
            } catch (error) { if (unit) unit.status = "unconfirmed"; throw error; }
          };
          return wrapped;
        }),
      };
      // A later source step can affect an earlier ECU. Read the complete linked
      // range again after all original steps; never copy the requested version.
      recipe.steps.push({ id: "app-final-full-readback", label: "读取全部参与单元的实际版本", kind: "software", safeStopBefore: false, safeStopAfter: false,
        execute: async context => {
          for (const unit of jobFlow.units) if (unit.attempted) unit.status = "checking";
          jobFlow.readbackAttempted = true;
          const checked = plan.inspectResult(await client.readState(context)); recordReadback(jobFlow, checked);
          return checked.ok ? { ok: true } : { ok: false, uncertain: true, cause: "刷写后的完整版本核对未通过。", remedy: "结果未确认，请按原厂恢复方案核对；不自动重发刷写。" };
        } });
      prepared = { id: recipe.id, recipe }; flow = jobFlow;
      const started = await executor.handle({ action: "start", functionId: recipe.id, generation: plan.job.conditions.generation }, owner);
      if (!started.ok) { prepared = null; flow = null; return started; }
      return result(owner);
    },
  };
}

const condition = (c, current = false) => knownFields(c, ["field", "values"]) &&
  (current ? ["PTNR", "HWTNR", "SWVERSION"] : ["PRODUKTSCHLUESSEL", "AUSSTATTUNG", "MODELLJAHR"]).includes(c.field)
  && list(c.values, 20) && c.values.length > 0 && c.values.every((v) => text(v) || (!current && c.field === "PRODUKTSCHLUESSEL" && v === ""));
export function validateWorkshopFlashIndex(doc) {
  if (!knownFields(doc, ["schemaVersion", "sources", "rules"]) || doc.schemaVersion !== 1 || !list(doc.sources, 20) || !doc.sources.length || !list(doc.rules, 2000)) return false;
  if (!doc.sources.every((s) => knownFields(s, ["file", "sha256"]) && text(s.file) && /^[a-f0-9]{64}$/.test(s.sha256))) return false;
  const sources = new Set(doc.sources.map((s) => s.file));
  if (sources.size !== doc.sources.length) return false;
  const ids = new Set();
  return doc.rules.every((r) => {
    if (!knownFields(r, ["id", "generation", "ecu", "description", "conditions", "targets", "source", "currentEcus", "kind", "familyEvidence"])
      || !text(r.id) || ids.has(r.id) || !["981", "982"].includes(r.generation) || !WORKSHOP_PROGRAMMING_ECUS.includes(r.ecu)
      || !text(r.description) || !sources.has(r.source) || !list(r.conditions, 20) || !r.conditions.length || !list(r.targets, 20) || !r.targets.length) return false;
    const kind = r.kind ?? "firmware";
    if (!["firmware", "dataset", "blocked"].includes(kind) || ![undefined, "product-key", "shared-platform", "description"].includes(r.familyEvidence)) return false;
    if (r.currentEcus !== undefined && (!list(r.currentEcus, 20) || !r.currentEcus.every((e) => knownFields(e, ["logicalLink", "conditions"]) && text(e.logicalLink)
      && list(e.conditions, 20) && e.conditions.every((c) => condition(c, true))))) return false;
    ids.add(r.id);
    return r.conditions.every((c) => condition(c)) && r.targets.every((t) => {
      if (!knownFields(t, ["logicalLink", "softwarePartNumber", "session"]) || (t.session !== null && !text(t.session))) return false;
      if (kind === "blocked") return t.softwarePartNumber === null && (t.logicalLink === null || text(t.logicalLink));
      if (kind === "dataset") return text(t.logicalLink) && t.softwarePartNumber === null && text(t.session);
      return text(t.logicalLink) && text(t.softwarePartNumber) && t.softwarePartNumber.toLowerCase() !== "noflash";
    });
  });
}

/** Fixed application-owned file; renderer cannot choose a path or a diagnostic payload. */
export async function readWorkshopFlashIndex(file) {
  try {
    const handle = await fs.open(file, "r");
    try {
      if ((await handle.stat()).size > 2 * 1024 * 1024) return { status: "invalid", index: null };
      const doc = JSON.parse(await handle.readFile("utf8"));
      if (!validateWorkshopFlashIndex(doc)) return { status: "invalid", index: null };
      // Version-one sources have a closed schema. Do not project away an
      // unknown operator, exclusion or target qualifier as harmless metadata.
      return { status: "loaded", index: {
        schemaVersion: 1,
        sources: doc.sources.map(({ file, sha256 }) => ({ file, sha256 })),
        rules: doc.rules.map(({ id, generation, ecu, description, conditions, targets, source, currentEcus, kind, familyEvidence }) => ({
          id, generation, ecu, description, source,
          kind: kind ?? "firmware", familyEvidence: familyEvidence ?? "description",
          conditions: conditions.map(({ field, values }) => ({ field, values })),
          currentEcus: (currentEcus ?? []).map(({ logicalLink, conditions }) => ({ logicalLink,
            conditions: conditions.map(({ field, values }) => ({ field, values })) })),
          targets: targets.map(({ logicalLink, softwarePartNumber, session }) => ({ logicalLink, softwarePartNumber, session })),
        })),
      } };
    } finally { await handle.close(); }
  } catch (e) {
    return { status: e.code === "ENOENT" ? "missing" : "invalid", index: null };
  }
}

export async function prepareWorkshopExport(input, file) {
  // Validate canonical function/options before touching the optional local rule index.
  const { flashRuleId, ...base } = input ?? {};
  buildWorkshopPreview(base);
  let rule;
  let sources = [];
  if (flashRuleId !== undefined) {
    const loaded = await readWorkshopFlashIndex(file);
    if (loaded.status !== "loaded") throw new Error("workshop-index-unavailable");
    rule = loaded.index.rules.find((r) => r.id === flashRuleId);
    sources = loaded.index.sources;
  }
  return { ...buildWorkshopPreview({ ...base, ...(flashRuleId === undefined ? {} : { flashRuleId }) }, rule), sources };
}
