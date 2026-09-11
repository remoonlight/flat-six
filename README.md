# 981 车库

## 下载后怎么用

这是源码版，**没有安装器，也没有 electron-builder 安装包**。第一次运行需要 Windows 10/11 和网络，用于安装运行所需的 Node.js、依赖与 Electron。

1. 打开 [GitHub 的 porsche981 分支](https://github.com/remoonlight/flat-six/tree/porsche981)。请确认分支是 **porsche981**，不是 `main`。
2. 点击绿色 **Code** → **Download ZIP**，解压 ZIP。
3. 进入解压后的文件夹，双击根目录的 **`开始车库.cmd`**（若中文文件名乱码，改双击 **`START.cmd`**）。
4. 首次运行时，按黑色窗口提示安装 Node.js LTS；有 `winget` 的 Windows 可直接确认自动安装。若安装后提示找不到 Node.js，关掉窗口后再双击一次。
5. 等待安装完成。弹出的 Electron 窗口才是车库；请保留黑色窗口，关闭它会退出车库。

首次下载约 **450 MB**，因为 3D GLB 文件直接随 Git 下载，不使用 Git LFS。以后仍从同一个 `开始车库.cmd` 启动即可。

### 这个启动器会做什么

`开始车库.cmd` 会调用 `scripts\run-desktop.cmd`，并依次：

1. 确认你完整解压了仓库。
2. 检查 Node.js 是否为 20 或更高；缺失时给出官网 <https://nodejs.org/>，并在可用时使用 `winget` 安装 `OpenJS.NodeJS.LTS`。
3. 设置 Electron 下载镜像、清理上次残留的开发进程。
4. 首次从 `data/seed/xray/` 准备姿态和 OEM 关联文件。
5. 首次安装依赖，然后在**同一个黑色窗口**运行车库。

可在桌面为解压后文件夹中的 `开始车库.cmd` 建快捷方式；快捷方式的“目标”直接指向该文件即可。

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

面向 **2014 Porsche Boxster S（981）PDK** 的本机 Windows Electron 车库：零件浏览、保养、只读 OBD、车辆设置和 3D 定位。

所有数据和数据库均在本机，无云服务、无账号。开发数据库路径是 `.local/garage.db`。

## 使用边界

- OBD、诊断和车辆设置均为**只读**，不会写入 ECU。
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

- [需求说明](docs/requirements.md)
- [架构决策记录](docs/adr/)
- [许可证](LICENSE)

## 许可证与第三方材料

本分支基于上游 fork [dmitry-grechko/flat-six](https://github.com/dmitry-grechko/flat-six)（MIT）。本分支原创代码采用 [PolyForm Noncommercial 1.0.0](LICENSE)，仅限非商业用途。

DTC、teile / Design911、X431 导出资料，以及 flat-six、PETKA、游戏和其他第三方 3D 资产，可能各有独立来源与使用条件；它们不因本分支许可证而获得额外授权。
