// Packaged startup reports errors to the private user directory and stderr.
// This also supplies the CommonJS main context expected by Electron tooling.
const { app } = require("electron");
const fs = require("node:fs");
const path = require("node:path");
if (process.env.PORSCHE981_USER_DATA) app.setPath("userData", process.env.PORSCHE981_USER_DATA);
// Asset modules resolve their mutable paths while main.mjs is imported.
process.env.PORSCHE981_LOCAL_ROOT ||= path.join(app.getPath("userData"), "local-assets");
function failed(error) {
  console.error(error);
  try {
    const directory = app.getPath("userData");
    fs.mkdirSync(directory, { recursive: true });
    fs.appendFileSync(path.join(directory, "startup-error.log"), `${new Date().toISOString()} ${error.stack || error}\n`);
  } catch { /* stderr remains available */ }
  app.exit(1);
}
import("./main.mjs").catch(failed);
