// Explicit display conversions only; never change the decoded capture or infer
// a physical quantity from a parameter name. Factors: NIST SP 811, appendix B.9.
// https://www.nist.gov/pml/special-publication-811/nist-guide-si-appendix-b-conversion-factors/nist-guide-si-appendix-b9
const units = new Map();
function define(type, unit, inputs) {
  for (const [source, scale = 1, offset = 0] of inputs) units.set(source, { type, unit, scale, offset });
}
define("温度", "°C", [["°C"], ["℃"], ["°F", 5 / 9, -32 * 5 / 9], ["℉", 5 / 9, -32 * 5 / 9], ["K", 1, -273.15]]);
define("压力", "kPa", [["Pa", .001], ["hPa", .1], ["kPa"], ["MPa", 1000], ["bar", 100], ["mbar", .1], ["psi", 6.894757293168361]]);
define("转速", "rpm", [["rpm"], ["1/min"], ["转/分"]]);
define("速度", "km/h", [["km/h"], ["mph", 1.609344], ["m/s", 3.6], ["knot", 1.852]]);
define("百分比", "%", [["%"]]);
define("电压", "V", [["V"], ["mV", .001]]);
define("电流", "A", [["A"], ["mA", .001]]);
define("扭矩", "N·m", [["Nm"], ["N·m"]]);
define("电阻", "Ω", [["Ω"], ["Ohm"], ["mOhm", .001]]);
define("频率", "Hz", [["Hz"], ["kHz", 1000], ["MHz", 1000000]]);
define("时长", "s", [["s"], ["ms", .001], ["us", .000001], ["h", 3600], ["小时", 3600]]);

export function mainlandUnit(unit) {
  const source = typeof unit === "string" ? unit.trim() : unit;
  return units.get(source) || { type: null, unit: source, scale: 1, offset: 0 };
}

export function mainlandParameter(value, unit) {
  const target = mainlandUnit(unit);
  if (typeof value !== "number" || !Number.isFinite(value)) return { value, unit };
  const converted = value * target.scale + target.offset;
  // Do not relabel an overflow or round the stored numerical observation.
  return Number.isFinite(converted) ? { value: Object.is(converted, -0) ? 0 : converted, unit: target.unit } : { value, unit };
}

export function mainlandValueText(value, unit) {
  const converted = mainlandParameter(value, unit);
  return { ...converted, text: typeof converted.value === "number" && Number.isFinite(converted.value)
    ? Number(converted.value.toPrecision(6)).toString() : String(converted.value ?? "--") };
}
