# 项目 Codex 配置

项目规则入口是 [AGENTS.md](../AGENTS.md)。[config.toml](config.toml) 只覆盖本项目的 MCP 启用状态；全局启动命令、地址、密钥、插件、模型和订阅保持原配置。

项目覆盖仅在受信任项目加载配置后生效；仓库文件不能证明当前电脑的信任状态或配置已经加载，当前回合已提供的工具不会因此热卸载。后续新会话核对实际工具清单；不要将文件写入成功表述为运行时已生效。

保留 `node_repl`（浏览器运行时依赖）与 `chrome-devtools`（已有 UI 工具）。禁用 MySQL、IDA、ldj-debug、hcrdi-aichat 和指向其他调试机的通用 SSH；本地 SQLite、Git 和 PETKA SSH 分别使用项目代码、Shell 和已核对的本机 SSH 配置。没有新增 server、安装包或认证操作。

若任务确需某个被禁用的 server，核对用途、目标及权限后，仅将对应项目 `enabled` 调整为 `true`；不复制全局密钥/地址进仓库。整体回退可移除本文件夹的 `config.toml`，恢复全局继承。

`.cursor/mcp.json.example` 仅保留旧客户端空模板，不会配置 Codex。旧模板的三个 server 不是本机已连接服务；不要照模板自动安装或填入私人配置。

配置层级与 `mcp_servers.<id>.enabled` 的含义依据 [OpenAI 配置参考](https://learn.chatgpt.com/docs/config-file/config-reference)。本次依据与检查输出保存在本机 `.local/agent-efficiency-20261004/`，不纳入版本管理；尚无修改后的执行耗时对照，不能声称提速比例。
