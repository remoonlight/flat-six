import { describe, expect, it } from "vitest";
import {
  collectSkusForModelSelection,
  findModelsForSku,
  findModelOemLink,
  findSkuByOemNumber,
  formatModelChildCaption,
  formatModelOemPointer,
  formatPetkaPrLabel,
  migrateModelOemLinkIds,
  modelOemLinkId,
  resolveModelOemSku,
  resolveModelOemSkus,
  suggestOemByInput,
  type ModelOemLink,
} from "./model-oem-links.js";

const links: ModelOemLink[] = [
  {
    id: modelOemLinkId("assembly", "engine"),
    kind: "assembly",
    ref: "engine",
    skus: ["oil-filter", "spark-plugs"],
  },
  {
    id: modelOemLinkId("mesh", "SM_Hood", "body"),
    kind: "mesh",
    ref: "SM_Hood",
    assemblyId: "body",
    skus: ["wiper-blades"],
  },
  {
    id: modelOemLinkId("mesh", "SM_FrontKit", "body"),
    kind: "mesh",
    ref: "SM_FrontKit",
    assemblyId: "body",
    skus: ["wiper-blades"],
  },
];

const parts = [
  { sku: "oil-filter", oem_number: "9A1.107.224.00", name_zh: "机油滤" },
  { sku: "spark-plugs", oem_number: "999.170.151.90", name_zh: "火花塞" },
  { sku: "wiper-blades", oem_number: null, name_zh: "雨刮" },
];

describe("model-oem-links", () => {
  it("ids and find", () => {
    expect(modelOemLinkId("mesh", "SM_Hood", "body")).toBe("mesh:body:SM_Hood");
    expect(modelOemLinkId("assembly", "engine")).toBe("assembly:engine");
    expect(findModelOemLink(links, "assembly", "engine")?.skus[0]).toBe(
      "oil-filter",
    );
    expect(findModelOemLink(links, "mesh", "SM_Hood", "body")?.skus[0]).toBe(
      "wiper-blades",
    );
  });

  it("same mesh name in two assemblies stays independent", () => {
    const shared: ModelOemLink[] = [
      {
        id: modelOemLinkId("mesh", "tripo_part_0", "pm-cooling"),
        kind: "mesh",
        ref: "tripo_part_0",
        assemblyId: "pm-cooling",
        skus: ["981-99110613103"],
      },
      {
        id: modelOemLinkId("mesh", "tripo_part_0", "pm-engine"),
        kind: "mesh",
        ref: "tripo_part_0",
        assemblyId: "pm-engine",
        skus: ["981-9A110092302"],
      },
    ];
    expect(
      resolveModelOemSku(shared, {
        kind: "mesh",
        ref: "tripo_part_0",
        assemblyId: "pm-cooling",
      }),
    ).toBe("981-99110613103");
    expect(
      resolveModelOemSku(shared, {
        kind: "mesh",
        ref: "tripo_part_0",
        assemblyId: "pm-engine",
      }),
    ).toBe("981-9A110092302");
    expect(
      resolveModelOemSku(shared, {
        kind: "mesh",
        ref: "tripo_part_0",
        assemblyId: "pm-fuel",
      }),
    ).toBeNull();
  });

  it("migrates legacy mesh:name ids without mixing assemblies", () => {
    const legacy: ModelOemLink[] = [
      {
        id: "mesh:tripo_part_0",
        kind: "mesh",
        ref: "tripo_part_0",
        assemblyId: "pm-cooling",
        skus: ["coolant-hose"],
      },
    ];
    expect(
      resolveModelOemSku(legacy, {
        kind: "mesh",
        ref: "tripo_part_0",
        assemblyId: "pm-cooling",
      }),
    ).toBe("coolant-hose");
    expect(
      resolveModelOemSku(legacy, {
        kind: "mesh",
        ref: "tripo_part_0",
        assemblyId: "pm-engine",
      }),
    ).toBeNull();
    const { links, changed } = migrateModelOemLinkIds(legacy);
    expect(changed).toBe(true);
    expect(links[0]?.id).toBe("mesh:pm-cooling:tripo_part_0");
  });

  it("one oem per model; mesh independent of assembly", () => {
    expect(
      resolveModelOemSku(links, {
        kind: "mesh",
        ref: "SM_Hood",
        assemblyId: "body",
      }),
    ).toBe("wiper-blades");
    expect(
      resolveModelOemSku(links, {
        kind: "mesh",
        ref: "SM_Unknown",
        assemblyId: "engine",
      }),
    ).toBeNull();
    expect(
      resolveModelOemSkus(links, {
        kind: "assembly",
        ref: "engine",
      }),
    ).toEqual(["oil-filter"]);
  });

  it("many models → one oem", () => {
    expect(findModelsForSku(links, "wiper-blades").map((m) => m.ref)).toEqual([
      "SM_Hood",
      "SM_FrontKit",
    ]);
  });

  it("format pointer single", () => {
    expect(formatModelOemPointer([], parts)).toBe("未关联");
    expect(formatModelOemPointer(["oil-filter", "spark-plugs"], parts)).toBe(
      "9A1.107.224.00 · 机油滤",
    );
    expect(formatModelOemPointer(["wiper-blades"], parts)).toBe(
      "wiper-blades · 雨刮",
    );
  });

  it("format pointer prefers Chinese over bulk oem-placeholder name", () => {
    const mixed = [
      {
        sku: "981-9A110722400",
        oem_number: "9A1.107.224.00",
        name_zh: "9A110722400",
        generation: "981",
      },
      {
        sku: "oil-filter",
        oem_number: "9A1.107.224.00",
        name_zh: "机油滤清器",
        generation: null,
      },
    ];
    expect(formatModelOemPointer(["981-9A110722400"], mixed)).toBe(
      "9A1.107.224.00 · 机油滤清器",
    );
    expect(suggestOemByInput(mixed, "9A1.107", 10)[0]?.label).toBe(
      "9A1.107.224.00 · 机油滤清器",
    );
  });

  it("formatPetkaPrLabel joins code and meaning", () => {
    expect(
      formatPetkaPrLabel(
        "PR:447,450",
        "447=紧急备用轮胎; 450=陶瓷制动系统 (PCCB)",
      ),
    ).toBe("447 紧急备用轮胎 · 450 陶瓷制动系统 (PCCB)");
    expect(formatPetkaPrLabel("", "")).toBeNull();
    expect(formatPetkaPrLabel("PR:480", null)).toBe("480");
  });

  it("formatModelChildCaption oem + zh + note + PR; en fallback", () => {
    expect(formatModelChildCaption("tripo_part_0", [], parts)).toEqual({
      title: "tripo_part_0",
      detail: "未关联",
    });
    expect(
      formatModelChildCaption("tripo_part_0", ["hose"], [
        {
          sku: "hose",
          oem_number: "991.106.131.03",
          name_zh: "冷却水管",
          name_en: "Coolant pipe",
          petka_note: "左侧",
          pr_label: "447 紧急备用轮胎",
        },
      ]),
    ).toEqual({
      title: "991.106.131.03",
      detail: "冷却水管 · 左侧 · PR 447 紧急备用轮胎",
    });
    expect(
      formatModelChildCaption("m", ["en-only"], [
        {
          sku: "en-only",
          oem_number: "9A1.107.224.00",
          name_zh: "9A110722400",
          name_en: "Oil filter insert",
        },
      ]),
    ).toEqual({
      title: "9A1.107.224.00",
      detail: "Oil filter insert",
    });
  });

  it("suggestOemByInput top 10 prefix then contains", () => {
    const catalog = [
      {
        sku: "a",
        oem_number: "9A1.107.224.00",
        name_zh: "机油滤",
        generation: "981",
      },
      {
        sku: "b",
        oem_number: "9A1.107.225.00",
        name_zh: "滤芯B",
        generation: "981",
      },
      {
        sku: "c",
        oem_number: "999.170.151.90",
        name_zh: "火花塞",
        generation: "981",
      },
      {
        sku: "d",
        oem_number: "9A110722400",
        name_zh: "重复号718",
        generation: "982",
      },
    ];
    const hits = suggestOemByInput(catalog, "9A1.107", 10);
    expect(hits.map((h) => h.oem_number)).toEqual([
      "9A1.107.224.00",
      "9A1.107.225.00",
    ]);
    expect(hits[0]?.name_zh).toBe("机油滤");
    expect(hits[0]?.label).toBe("9A1.107.224.00 · 机油滤");
    expect(suggestOemByInput(catalog, "", 10)).toEqual([]);
  });

  it("suggest shows 无中文名 when only oem placeholder", () => {
    const catalog = [
      {
        sku: "981-99110613103",
        oem_number: "991.106.131.03",
        name_zh: "99110613103",
        generation: "981",
      },
    ];
    expect(suggestOemByInput(catalog, "991.106", 10)[0]?.label).toBe(
      "991.106.131.03 · （无中文名）",
    );
  });

  it("findSkuByOemNumber normalizes and prefers 981", () => {
    const catalog = [
      {
        sku: "981-9A110722400",
        oem_number: "9A1.107.224.00",
        name_zh: "机油滤",
        generation: "981",
      },
      {
        sku: "982-9A110722400",
        oem_number: "9A1.107.224.00",
        name_zh: "机油滤718",
        generation: "982",
      },
    ];
    expect(findSkuByOemNumber(catalog, "9a1 107 224 00")).toBe(
      "981-9A110722400",
    );
    expect(findSkuByOemNumber(catalog, "nope")).toBeNull();
  });

  it("collectSkusForModelSelection is self-only (no child rollup)", () => {
    expect(
      collectSkusForModelSelection(links, {
        kind: "assembly",
        ref: "body",
      }),
    ).toEqual([]);
    expect(
      collectSkusForModelSelection(links, {
        kind: "mesh",
        ref: "SM_Hood",
        assemblyId: "body",
      }),
    ).toEqual(["wiper-blades"]);
  });
});
