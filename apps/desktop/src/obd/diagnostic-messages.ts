const messages: Record<string, string> = {
  "coding-batch-target-conflict": "本次修改的设码项相互覆盖，请撤销全部修改后重新选择。",
  "coding-batch-effects-invalid": "设码字段的连续变化不一致，本次核对已停止。请重新读取并核对定义后再修改。",
  "coding-batch-preview-invalid": "设码修改与完整码值不一致，本次核对已停止。请重新读取并核对定义后再修改。",
  "coding-batch-edit-invalid": "本次修改中有未支持的项目或目标值，请撤销全部修改后重新选择。",
  "coding-batch-duplicate-field": "本次修改重复选择了同一设码项，请撤销全部修改后重新选择。",
  "stream_retry_not_available": "当前重试选择已失效或本批已到截止时间。已有数据保留，请查看本批状态。",
  "parameter_data_unavailable": "本批没有可保存的参数数据。请采集到参数数值后再保存；通信记录不会代替参数数据导出。",
  "parameter_sample_invalid": "本批参数的名称、数值或单位不完整，尚未保存。已有数据保留，请检查采集结果。",
  "parameter_timestamp_invalid": "参数采集时间无效，尚未保存。已有数据保留，请检查采集结果。",
  "parameter_count_mismatch": "本批参数数量不一致，尚未保存。已有数据保留，请检查采集结果。",
  "parameter_recording_save_failed": "参数文件未能保存，已有数据保留。请检查桌面写入权限和磁盘空间后重试。",
  "desktop_path_unavailable": "系统桌面位置不可用，已有数据保留。请恢复桌面目录后重试。",
  "parameter_recording_reveal_failed": "无法打开文件夹，已保存文件保留。请从桌面查看。",
  "parameter_recording_not_owned": "当前窗口没有这份保存记录。请从桌面查看文件。",
  "stream_evidence_invalid": "参数与请求、响应或采样时间的关联不一致，本次采集已停止，先前有效结果保留。",
  "numeric-target-invalid": "请填写十进制数值或分数，不能同时提交原始值。",
  "numeric-target-not-representable": "目标值不在字段的精确步进上，不能取近似值。",
  "numeric-target-out-of-source-bounds": "目标值超出来源定义的数值范围。",
  "numeric-target-reserved": "目标值对应来源中的保留或无效值。",
  "numeric-target-mask-unrepresentable": "目标值需要改变该字段掩码之外的位，不能修改。",
  "numeric-complete-source-bounds-required": "来源缺少完整数值边界，暂时不能修改该数值字段。",
  "numeric-integer-encoding-unqualified": "该数值编码尚未完成精确修改核对。",
  "numeric-inverse-not-unique": "来源公式无法唯一确定目标编码值，暂时不能修改。",
  "numeric-source-bounds-unrepresentable": "来源边界与字段位宽不一致，暂时不能修改。",
  "numeric-field-layout-unresolved": "数值字段布局尚未核实，暂时不能修改。",
  "numeric-field-name-unresolved": "数值字段缺少已核实名称，暂时不能修改。",
  "numeric-read-request-unresolved": "数值字段的读取来源尚未核实，暂时不能修改。",
  "numeric-linear-definition-required": "该字段缺少可核实的线性数值定义，暂时不能修改。",
  "numeric-coefficients-unresolved": "数值换算系数尚未核实，暂时不能修改。",
  "numeric-mask-unrepresentable": "来源中的数值掩码与字段布局不一致，暂时不能修改。",
  "numeric-original-buffer-invalid": "完整原始码值不符合格式，数值预览已停止。",
  "numeric-preview-decode-mismatch": "目标码值与重新解码的结果不一致，数值预览已停止。",
  "manufacturer-transport-unqualified": "当前诊断头尚未完成厂商参数传输适配，不能开始实车读取。",
  "manufacturer-profile-unqualified": "当前控制单元版本尚未完成独立读取适配，请选择已支持的版本；读取前仍须核对本次车辆身份。",
  "manufacturer-source-route-unidentified": "未识别当前控制单元。请检查车辆供电和连接后重试。",
  "manufacturer-source-route-ambiguous": "当前控制单元的通信路径无法唯一确认。请核对原厂资料后重试。",
  "vnci-manufacturer-transport-not-qualified": "VNCI的厂商参数传输适配尚未完成，当前不能启动此项读取。",
  "manufacturer-read-bundle-required": "请导入与当前程序匹配的新诊断资料；旧演练资料不能用于车辆读取。",
  "manufacturer-read-preconditions-required": "车辆读取需要只读操作和X431已停止通信的前置条件。",
  "manufacturer-handoff-failed": "诊断头会话交接失败，已停止启动。",
  "port_release_pending": "诊断头进程尚未完成关闭，请等待释放后继续。",
  "manufacturer-definition-incomplete": "所选参数的请求或解码定义不完整，请先补齐资料。",
  "manufacturer-historical-response-missing": "所选参数缺少完整历史响应，请先按采集清单补采。",
  "manufacturer-response-source-missing": "本机缺少已核对的历史原始响应资料，不能开始演练。",
  "manufacturer-response-reference-mismatch": "历史响应与所选参数的来源、地址或采集阶段不符，演练已停止。",
  "manufacturer-shared-response-conflict": "同一请求组的历史响应来源冲突，演练已停止。",
  "manufacturer-no-data": "没有收到可处理的响应，已停止本轮，缺失值不会补零。",
  "EISDIR": "所选位置是文件夹，请选择可保存的文件名；本批结果已保留。",
  "backup-identity-or-coverage-invalid": "备份的车辆身份、控制单元版本或完整码值清单不符合格式。",
  "backup-block-invalid": "备份中的码值块格式无效，不能继续分析。",
  "backup-block-identifier-invalid": "备份的 LID/DID 类型或标识长度不符合格式，不能混用。",
  "coding_field_block_mismatch": "所选字段属于其他码值块，不能用于当前块的分析或修改。",
  "coding_block_request_invalid": "码值块的读取标识无效。",
  "coding_value_not_defined": "目标值没有已核实的名称或合法选项，不能修改。",
  "coding-overlapping-layout-unresolved": "修改涉及同块中布局尚未核实的字段，预览已停止。",
  "coding-overlapping-value-unresolved": "修改涉及同块中无法核实原值或目标值的字段，预览已停止。",
  "coding-overlapping-field-unresolved": "修改涉及同块中没有已核实名称的字段，预览已停止。",
  "coding-effects-limit": "相关字段变化超过支持范围，预览已停止。",
  "backup-incomplete": "备份缺少码值块或存在重复项目，不能作为完整备份。",
  "backup-time-invalid": "备份缺少有效的读取时间。",
  "backup-current-ecu-mismatch": "备份属于其他控制单元，请选择当前控制单元的备份。",
  "backup-integrity-failed": "备份内容已改变或损坏，完整性校验未通过。",
  "restore-identity-version-mismatch": "原码基线与当前备份的车辆、控制单元或硬件软件版本不一致，不能恢复。",
  "restore-coverage-mismatch": "两份备份包含的码值块不同，不能把不完整的内容用于恢复。",
  "restore-block-length-mismatch": "码值块长度发生变化，当前版本的恢复方案无法成立。",
  "coding_options_not_defined": "该字段尚未提供可核实的合法选项，暂时不能修改。",
  "coding_options_limit": "该字段的合法选项超过当前支持范围，需要先补充字段定义。",
  "unknown_profile": "本机没有该控制单元版本的定义资料。",
  "unknown_record": "所选字段不在当前版本的定义中。",
  "firmware-manifest-invalid": "原厂固件匹配清单缺少有效的来源、哈希、长度或版本信息。",
  "firmware-current-ecu-mismatch": "固件清单或所选备份属于其他控制单元。",
  "firmware-size-invalid": "固件文件为空或超过当前支持的 512 MiB 上限。",
  "firmware-not-prepared": "还没有当前控制单元的固件准备结果。",
  "firmware-container-inspector-unavailable": "固件容器检查器不可用，无法生成本次准备结果。",
  "firmware-container-selection-missing": "请通过桌面文件选择器选择本次固件。",
  "firmware-container-file-unavailable": "无法读取所选固件文件。",
  "firmware-container-file-changed": "固件检查期间文件发生变化，本次准备结果已丢弃。",
  "firmware-container-xml-limit": "固件 XML 超过当前检查范围。",
  "firmware-container-xml-invalid": "固件 XML 内容损坏或格式不正确。",
  "firmware-container-xml-declaration-forbidden": "固件 XML 包含不支持的文档或实体声明。",
  "firmware-container-odx-invalid": "所选文件不是可识别的 ODX 文档。",
  "firmware-container-id-duplicate": "固件容器包含重复定义，无法可靠关联数据块。",
  "firmware-container-member-invalid": "固件容器包含不支持的成员路径。",
  "firmware-container-member-duplicate": "固件容器包含重复或大小写冲突的成员名。",
  "firmware-container-expanded-limit": "固件容器展开后的大小或文件数量超过当前检查范围。",
  "firmware-container-zip-invalid": "固件容器损坏、校验失败或压缩格式不支持。",
  "firmware-container-zip-encrypted": "固件容器使用 ZIP 加密，当前无法核对内容。",
  "firmware-container-report-limit": "固件结构结果超过当前可完整保存的范围。",
  "connection-lost-outcome-unknown": "模拟连接在写入过程中断开，无法确认模拟目标是否已应用码值。流程已停止，没有自动重发写入。",
  "readback-mismatch": "模拟回读的码值与目标码值不一致。流程已停止，需要恢复检查。",
  "pre-write-backup-failed": "完整操作前备份没有保存成功，流程已停止，没有执行模拟写入。",
  "pre-write-identity-mismatch": "重新读取的控制单元身份或版本与方案不一致，流程已停止，没有执行写入。",
  "pre-write-coding-changed": "重新读取的完整当前码值与方案的修改前内容不同，流程已停止，需要重新分析。",
  "bundle-busy-disconnect-first": "请先停止诊断任务并断开诊断头，再迁移资料。",
  "bundle-integrity-failed": "资料包中有文件未通过哈希校验，未导入这些文件。",
  "bundle-path-invalid": "资料包包含不允许导入的位置或文件类型。",
  "bundle-target-outside-root": "资料包的目标位置超出了本项目目录，不能导入。",
  "bundle-insufficient-space": "资料目录所在磁盘空间不足，尚未导入新文件。请先腾出空间后重试。",
  "bundle-timeout": "资料包处理超过三分钟，已停止。请检查资料包大小和磁盘状态。",
  "EEXIST": "所选位置已有同名文件。原文件已保留，请选择新的文件名。",
  "ENOSPC": "磁盘空间不足，文件没有完整保存。",
  "EACCES": "所选位置没有写入权限，请选择其他保存位置。",
  "EPERM": "系统不允许写入所选位置，请选择其他保存位置。",
  "no_valid_received_can_frames": "本批没有带有效接收时间的原始 CAN 帧，不能生成抓包文件。",
  "raw_can_frames_unavailable": "请先连接能够提供原始接收 CAN 帧的 vLinker 或 MX+ 诊断头。只返回诊断 PDU 的设备无法生成真实 CAN 抓包。",
  "capture-busy": "响应核对正在处理，请等待完成或取消。",
  "capture-finish-vehicle-first": "请先结束车辆采集和报文录制，再做离线响应核对。",
  "capture-cancelled": "本次响应核对已取消。",
  "capture-timeout": "响应核对超时，已停止处理；原始捕获文件仍保留。",
  "capture-worker-close-timeout": "处理进程尚未确认退出，临时文件保留，不能启动下一次核对。",
  "capture-session-mismatch": "请选择捕获中实际存在的运行目标及对应采集会话。",
  "dtc-capture-target-source-mismatch": "捕获目标不能与当前车型或控制单元的来源版本比较，请核对选择。",
  "dtc-capture-target-invalid": "请选择捕获中的完整运行目标。",
  "capture-changed-reopen": "原始捕获已改变，请结束录制后重新选择文件。",
  "capture-analysis-expired": "上一批核对结果已丢弃，请重新核对。",
  "capture-artifact-changed": "临时核对结果已改变，已停止保存，请重新核对。",
  "capture-export-overwrites-source": "保存位置与原始捕获是同一文件，请选择其他 ZIP 文件名。",
  "capture-selection-expired": "上一份捕获选择已失效，请重新选择。",
  "diagnostic_pdu_unavailable": "请先连接已支持厂商只读采集的 vLinker、MX+ 或 VNCI，在诊断读取前开始记录完整应用响应。",
};
const sourceMismatchCodes = new Set([
  "manufacturer-read-bundle-required", "manufacturer-read-code-missing", "manufacturer-source-hash-invalid",
  "manufacturer-code-path-invalid", "manufacturer-catalog-path-invalid", "manufacturer-index-binding-invalid",
  "invalid-profile-file", "manufacturer-profile-binding-invalid", "profile-ecu-mismatch",
]);
function sourceMessage(code: string) {
  if (code.startsWith("source-hash-mismatch:") || sourceMismatchCodes.has(code))
    return "诊断资料与当前程序不匹配，读取已停止。请更新配套程序和诊断资料后重试。";
  // A missing serial port can also produce ENOENT. Classify only known source files.
  if ((code.startsWith("[Errno 2]") || code.startsWith("ENOENT"))
    && /(?:[/\\](?:manifest|index)\.json|[/\\]profiles[/\\][a-f0-9]{64}\.json|[/\\]scripts[/\\]diagnostics[/\\][\w-]+\.py)(?:['"\s]|$)/.test(code))
    return "本机诊断资料不完整，暂时无法读取。请补齐与当前程序匹配的诊断资料后重试。";
  return null;
}

/** Catalog errors retain their evidence text without exposing it in the daily UI. */
export function diagnosticSourceFailure(error: unknown) {
  const detail = error instanceof Error ? error.message : String(error || "diagnostic_source_unavailable");
  return { message: sourceMessage(detail)
    || "本机诊断资料暂时无法读取。请检查资料是否齐全并与当前程序匹配后重试。", detail };
}

export function diagnosticMessage(error: unknown) {
  const code = String(error || "");
  if (messages[code]) return messages[code];
  if (code.startsWith("source-hash-mismatch:")) return sourceMessage(code)!;
  if (code.startsWith("bundle-existing-file-conflict:")) return "目标电脑已有不同内容的同名资料。原文件已保留，本次导入没有覆盖它。";
  return code ? `处理未完成（${code}）。` : "处理未完成，未获得有效结果。";
}

const parameterSaveMessages: Record<string, string> = {
  ENOSPC: "磁盘空间不足，参数文件未保存，已有数据保留。请释放桌面所在磁盘的空间后重试。",
  EACCES: "桌面没有写入权限，参数文件未保存，已有数据保留。请恢复桌面写入权限后重试。",
  EPERM: "系统阻止写入桌面，参数文件未保存，已有数据保留。请检查桌面权限后重试。",
  EIO: "磁盘写入失败，参数文件未保存，已有数据保留。请检查磁盘状态后重试。",
};
/** Parameter saves retain their batch; unknown filesystem/IPC text is detail only. */
export function parameterSaveFailure(error: unknown) {
  const detail = error instanceof Error ? error.message : String(error || "parameter_recording_save_failed");
  return { message: parameterSaveMessages[detail] || messages[detail] || messages.parameter_recording_save_failed, detail };
}

/** Retain technical evidence in data; the UI renders only the task message. */
export function standardSessionFailure(error: unknown) {
  const detail = error instanceof Error ? error.message : String(error || "status_failed");
  const sessionMessages: Record<string, string> = {
    "vehicle-model-unidentified": "车型未识别，不能开始采集。请检查车辆供电和诊断头连接后重试。",
    timeout: "采集超时，已有数据保留。请等待停止完成，再检查诊断头连接后重新开始。",
    adapter_release_not_verified: "诊断头释放未通过核对，已有数据保留。请返回连接设置，检查并重新连接诊断头。",
    unknown_job: "无法确认本次采集状态，已请求停止并保留已有数据。请等待状态恢复；持续失败时重新打开项目并检查诊断头。",
    status_failed: "暂时无法读取采集状态，已请求停止并保留已有数据。请等待状态恢复；持续失败时重新打开项目并检查诊断头。",
  };
  return { message: sourceMessage(detail) || sessionMessages[detail] || messages[detail]
    || "实时采集未完成，已有数据保留。请等待停止完成，再检查诊断头连接后重试。", detail };
}

/** Internal receiving keeps backend errors; daily UI explains the next action. */
export function internalReceiveFailure(error: unknown) {
  const code = error instanceof Error ? error.message : typeof error === "string" ? error : "";
  const internalMessages: Record<string, string> = {
    internal_receive_busy: "本批接收尚未停止完成。请等待停止确认后再开始。",
    busy: "当前车辆操作尚未结束。请等待操作结束后重试。",
    not_connected: "诊断头未连接。请返回连接设置，连接后再开始接收。",
    internal_profile_not_qualified: "当前诊断头与接线的接收适配尚未完成。请返回连接设置，选择已支持的诊断头与用途。",
    internal_receive_not_active: "当前没有正在接收的批次。请查看本页状态，需要采集时点击开始。",
    port_release_pending: "诊断头尚未释放完成。请等待关闭完成后再连接。",
    monitor_close_timeout: "诊断头停止确认超时。请等待状态更新；仍未停止时检查诊断头连接。",
  };
  return internalMessages[code] || "无法确认本次接收操作结果。请等待页面状态更新；仍未恢复时检查诊断头连接。";
}
