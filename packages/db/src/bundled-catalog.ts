import fs from "node:fs";
import path from "node:path";
import { createHash } from "node:crypto";
import {
  assembleBundledCatalog,
  BUNDLED_CATALOG_ALGO,
  type BundledPart,
} from "@porsche981/domain";

const ZONES = ["engine-bay", "brakes", "chassis"] as const;
const GENS = ["981", "982"] as const;

export const BUNDLED_CATALOG_HASH_KEY = "bundled_catalog_hash";

export type LoadedBundledCatalog = {
  rows: BundledPart[];
  sourceHash: string;
  zoneDrafts: number;
  bulkDrafts: number;
  epcUnique: number;
};

function readTracked(repoRoot: string, rel: string): string {
  const abs = path.join(repoRoot, rel);
  if (!fs.existsSync(abs)) throw new Error(`bundled_catalog_missing:${rel}`);
  return fs.readFileSync(abs, "utf8");
}

export function loadBundledCatalog(repoRoot: string): LoadedBundledCatalog {
  const bootstrapRel = path.join("data", "seed", "parts", "bootstrap.json");
  const fxRel = path.join("data", "seed", "fx.json");
  const epcRel = path.join(
    "data",
    "seed",
    "petka",
    "plaintext-archive",
    "PETKA_Porsche_981_EPC.csv",
  );
  const zoneRels = ZONES.map((z) =>
    path.join("data", "petka", z, "parts.csv"),
  );
  const bulkRels = GENS.map((g) =>
    path.join("data", "seed", "parts", "catalog", `${g}.csv`),
  );
  const exactRel = path.join(
    "data",
    "seed",
    "parts",
    "catalog",
    "teile-oem-exact.json",
  );

  const bootstrapJson = readTracked(repoRoot, bootstrapRel);
  const fxJson = readTracked(repoRoot, fxRel);
  const epcCsv = readTracked(repoRoot, epcRel);
  const zoneCsvs = zoneRels.map((rel) => readTracked(repoRoot, rel));
  const bulkCsvs = bulkRels.map((rel) => readTracked(repoRoot, rel));
  const exactOemJson = readTracked(repoRoot, exactRel);

  const hash = createHash("sha256");
  hash.update(BUNDLED_CATALOG_ALGO);
  for (const chunk of [
    bootstrapJson,
    fxJson,
    epcCsv,
    exactOemJson,
    ...zoneCsvs,
    ...bulkCsvs,
  ]) {
    hash.update("\0");
    hash.update(chunk);
  }

  const assembled = assembleBundledCatalog({
    bootstrapJson,
    zoneCsvs,
    bulkCsvs,
    epcCsv,
    fxJson,
    exactOemJson,
  });

  return {
    rows: assembled.rows,
    sourceHash: hash.digest("hex"),
    zoneDrafts: assembled.zoneDrafts,
    bulkDrafts: assembled.bulkDrafts,
    epcUnique: assembled.epcUnique,
  };
}
