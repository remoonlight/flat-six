import type { XrayTransform } from "../api";

const POS_MIN = -3;
const POS_MAX = 3;
const POS_STEP = 0.01;
const DEG_MIN = -180;
const DEG_MAX = 180;
const DEG_STEP = 0.5;
const SCALE_MIN = 0.1;
const SCALE_MAX = 3;
const SCALE_STEP = 0.01;

const IDENTITY: XrayTransform = {
  position: [0, 0, 0],
  rotationEuler: [0, 0, 0],
  scale: [1, 1, 1],
};

function radToDeg(r: number): number {
  return (r * 180) / Math.PI;
}

function degToRad(d: number): number {
  return (d * Math.PI) / 180;
}

function AxisSlider(props: {
  label: string;
  value: number;
  min: number;
  max: number;
  step: number;
  unit: string;
  onChange: (v: number) => void;
}) {
  const { label, value, min, max, step, unit, onChange } = props;
  return (
    <label className="locator-slider-row">
      <span className="locator-slider-label">{label}</span>
      <input
        type="range"
        min={min}
        max={max}
        step={step}
        value={value}
        onChange={(e) => onChange(Number(e.target.value))}
      />
      <input
        type="number"
        className="locator-slider-num"
        min={min}
        max={max}
        step={step}
        value={Number(value.toFixed(3))}
        onChange={(e) => onChange(Number(e.target.value))}
      />
      <span className="muted">{unit}</span>
    </label>
  );
}

export type XrayTransformSlidersProps = {
  assemblyId: string;
  labelZh: string;
  value: XrayTransform;
  onChange: (next: XrayTransform) => void;
  onReset: () => void;
  /** Override persist hint (layer vs mesh file). */
  persistHint?: string;
  /** One scale slider → [s,s,s]. */
  uniformScale?: boolean;
};

export function identityTransform(): XrayTransform {
  return {
    position: [...IDENTITY.position],
    rotationEuler: [...IDENTITY.rotationEuler],
    scale: [...IDENTITY.scale],
  };
}

export function XrayTransformSliders({
  assemblyId,
  labelZh,
  value,
  onChange,
  onReset,
  persistHint = ".local/xray-transforms.json",
  uniformScale = false,
}: XrayTransformSlidersProps) {
  const [px, py, pz] = value.position;
  const [rx, ry, rz] = value.rotationEuler;
  const [sx, sy, sz] = value.scale;
  const uniformS = (sx + sy + sz) / 3;

  function setPos(i: 0 | 1 | 2, v: number) {
    const position = [...value.position] as number[];
    position[i] = v;
    onChange({ ...value, position });
  }

  function setRotDeg(i: 0 | 1 | 2, deg: number) {
    const rotationEuler = [...value.rotationEuler] as number[];
    rotationEuler[i] = degToRad(deg);
    onChange({ ...value, rotationEuler });
  }

  function setScale(i: 0 | 1 | 2, v: number) {
    const scale = [...value.scale] as number[];
    scale[i] = v;
    onChange({ ...value, scale });
  }

  function setUniformScale(v: number) {
    onChange({ ...value, scale: [v, v, v] });
  }

  return (
    <div className="locator-sliders panel">
      <div className="locator-sliders-head">
        <h3>
          拖动微调 · {labelZh} <code>{assemblyId}</code>
        </h3>
        <button type="button" className="btn-link" onClick={onReset}>
          归零
        </button>
      </div>
      <p className="muted" style={{ margin: "0 0 8px", fontSize: 12 }}>
        位置（米）· 角度（度）· {uniformScale ? "整体缩放" : "缩放"} · 自动写入{" "}
        <code>{persistHint}</code>
      </p>
      <AxisSlider
        label="X"
        value={px}
        min={POS_MIN}
        max={POS_MAX}
        step={POS_STEP}
        unit="m"
        onChange={(v) => setPos(0, v)}
      />
      <AxisSlider
        label="Y"
        value={py}
        min={POS_MIN}
        max={POS_MAX}
        step={POS_STEP}
        unit="m"
        onChange={(v) => setPos(1, v)}
      />
      <AxisSlider
        label="Z"
        value={pz}
        min={POS_MIN}
        max={POS_MAX}
        step={POS_STEP}
        unit="m"
        onChange={(v) => setPos(2, v)}
      />
      <AxisSlider
        label="RX"
        value={radToDeg(rx)}
        min={DEG_MIN}
        max={DEG_MAX}
        step={DEG_STEP}
        unit="°"
        onChange={(v) => setRotDeg(0, v)}
      />
      <AxisSlider
        label="RY"
        value={radToDeg(ry)}
        min={DEG_MIN}
        max={DEG_MAX}
        step={DEG_STEP}
        unit="°"
        onChange={(v) => setRotDeg(1, v)}
      />
      <AxisSlider
        label="RZ"
        value={radToDeg(rz)}
        min={DEG_MIN}
        max={DEG_MAX}
        step={DEG_STEP}
        unit="°"
        onChange={(v) => setRotDeg(2, v)}
      />
      {uniformScale ? (
        <AxisSlider
          label="缩放"
          value={uniformS}
          min={SCALE_MIN}
          max={SCALE_MAX}
          step={SCALE_STEP}
          unit="×"
          onChange={setUniformScale}
        />
      ) : (
        <>
          <AxisSlider
            label="SX"
            value={sx}
            min={SCALE_MIN}
            max={SCALE_MAX}
            step={SCALE_STEP}
            unit="×"
            onChange={(v) => setScale(0, v)}
          />
          <AxisSlider
            label="SY"
            value={sy}
            min={SCALE_MIN}
            max={SCALE_MAX}
            step={SCALE_STEP}
            unit="×"
            onChange={(v) => setScale(1, v)}
          />
          <AxisSlider
            label="SZ"
            value={sz}
            min={SCALE_MIN}
            max={SCALE_MAX}
            step={SCALE_STEP}
            unit="×"
            onChange={(v) => setScale(2, v)}
          />
        </>
      )}
    </div>
  );
}
