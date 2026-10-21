import assert from "node:assert/strict";
import { groupLiveCurves, liveCurveGeometry } from "./live-curve-logic.mjs";
import { manufacturerTiming } from "./manufacturer-timing.mjs";
import {} from "./live-unit-logic.selfcheck.mjs";

const point = (elapsedMs, value) => ({ elapsedMs, value });
const series = (id, unit, segments = []) => ({ id, name: id, unit, latest: "—", segments });
const input = [series("coolant", "°C", [[point(1000, 80), point(3000, 90)]]),
  series("intake", "°C", [[point(2000, 20)]]), series("rpm", "rpm"),
  series("pressure1", "kPa"), series("pressure2", "kPa"), series("pressure3", "bar"),
  series("state1", null), series("state2", null), series("unknown1", "unknown"), series("unknown2", "unknown")];
const snapshot = JSON.stringify(input), grouped = groupLiveCurves(input);
assert.equal(grouped.length, 7);
assert.deepEqual(grouped[0].series.map(s => s.id), ["coolant", "intake"]);
assert.deepEqual(grouped[2].series.map(s => s.id), ["pressure1", "pressure2", "pressure3"]);
assert.equal(grouped[2].unit, "kPa", "known pressure units share an explicitly converted axis");
assert.notEqual(grouped[3].key, grouped[4].key, "missing type must remain separate");
assert.notEqual(grouped[5].key, grouped[6].key, "unknown type must remain separate");
const chart = liveCurveGeometry(grouped[0]);
assert.equal(chart.start, 0); assert.equal(chart.end, 3000);
assert.equal(chart.series[1].segments[0][0].x, 76 + 2000 / 3000 * 374, "different series share actual elapsed time, not independent origins");
assert.equal(chart.series[0].segments[0][0].value, 80);
assert.equal(chart.series[1].segments[0][0].value, 20, "no per-parameter normalization");
assert.equal(JSON.stringify(input), snapshot, "no change to stored data or timestamps");
const sourceUnits = [
  ["resistance-ohm", "Ohm", 2, "电阻", "Ω", 2], ["resistance-milliohm", "mOhm", 1500, "电阻", "Ω", 1.5],
  ["frequency-kilo", "kHz", 2, "频率", "Hz", 2000], ["frequency-mega", "MHz", .5, "频率", "Hz", 500000],
  ["duration-milli", "ms", 1500, "时长", "s", 1.5], ["duration-micro", "us", 250000, "时长", "s", .25],
  ["duration-hour", "小时", .5, "时长", "s", 1800],
  ["rotation-chinese", "转/分", 1500, "转速", "rpm", 1500], ["rotation-rpm", "rpm", 2000, "转速", "rpm", 2000],
  ["speed-knot", "knot", 10, "速度", "km/h", 18.52], ["speed-kph", "km/h", 20, "速度", "km/h", 20],
];
const unitSeries = sourceUnits.map(([id, unit, value]) => ({ ...series(id, unit, [[point(1000, value)]]), latestValue: value }));
const unitSnapshot = JSON.stringify(unitSeries), unitGroups = groupLiveCurves(unitSeries);
assert.equal(unitGroups.length, 5, "explicit source units merge only with the same quantity");
for (const [id, , , label, unit, expected] of sourceUnits) {
  const group = unitGroups.find(g => g.series.some(s => s.id === id));
  assert.equal(group.label, label); assert.equal(group.unit, unit);
  const item = liveCurveGeometry(group).series.find(s => s.id === id);
  assert.ok(Math.abs(item.segments[0][0].value - expected) < 1e-9);
}
assert.equal(JSON.stringify(unitSeries), unitSnapshot, "unit grouping preserves source samples");
const epoch = (t, connectionEpoch) => ({ ...point(t, 40), numeric: true, connectionEpoch });
const timed = manufacturerTiming([epoch(100, 1), epoch(200, 1), epoch(900, 2), epoch(1000, 2)]);
const broken = liveCurveGeometry(groupLiveCurves([series("temp", "°C", timed.segments)])[0]);
assert.deepEqual(broken.series[0].segments.map(s => s.map(p => p.elapsedMs)), [[100, 200], [900, 1000]], "reconnect gap never joined or filled");
const invalid = liveCurveGeometry(groupLiveCurves([series("temp", "°C", [[point(0, 2), point(10, NaN), point(20, 3), point(15, 4)]])])[0]);
assert.deepEqual(invalid.series[0].segments.map(s => s.map(p => p.elapsedMs)), [[0], [20], [15]]);
assert.equal(liveCurveGeometry(groupLiveCurves([series("empty", "°C")])[0]), null);
const flat = liveCurveGeometry(groupLiveCurves([series("zero", "°C", [[point(0, 0)]])])[0]);
assert.ok(Number.isFinite(flat.series[0].segments[0][0].y));
const many = Array.from({ length: 150000 }, (_, i) => point(i, i % 100));
assert.equal(liveCurveGeometry(groupLiveCurves([series("large", "°C", [many])])[0]).series[0].segments[0].length, 60001);
console.log("PASS converted type/unit grouping, shared axes, epoch/invalid gaps, empty/flat/large series and unchanged input");

const full = [series("rolling", "rpm", [[point(0, 90000), point(10000, 1000)], [point(65000, 1200), point(70000, 1300)]])];
const original = JSON.stringify(full), rolling = liveCurveGeometry(groupLiveCurves(full)[0]);
assert.equal(rolling.end - rolling.start, 60000);
assert.deepEqual(rolling.series[0].segments.map(s => s.map(p => p.elapsedMs)), [[10000], [65000, 70000]]);
assert.ok(rolling.max < 90000, "offscreen values do not distort the current window");
assert.equal(JSON.stringify(full), original, "full saved recording is untouched by rolling chart");

const mixed = [
  { ...series("celsius", "°C", [[point(0, 0), point(70000, 100)]]), latestValue: 100, latest: "100" },
  { ...series("fahrenheit", "°F", [[point(0, 32)], [point(70000, 212)]]), latestValue: 212, latest: "212（未更新）", latestSuffix: "（未更新）" },
  { ...series("kelvin", "K", [[point(70000, 373.15)]]), latestValue: 373.15, latest: "373.15" },
];
const mixedBefore = JSON.stringify(mixed), normalized = groupLiveCurves(mixed);
assert.equal(normalized.length, 1); assert.equal(normalized[0].unit, "°C");
assert.equal(normalized[0].series[1].latest, "100（未更新）");
const combined = liveCurveGeometry(normalized[0]);
assert.equal(combined.end - combined.start, 60000, "stopped and running batches share the last sixty seconds only");
for (const s of combined.series) assert.ok(Math.abs(s.segments.at(-1).at(-1).value - 100) < 1e-10);
assert.equal(JSON.stringify(mixed), mixedBefore, "conversion never rewrites captured values, timestamps or reconnect gaps");

const uneven = [series("old-temperature", "°C", [[point(10000, 90)]]),
  series("voltage", "V", [[point(10000, 12), point(80000, 13)]]),
  series("rpm", "rpm", [[point(140000, 2000)]])];
const unevenBefore = JSON.stringify(uneven), windows = groupLiveCurves(uneven);
assert.equal(liveCurveGeometry(windows[0]), null, "expired temperature is not kept on a separate frozen time axis");
const voltage = liveCurveGeometry(windows[1]), rpm = liveCurveGeometry(windows[2]);
assert.equal(voltage.start, 80000); assert.equal(voltage.end, 140000);
assert.equal(voltage.series[0].segments[0][0].elapsedMs, 80000, "the exact sixty-second boundary remains visible");
assert.deepEqual([rpm.start, rpm.end], [voltage.start, voltage.end], "different types share the same batch time window");
assert.equal(JSON.stringify(uneven), unevenBefore, "expired display points remain intact for full-batch saving");
