// Import data literals only; never execute the upstream HTML or JavaScript.
import fs from "node:fs";
import path from "node:path";
import { execFileSync } from "node:child_process";
import ts from "typescript";
const root = path.resolve(import.meta.dirname, "..");
const source = path.resolve(
  root,
  process.argv[2] ?? ".local/references/porsche-coding-guide-tool",
);
const revision = execFileSync("git", ["-C", source, "rev-parse", "HEAD"], {
  encoding: "utf8",
}).trim();
if (revision !== "a1f5af3bdd1b0ac78becd94033cd4ebf9df9de7b")
  throw new Error("Review the new upstream revision before importing");
if (execFileSync("git", ["-C", source, "status", "--porcelain", "--", "index.html", "LICENSE"], { encoding: "utf8" }).trim())
  throw new Error("Upstream data or license has local changes; expected the pinned revision");
const html = fs.readFileSync(path.join(source, "index.html"), "utf8");
const literal = html.slice(
  html.indexOf("const ITEMS = ["),
  html.indexOf("const seriesModels="),
);
const ast = ts.createSourceFile(
  "items.js",
  literal,
  ts.ScriptTarget.Latest,
  true,
  ts.ScriptKind.JS,
);
function read(node) {
  if (ts.isStringLiteral(node) || ts.isNoSubstitutionTemplateLiteral(node))
    return node.text;
  if (ts.isNumericLiteral(node)) return Number(node.text);
  if (node.kind === ts.SyntaxKind.TrueKeyword) return true;
  if (node.kind === ts.SyntaxKind.FalseKeyword) return false;
  if (ts.isArrayLiteralExpression(node)) return node.elements.map(read);
  if (ts.isObjectLiteralExpression(node))
    return Object.fromEntries(
      node.properties.map((p) => {
        if (
          !ts.isPropertyAssignment(p) ||
          !(ts.isIdentifier(p.name) || ts.isStringLiteral(p.name))
        )
          throw new Error("Not a plain data property");
        return [p.name.text, read(p.initializer)];
      }),
    );
  throw new Error("Nonliteral upstream content rejected");
}
const declaration = ast.statements[0]?.declarationList?.declarations[0];
if (declaration?.name?.text !== "ITEMS") throw new Error("ITEMS not found");
const all = read(declaration.initializer);
const items = all
  .filter((i) => i.compat.includes("981"))
  .map((i) => ({
    ...i,
    steps: i.steps.filter((s) => !s.compat || s.compat.includes("981")),
  }));
if (
  !items.length ||
  items.some((i) => !i.steps.length) ||
  new Set(items.map((i) => i.id)).size !== items.length
)
  throw new Error("Invalid 981 catalog");
const destination = path.join(root, "data/seed/coding-guide");
fs.mkdirSync(destination, { recursive: true });
fs.writeFileSync(
  path.join(destination, "981.json"),
  JSON.stringify(
    {
      source: {
        repository: "https://github.com/StormEye818/porsche-coding-guide-tool",
        revision,
        license: "MIT",
        retrieved: "2026-09-13",
      },
      vehicle: {
        chassis: "981",
        model: "Boxster S",
        year: 2014,
        transmission: "PDK",
      },
      items,
    },
    null,
    2,
  ) + "\n",
);
fs.copyFileSync(
  path.join(source, "LICENSE"),
  path.join(destination, "LICENSE-StormEye818.txt"),
);
console.log(
  `Imported ${items.length} 981 features, ${items.reduce((n, i) => n + i.steps.length, 0)} steps from ${revision}`,
);
