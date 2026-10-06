"""Explicit no-car preparation: verified definitions, private snapshots and checklist.

No adapter discovery, serial, vehicle writes, package installation or production
database mutation. Run from the project root; output is a new private directory.
"""
from __future__ import annotations

import argparse
from contextlib import closing
from datetime import datetime, timezone
import json
from pathlib import Path
import platform
import shutil
import sqlite3
import subprocess

from .field_collection import build_pack
from .hashutil import sha256_file
from .realtime_preparation import DEFAULT_OUTPUT, ROOT
from .realtime_preparation_accept import validate_bundle


CHECKLIST = '''# 首次接车只读验收清单

对象：2014 Boxster S（981）PDK。软件演练通过不等于实车通过。每一步单独记录实际结果；失败时保留原因和已经收到的内容，不把未完成项填为通过。

## 出发前

1. 运行 `npm run prepare:obd-precar`，保留输出目录中的 `environment.json`、`db-backup`、编码备份目录（如存在）和 `collection`。脚本核对定义来源及哈希、生成全部 981/982 控制单元的身份/实时/原码采集资料、保留七单元优先批次并制作开发数据库快照；它不连接诊断头。若资料哈希不符，先重新生成定义，不绕过检查。完整运行检查证据另见 `docs/progress.md`。
2. 打开 Windows 桌面程序，确认拓扑、标准数据、X431 清单及已有诊断资料能显示。在本机选择保存位置，确认剩余空间够用。跨电脑或全新安装必须另测 Python、诊断头驱动、资料导入和资源路径，不能沿用开发电脑结论。
3. 带上已核对的诊断头、电脑和线缆。首次独立只读优先使用已有本车记录的 vLinker；MX+ 的车辆与静默接收资格分别核对。VNCI/PT3G 当前只核验诊断头身份、供电查询和释放，不用于本次车辆读取；设备枚举或驱动已安装不能证明 ECU 通信可用。提前核对本次可用设备及正常关闭，不用其他日期的记录填写本次结果。
4. 关闭程序后另行备份正式安装的 userData：`python -X utf8 -m scripts.diagnostics.user_data_backup backup --user-data <实际用户目录> --output-dir <新的备份目录> --app-closed`。包含业务库、诊断资料、连接设置、原码备份及本地资源；恢复只写新目录，经哈希及 SQLite 一致性核对后再启动。外部另存的捕获/导出文件须另行保留；开发库快照不替代此备份。

## 诊断 CAN：首次只读测试

1. 停车固定车辆，记录车辆、点火/发动机状态、诊断头型号及固件、程序版本、电脑时间基准。关闭其他诊断客户端，保证 X431/PIWIS 没有同时访问车辆。将诊断头接到诊断接口，在连接设置选择“诊断”，核对本次设备后连接。期望：适配器通信成功；电压读不到显示 `--`，不得把未知电压写成 0 V。
2. 进入系统拓扑，观察先识别当前已适配的 DME 和 Gateway，再读取身份与故障码。当前不会用未知地址扫描其他单元。期望：各单元结果带本次身份、时间与故障码；无响应只有在适配器链路独立确认健康且请求终结时才记为本单元无响应。立即点击保存结果，保存本次完整只读批次。
3. 在 DME 标准数据选择发动机负荷、冷却液温度、发动机转速、车速、进气温度、节气门开度这六项，开始连续采集。先做短批次，主动停止并保存全部轮次和样本；对照 X431 时应停止项目通信，再分别读取相同工况。期望：按实际返回时间更新，缺失值不补零，界面和保存内容一致。每轮完成后等待设置间隔，实际周期还包含通信耗时，不能预填“每秒更新一次”。
4. 如需原始报文，在开始测试前显式启动 PCAPNG 记录，完成后停止并检查文件。期望：只含记录开启期间诊断头实际报告的 RX CAN 帧；没有 TX 或物理时间证据时不得补造。用 Wireshark/TShark 实际打开，逐项核对标准/扩展 ID、DLC、字节、时间单位和注释。出发前可运行 `npm run accept:pcapng-wireshark` 检查本机解析兼容性；它使用模拟帧，不连接车辆。换电脑需重测，不能把历史软件通过填作本次车辆通过。
5. 只读短批次稳定后，可测试一次适配器链路中断。期望：只重连同一诊断头，最多三次；重新核对身份和会话后只重发尚未完成的只读请求。身份变化、三次失败或端口不能确认关闭时结束任务。后台恢复连接不能自动重启失败任务。不得在车辆写入或恢复过程中做断线试验。
6. 停止任务并断开。期望：程序确认实际子进程和端口释放，才能开始下一次连接。保存失败时保留当前批次，换有效目录重试保存；未保存就换选项或离开页面会丢弃旧批次。

## X431 厂商参数：补采及适配门槛

完整目标覆盖所有控制单元及完整 X431 设码，不以七单元或标准六项结束。先查 `collection/all-control-units/全控制单元采集.md`，按实际安装情况与完整身份选择候选版本；对应 JSON 保留身份/实时请求组、命名设码字段及 LID/DID 原码范围。无映射菜单也保留缺口，候选不等于本车装配。按时间分批采集所有实际安装单元，之后逐版本建立读取、设码、回读与当前单元恢复资格。

1. 当前 DME 有 365 项定义、31 个请求组；历史覆盖 217 项、16 组，还缺 148 项、15 组。项目的“开始厂商参数演练”使用完整历史 PDU，支持分组处理、连续更新、停止、曲线和完整保存，但没有实车厂商传输入口。先使用演练核对界面与结果来源；不得把演练次数统计为实车采样。
2. 用 X431 自带的已适配只读菜单采集 `collection/接车采集.md` 所列信息。先保存完整 DME 身份与版本、会话进入/退出报文，再按请求组选参，每批暂定最多 12 项。保存实际名称、单位、工况、完整请求/响应、错误和原始抓包。缺响应组优先，同一组的多个字段不需重复抓同一个响应。
3. 保留原始文件并记录 SHA-256。回家核对 7E0/7E8 地址、诊断头流、采集阶段、完整 PDU、版本及解码，再为独立头建立明确会话/请求资格并测试长响应。X431 已有历史响应不自动授权 vLinker 发送；本轮没有扩大厂商读取白名单。

## 内网 CAN：另一次独立只收测试

1. 先停止诊断任务并等待实际断开，再由你手动更换接线，按实际网络选择“内网-驱动can / 内网-底盘can / 内网-舒适性can / 内网-碰撞can”。软件不识别或切换物理网络。当前只有 MX+ 的驱动 CAN 500 kbit/s 具名软件接收配置；底盘、舒适性、碰撞及其他诊断头保持未适配，不沿用驱动 CAN 配置。旧 ADAS 记忆须按实际接线重新选择，不当作当前可选用途。
2. 开始接收并观察持续数据；跨页面继续接收，掉线后继续接收新帧。保存本批完整原始帧，必要时显式启动 PCAPNG。此时没有已验证广播信号定义，显示原始 ID/字节，不声称已解析车辆数值。
3. 用独立总线监测确认接收模式不发送诊断请求，也不产生 CAN ACK；软件配置 `STCMM0` 不能替代物理静默证明。没有这项证据，不把接收功能记为本车已验收。

## 统一停止条件及结果填写

身份/版本不符、响应未终结、掉线重试用尽、关闭失败、车辆状态异常或供电条件不满足时，立即停止当前测试，不换未知地址、请求或版本继续。保留原始文件、电脑时钟来源/偏差不确定性和失败时间，不删除部分结果。

本轮首次接车不执行清故障码、设码、恢复原码、维护例程或固件刷写。当前受限清码另行验收；设码须先取得新鲜完整原值、明确字段、身份匹配、持久备份、精确写入/回读及当前单元恢复方案。固件还需原厂真实性、具体配方、有线头资格和失败恢复证据，蓝牙不刷固件。

每项填写：实际起止时间、实际版本与身份、操作、预期、实际结果、通过/失败/未测试、原始文件和 SHA-256。失败仍保留本批已收内容、错误、时间及正常释放/关闭失败记录，未测试不填通过。目录中的 SQLite 快照仅备份开发库；正式安装的 userData 及外部另存文件应另行备份。
'''


def prepare(output, *, repo=ROOT, bundle=DEFAULT_OUTPUT):
    output, repo = Path(output).resolve(), Path(repo).resolve()
    if not output.is_relative_to(repo / '.local'):
        raise ValueError('output-must-be-private-local')
    if output.exists():
        raise ValueError('output-already-exists')
    validation = validate_bundle(bundle)
    output.mkdir(parents=True)
    pack = build_pack(bundle, output / 'collection')
    report = {'schemaVersion': 1, 'createdUtc': datetime.now(timezone.utc).isoformat(),
              'platform': platform.platform(), 'python': platform.python_version(),
              'noDeviceIO': True, 'vehicleValidated': False, 'definitions': validation,
              'collection': pack, 'developmentDb': {'present': False}, 'codingBackups': [],
              'pending': ['adapter-power/open/close', 'physical-silent-receive', 'run-PCAPNG-check-on-target-PC',
                          'manufacturer-live-transport', 'clean-Windows-install', 'vehicle-acceptance']}
    report['node'] = subprocess.run(['node', '--version'], check=True, capture_output=True,
                                   text=True, timeout=5).stdout.strip()
    source_db = repo / '.local/garage.db'
    if source_db.is_file():
        target = output / 'db-backup/garage.db'; target.parent.mkdir()
        with closing(sqlite3.connect(source_db.as_uri() + '?mode=ro', uri=True)) as source:
            with closing(sqlite3.connect(target)) as destination:
                source.backup(destination)
                integrity = destination.execute('PRAGMA integrity_check').fetchone()[0]
                if integrity != 'ok':
                    raise ValueError('db-backup-integrity-failed')
                table_count = destination.execute("SELECT COUNT(*) FROM sqlite_master WHERE type='table'").fetchone()[0]
        report['developmentDb'] = {'present': True, 'source': str(source_db), 'backup': str(target),
                                   'bytes': target.stat().st_size, 'sha256': sha256_file(target),
                                   'integrity': integrity, 'tableCount': table_count}
    vault = repo / '.local/diagnostics/coding-backups'
    total = 0
    if vault.exists():
        for source in sorted(vault.rglob('*')):
            if source.is_symlink():
                raise ValueError('coding-backup-symlink')
            if not source.is_file():
                continue
            if not source.resolve().is_relative_to(vault.resolve()):
                raise ValueError('coding-backup-path')
            total += source.stat().st_size
            if total > 1024 ** 3 or source.stat().st_size > 512 * 1024 ** 2 or len(report['codingBackups']) >= 10000:
                raise ValueError('coding-backup-capacity')
            target = output / 'coding-backups' / source.relative_to(vault)
            target.parent.mkdir(parents=True, exist_ok=True)
            before = sha256_file(source); shutil.copyfile(source, target)
            if before != sha256_file(target) or before != sha256_file(source):
                raise ValueError('coding-backup-changed-during-copy')
            report['codingBackups'].append({'file': str(target.relative_to(output)), 'sha256': before})
    (output / '首次接车清单.md').write_text(CHECKLIST, encoding='utf-8')
    (output / 'environment.json').write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir', type=Path, default=ROOT / '.local/diagnostics' /
                        ('precar-' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')))
    args = parser.parse_args()
    report = prepare(args.output_dir)
    print(json.dumps({'ok': True, 'output': str(args.output_dir.resolve()), 'noDeviceIO': True,
                      'dbBackup': report['developmentDb']['present'], 'vehicleValidated': False}))


if __name__ == '__main__': main()
