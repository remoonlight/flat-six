/** Only a native Save As choice grants a destination; renderer paths are rejected. */
export async function saveRecordingFile(input, { dialog, writeFile, window }) {
  try {
    if (!input || typeof input !== "object" || Array.isArray(input)
      || Object.keys(input).some((key) => !["fileName", "recording"].includes(key))
      || typeof input.fileName !== "string" || !/^[a-zA-Z0-9_-]{1,120}\.json$/.test(input.fileName)
      || !input.recording || typeof input.recording !== "object" || Array.isArray(input.recording)) {
      return { ok: false, saved: false, error: "invalid_recording" };
    }
    const json = JSON.stringify(input.recording, null, 2) + "\n";
    if (Buffer.byteLength(json, "utf8") > 8 * 1024 * 1024) return { ok: false, saved: false, error: "recording_too_large" };
    const options = { title: "保存此次采集", defaultPath: input.fileName,
      filters: [{ name: "采集记录 JSON", extensions: ["json"] }] };
    const chosen = window ? await dialog.showSaveDialog(window, options) : await dialog.showSaveDialog(options);
    if (chosen.canceled || !chosen.filePath) return { ok: true, saved: false, canceled: true };
    await writeFile(chosen.filePath, json, "utf8");
    return { ok: true, saved: true, filePath: chosen.filePath };
  } catch (error) { return { ok: false, saved: false, error: error?.code || "recording_save_failed" }; }
}
