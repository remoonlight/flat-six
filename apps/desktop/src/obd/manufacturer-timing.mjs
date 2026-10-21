/** Host response observations, preserving discontinuities in recorded order. */
export function manufacturerTiming(series) {
  const segments = [], points = [];
  let segment = null, previous = null, interruptions = 0;
  for (const sample of series) {
    const changed = previous && sample.connectionEpoch !== previous.connectionEpoch;
    if (changed) interruptions++;
    if (changed || previous && sample.elapsedMs < previous.elapsedMs) segment = null;
    if (sample.numeric && Number.isFinite(Number(sample.value)) && Number.isFinite(sample.elapsedMs)) {
      if (!segment) { segment = []; segments.push(segment); }
      segment.push(sample); points.push(sample);
    } else segment = null;
    previous = sample;
  }
  const latest = series.at(-1), prior = series.at(-2);
  const recovered = !!prior && latest.connectionEpoch !== prior.connectionEpoch;
  const delta = prior ? latest.elapsedMs - prior.elapsedMs : NaN;
  const intervalMs = !recovered && Number.isFinite(delta) && delta >= 0 ? delta : null;
  return { segments, points, intervalMs, recovered, interruptions };
}

export function manufacturerConnectionMessage(event, finished = false) {
  if (!event) return "";
  if (finished && ["interrupted", "awaiting-retry", "retrying", "qualified", "retry-failed"].includes(event.phase))
    return "本批已结束，通信恢复结果未确认，已保留中断记录";
  switch (event.phase) {
    case "interrupted": return "通信中断，保留已收结果，正在释放旧连接";
    case "awaiting-retry": return "旧连接已释放，等待选择重试继续或终止；原十分钟截止时间不变";
    case "retrying": return `正在第 ${event.attempt} 次手动重连并核对身份`;
    case "qualified": return "身份核对一致，等待未完成请求的响应";
    case "recovered": return "通信已恢复，继续当前参数组";
    case "retry-failed": return `第 ${event.attempt} 次重连未收到有效响应，正在释放本次连接`;
    case "failed": return event.reason === "identity-mismatch" ? "身份不符，停止本批并保留已收结果"
      : event.reason === "cancelled" ? "恢复已取消，正在结束本批"
      : event.reason === "deadline-expired" ? "本次读取期限已到，正在结束本批"
      : "通信恢复失败，停止本批并保留已收结果";
    default: return "";
  }
}
