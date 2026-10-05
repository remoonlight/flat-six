# 981 车库

## 下载后怎么用

GitHub 下载提供源码，**没有安装器，也没有 electron-builder 安装包**。第一次运行需要 Windows 10/11 和网络，用于安装运行所需的 Node.js **22+**、依赖与 Electron。

1. 打开 [GitHub 的 porsche981 分支](https://github.com/remoonlight/flat-six/tree/porsche981)。请确认分支是 **porsche981**，不是 `main`。
2. 点击绿色 **Code** → **Download ZIP**，解压 ZIP。
3. 进入解压后的文件夹，双击根目录的 **`开始车库.cmd`**（若中文文件名乱码，改双击 **`START.cmd`**）。
4. 首次运行时，按黑色窗口提示安装 Node.js LTS；有 `winget` 的 Windows 可直接确认自动安装。若安装后提示找不到 Node.js，关掉窗口后再双击一次。
5. 等待安装完成。弹出的 Electron 窗口才是车库；请保留黑色窗口，关闭它会退出车库。

首次下载约 **450 MB**，因为 3D GLB 文件直接随 Git 下载，不使用 Git LFS。以后仍从同一个 `开始车库.cmd` 启动即可。

### 这个启动器会做什么

`开始车库.cmd` 会调用 `scripts\run-desktop.cmd`，并依次：

1. 确认你完整解压了仓库。
2. 检查 Node.js 是否为 **22 或更高**（`node:sqlite` / db-bridge）；缺失时给出官网 <https://nodejs.org/>，并在可用时使用 `winget` 安装 `OpenJS.NodeJS.LTS`。
3. 设置 Electron 下载镜像、清理上次残留的开发进程。
4. 首次从 `data/seed/xray/` 准备姿态和 OEM 关联文件。
5. 首次安装依赖，然后在**同一个黑色窗口**运行车库。
6. 每次启动由 db-bridge 把仓库内捆绑目录（保养价、`data/seed/parts/catalog/` 公开快照、PETKA EPC 中英文名）**安全合并**进本机 SQLite：补齐空库或缺字段；不覆盖你改过的价格、币种备注、自定义零件、保养间隔、定位、车辆和服务记录。

可在桌面为解压后文件夹中的 `开始车库.cmd` 建快捷方式；快捷方式的“目标”直接指向该文件即可。

### 本地 Windows 解压式运行包

完成依赖安装和构建后，可运行 `npm run stage:windows -- .local/<新的目录>` 生成本地运行包，再启动其中的 `FlatSix.exe`。包内包含 Node/Python，运行数据写入用户目录；诊断头驱动和配对仍需单独完成。生成脚本只接受全新输出目录。

私有诊断资料通过应用连接页显式导入 ZIP，程序包不包含原始捕获、车辆原码、业务库或固件正文。已在本机空用户目录、移除开发环境 PATH 后验证运行和导入；另一台全新 Windows 及实际诊断头仍未验收。完整操作见 [OBD 当前使用](docs/obd-current-guide.md)。运行包和私人资料不随源码提交。

### 用 Git 获取（可选）

会使用 Git 的人，也必须切换到指定分支：

```powershell
git clone https://github.com/remoonlight/flat-six.git
cd flat-six
git checkout porsche981
```

### macOS 开发启动

macOS 仅用于开发和贡献，不是车主正式支持平台。OBD、串口和蓝牙行为以 Windows 为准，Mac 上可能不一致。

使用 Git 时，请切到 `porsche981` 分支：

```zsh
git clone https://github.com/remoonlight/flat-six.git
cd flat-six
git checkout porsche981
```

随后双击根目录的 **`开始车库.command`**；若中文文件名乱码，双击 **`START.command`**。也可在终端运行：

```zsh
bash scripts/run-desktop.sh
```

或直接安装依赖并启动：

```zsh
npm install && npm run dev
```

首次被 Gatekeeper 提示无法打开时，在访达中右键该 `.command` 文件，选择“打开”；也可先执行 `chmod +x 开始车库.command START.command`。

## 它是什么

面向 **2014 Porsche Boxster S（981）PDK** 的本机 Windows Electron 车库：零件浏览、保养、受限 OBD、车辆设置和 3D 定位。

所有数据和数据库均在本机，无云服务、无账号。开发数据库路径是 `.local/garage.db`。

## 使用边界

- OBD 默认只读；唯一例外是已具名 981 DME/Gateway 的受限清故障码流程。桌面拓扑点击目标模块或 GW 批量按钮即执行，无确认框；实际只处理已适配的 DME/Gateway，X431 须退出诊断会话。清前保存原始记录，清码实车验收仍待完成；CLI 仍需显式清码授权参数。设码、隐藏功能、刷写、执行器和其他 ECU 写入尚未开放，后续逐功能验证见 [ADR 001](docs/adr/001-no-ecu-write.md)。
- 仅在本机 Windows + SQLite 运行。
- macOS 仅提供开发启动路径，不作为车主正式支持平台。
- 不解析 PETKA `DATA\PO` 或 `.zgd` 文件。
- 界面价格统一折算为 CNY；teile / Design911 价格不等于 PETKA 已核价格。
- flat-six 与 PETKA 模型 GLB 已随仓库提供；CMS 991 抠模只可保留在本机，产品不使用 991 车身。

## 给开发者

Windows PowerShell：

```powershell
npm install
npm run dev
```

macOS / zsh：

```zsh
npm install
npm run dev
```

常用检查：

```powershell
npm test
npm run accept:catalog
npm run accept:all
```

## 简要结构

```text
apps/desktop/  Electron 桌面界面
packages/*/    业务逻辑与 SQLite 数据库代码
data/seed/     随仓库提供的种子数据
.local/        本机数据库、缓存和运行时文件
```

## 文档

- [文档总入口与职责](docs/README.md)
- [当前交付状态](docs/progress.md) · [OBD 结果与剩余验收](OBD_STATUS.md)
- [收敛审查与质疑](docs/convergence-review.md)
- [许可证](LICENSE)

`docs/` 的需求、ADR、当前进度和使用说明按白名单纳入版本管理；私人设备记录、原始资料与历史日志继续本地保存。历史迁入记录从文档总入口查阅。

源码下载不含私人车辆捕获、厂商原件或已生成的 X431 离线准备包。缺准备包时参数清单显示尚未生成；仓库有生成脚本不代表全新下载能在缺少来源资料时复现本机研究结果。当前也没有 Windows 安装器或完整新机交付验收，软件、实车及分发状态分别见上述进度与收敛审查。

## 许可证与第三方材料

本分支基于上游 fork [dmitry-grechko/flat-six](https://github.com/dmitry-grechko/flat-six)（MIT）。本分支原创代码采用 [PolyForm Noncommercial 1.0.0](LICENSE)，仅限非商业用途。

DTC、teile / Design911、X431 导出资料，以及 flat-six、PETKA、游戏和其他第三方 3D 资产，可能各有独立来源与使用条件；它们不因本分支许可证而获得额外授权。
