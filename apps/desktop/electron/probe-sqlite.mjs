import { app } from "electron";

app.whenReady().then(async () => {
  try {
    const mod = await import("node:sqlite");
    console.log("node:sqlite keys", Object.keys(mod));
    console.log("versions", process.versions);
    app.exit(0);
  } catch (e) {
    console.error("FAIL", e);
    app.exit(1);
  }
});
