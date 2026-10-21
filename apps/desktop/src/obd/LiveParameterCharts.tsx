import { groupLiveCurves, liveCurveGeometry, type CurveSeries } from "./live-curve-logic.mjs";

const COLORS = ["#2877bc", "#d45b27", "#31916c", "#9863bc", "#b27f12", "#b64273",
  "#145d68", "#6f7220", "#784632", "#47643c", "#b03539", "#5555a8"];
const number = (value: number) => Number(value.toPrecision(5)).toString();

export function LiveParameterCharts({ series, currentElapsedMs }: { series: CurveSeries[]; currentElapsedMs?: number | null }) {
  return <div className="eng-curve-groups" data-testid="live-curve-groups">
    {groupLiveCurves(series, currentElapsedMs).map(group => {
      const chart = liveCurveGeometry(group);
      return <section key={group.key} className="eng-curve-group" data-testid="live-curve-group" data-unit={group.unit}>
        <h4>{group.label}{group.unit ? `（${group.unit}）` : ""}</h4>
        <ul className="eng-curve-legend">
          {group.series.map((item, i) => <li key={item.id} data-parameter-id={item.id}>
            <span className="eng-curve-swatch" style={{ backgroundColor: COLORS[i % COLORS.length] }} aria-hidden="true" />
            <span>{item.name}：{item.latest}{item.unit ? ` ${item.unit}` : ""}
            </span>
          </li>)}
        </ul>
        {!chart ? <p className="muted eng-chart-empty" data-testid="live-curve-empty">{group.series.some(item => item.segments.some(segment => segment.length)) ? "最近60秒无有效数值" : "等待数值采样"}</p> :
          <svg className="eng-chart" viewBox="0 0 480 180" role="img" aria-label={`${group.label}随时间变化，单位${group.unit || "未提供"}`}>
            {[0, .5, 1].map(fraction => <g key={fraction}>
              <line x1="76" x2="450" y1={142 - fraction * 120} y2={142 - fraction * 120} className="eng-chart-grid" />
              <text x="68" y={146 - fraction * 120} textAnchor="end">{number(chart.min + fraction * (chart.max - chart.min))}</text>
            </g>)}
            {chart.series.map((item, i) => <g key={item.id} data-curve-id={item.id}>
              {item.segments.map((segment, j) => <g key={j}>
                <polyline fill="none" stroke={COLORS[i % COLORS.length]} strokeWidth="2" points={segment.map(p => `${p.x},${p.y}`).join(" ")} />
                {segment.map((p, k) => <circle key={k} cx={p.x} cy={p.y} r="2.5" fill={COLORS[i % COLORS.length]}>
                  <title>{`${item.name}：${number(p.value)} ${item.unit}`}</title>
                </circle>)}
              </g>)}
            </g>)}
            <text x="76" y="169">{number(chart.start / 1000)} s</text>
            <text x="450" y="169" textAnchor="end">{number(chart.end / 1000)} s</text>
            <text x="76" y="12">{group.unit}</text>
          </svg>}
      </section>;
    })}
  </div>;
}
