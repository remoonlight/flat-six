# 项目 Codex 配置

项目规则入口是 [AGENTS.md](../AGENTS.md)。[config.toml](config.toml) 只放本项目能通过 Codex 加载的覆盖；全局启动命令、地址、密钥、插件、模型和订阅保持原配置。

项目覆盖仅在受信任项目加载配置后生效；仓库文件不能证明当前电脑的信任状态或配置已经加载，当前回合已提供的工具不会因此热卸载。后续新会话核对实际工具清单；不要将文件写入成功表述为运行时已生效。

REA 辅助逆向按 2026-10-14 用户要求接入。固定使用官方 [`rea-agents@6.5.0`](https://github.com/morluto/rea)，安装在 `.local/tools/rea/`，不进入产品依赖或Windows交付。复现安装：`npm install --prefix .local/tools/rea --ignore-scripts --no-audit --no-fund --save-exact rea-agents@6.5.0`。通过 `bash scripts/rea.sh --help` 使用；启动器将REA的XDG配置、缓存及临时目录放在该本地工具目录，已有项目ADB存在时供显式设备任务使用，不自动连接设备。安装失败先核对当前错误与代理配置，不修改全局网络设置。

`config.toml` 的 `mcp_servers.rea` 通过该启动器提供stdio服务，仅作用于本项目；绝对路径绑定当前Mac目录，移动项目后需同步这两处路径。当前回合可直接调用CLI；新会话重新连接后，以实际工具清单确认MCP发现。REA默认安装向导会配置用户级客户端，因此本项目不运行全局 `setup`，不自动启动其他AI代理或改写其他客户端配置。

原生分析使用项目本地 Ghidra 12.1.4 与 Temurin JDK 21.0.12.1（macOS arm64），位于 `.local/tools/rea/engines/`。官方发布包先核对大小及 SHA-256；Ghidra 包未附 Mac 原生反编译器，已使用包内源码和本机 Clang 编译 arm64 `decompile`。启动器默认设置对应 `GHIDRA_INSTALL_DIR`、`JAVA_HOME`，保留调用方显式设置。检查命令：`bash scripts/rea.sh doctor --provider ghidra --json`。本次下载来源、构建参数及检查结果见本地 `.local/rea-setup-20261014/`；不需要安装 Hopper／IDA。

Ghidra 不接受项目路径中包含点开头的目录，REA 因此将原生分析会话放到 `/tmp/rea-ghidra-*`，结束后删除临时项目与运行目录；这是其已安装版本的路径兼容行为。工具安装及保留证据仍在 `.local/`，不要强制把 Ghidra 项目移入 `.local/`。

协议库调试采用两套工具交叉核对：先用 `bash scripts/rea.sh decompile <原始库路径> <函数名或地址> --provider ghidra --json` 获取静态逻辑，再复用 `.local/obd-x431-native-eval-20261013/venv/bin/python` 的 Unicorn 2.1.4 环境验证原始 ARM／x86-64 指令。两边须核对相同库的 SHA-256 和函数地址，明确标注合成输入、模型化回调及未执行部分，分歧解决前不提升能力状态。Electron 界面与业务行为仍使用项目现有验收脚本。

独立REA stdio会话分析大型函数时，若返回`resource_constraint`并附`details.reported_limits.continuation`，分析结果可能已完整保留：在同一会话按所给`inspect_analysis_view`参数读取。`pseudocode`页的单位是UTF-16代码单元，已验证`limit=4096`；逐页采用`coverage.next_offset`，直到`exhausted=true`，核对父Evidence和原件哈希后合并。`close_binary`会清除该会话保留的Evidence，不能先关闭再用旧引用继续读取。当前完整分页及失败记录见`.local/obd-x431-producer-selector-20261015/`。

Unicorn 2.1.4 的C API以[微秒](https://github.com/unicorn-engine/unicorn/blob/2.1.4/include/unicorn/unicorn.h)解释`emu_start(timeout=...)`，当前安装的Python方法说明误写为毫秒。原始指令执行须有显式时间／指令上限，注明模型化装载、文件与堆边界；来源索引见`OBD_STATUS.md`。

`mysql-mcp`、`ida-pro-mcp`、`ldj-debug`、`hcrdi-aichat`、通用 `ssh-mcp` 不在 `~/.codex/config.toml` 里。不能用只有 `enabled = false` 的表去“禁用”它们：Codex 会把这种表当成新 server，因缺少 command/url 报 `invalid transport`，桌面端把这次加载失败显示成 `failed to load workspace requirements`。这些 server 当前本来就不会被加载。本地 SQLite、Git 和 PETKA SSH 分别使用项目代码、Shell 和已核对的本机 SSH 配置。

若任务确需某个 server，先在全局配置里确认已有 transport，再在项目里覆盖 `enabled`；不复制全局密钥/地址进仓库。整体回退可移除本文件夹的 `config.toml`，恢复全局继承。

`.cursor/mcp.json.example` 仅保留旧客户端空模板，不会配置 Codex。旧模板的三个 server 不是本机已连接服务；不要照模板自动安装或填入私人配置。

配置层级与 `mcp_servers.<id>.enabled` 的含义依据 [OpenAI 配置参考](https://learn.chatgpt.com/docs/config-file/config-reference)。本次依据与检查输出保存在本机 `.local/agent-efficiency-20261004/`，不纳入版本管理；尚无修改后的执行耗时对照，不能声称提速比例。

PIWIS离线索引辅助工具：项目本地PBL库为 `.local/tools/pbl/libpbl.dylib`，现成格式读取器位于 `.local/tools/odis-project-explorer/`，固定来源与编译参数见 `.local/obd-piwis-dtc-source-20261018/tools.json`。仅绑定PBL只读API（打开参数update=0），读取前后校验输入哈希；运行入口为该任务的 `pbl-index.py`。原厂字典的技术名称按ASCII严格读取、Unicode按UTF-16LE读取，不猜测未知文本编码。公共解析器有层数据尾部兼容性缺项，只用于定位；关键字段继续以REA／Ghidra和同库原始指令交叉核对，不能据输出直接启用车辆操作。
