import assert from "node:assert/strict";
import { mainlandParameter, mainlandValueText, mainlandUnit } from "./live-unit-logic.mjs";
const near = (actual, expected) => assert.ok(Math.abs(actual - expected) < 1e-9, `${actual} != ${expected}`);
for (const [value, unit, expected, target] of [
  [32, "°F", 0, "°C"], [212, "°F", 100, "°C"], [-40, "℉", -40, "°C"],
  [273.15, "K", 0, "°C"], [20, "℃", 20, "°C"], [1013.25, "hPa", 101.325, "kPa"],
  [1, "bar", 100, "kPa"], [1, "MPa", 1000, "kPa"], [1000, "mbar", 100, "kPa"],
  [1000, "Pa", 1, "kPa"], [1, "psi", 6.894757293168361, "kPa"],
  [60, "mph", 96.56064, "km/h"], [10, "m/s", 36, "km/h"],
  [500, "mA", .5, "A"], [12500, "mV", 12.5, "V"], [1500, "1/min", 1500, "rpm"],
  [20, "Nm", 20, "N·m"],
  [1500, "转/分", 1500, "rpm"], [10, "knot", 18.52, "km/h"],
  [2, "Ohm", 2, "Ω"], [1500, "mOhm", 1.5, "Ω"], [2, "Ω", 2, "Ω"],
  [2, "kHz", 2000, "Hz"], [.5, "MHz", 500000, "Hz"],
  [1500, "ms", 1.5, "s"], [250000, "us", .25, "s"], [.5, "h", 1800, "s"], [.5, "小时", 1800, "s"],
]) { const result = mainlandParameter(value, unit); near(result.value, expected); assert.equal(result.unit, target); }
for (const value of ["开启", "212", null, undefined, NaN, Infinity, true]) {
  const result = mainlandParameter(value, "°F"); assert.equal(result.value, value); assert.equal(result.unit, "°F");
}
assert.deepEqual(mainlandParameter(5, "unknown"), { value: 5, unit: "unknown" });
assert.equal(mainlandUnit(null).type, null);
assert.deepEqual(mainlandParameter(Number.MAX_VALUE, "MPa"), { value: Number.MAX_VALUE, unit: "MPa" });
assert.equal(mainlandValueText(212, "°F").text, "100");
for (const unit of ["H", "最低", "年", "月", "日", "编号", "步骤", "1/s2", "degree crk", "°TPS"]) {
  assert.equal(mainlandUnit(unit).type, null, "ambiguous source labels and distinct quantities remain separate");
  assert.deepEqual(mainlandParameter(5, unit), { value: 5, unit });
}
for (const unit of ["转/分", "knot", "Ohm", "mOhm", "kHz", "MHz", "ms", "us", "h", "小时"]) {
  for (const value of ["1500", "开启", null, false, Infinity]) assert.deepEqual(mainlandParameter(value, unit), { value, unit });
}
for (const unit of ["knot", "kHz", "MHz", "h", "小时"]) assert.deepEqual(mainlandParameter(Number.MAX_VALUE, unit), { value: Number.MAX_VALUE, unit });
console.log("PASS mainland temperature/pressure/speed/current/voltage/torque conversions, exact numeric-only values, enum/unknown/overflow preservation");
