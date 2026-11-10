# 方舟 Waydroid 工具箱

Flutter Linux 桌面工具：整合《明日方舟》Waydroid HUD 启动器、MAA Wine 启动、Linux 定时准备，以及独立的 MuMu 接口截图兼容层。

目前是针对 **CachyOS / Niri / Waydroid / Lutris + Wine / 1280×720** 的实验性项目，尚未适配所有发行版和桌面环境。使用 MIT 许可证。

## 功能

- Gamescope + MangoApp + Weston 内嵌 Waydroid，支持 HUD 和全屏启动。
- 启动现有 Lutris 中的 Windows MAA，保留独立 Wine 前缀。
- 截图增强开关、预览、20 次接口测速、普通 ADB 回退和运行日志。
- 读取 MAA `config/gui.new.json` 的 `Timers.List`，生成用户级 systemd 定时器，并监听配置保存后自动同步。
- 每个定时可选：开始前 1 分钟发送桌面通知，任务完成后自动关闭 Waydroid 会话和 MAA。

## 关闭窗口后，定时还有效吗？

**有效。** Linux 在 MAA 定时前 5 分钟按需启动 Waydroid、连接 ADB 并启动 MAA；已有会话不会被定时准备重启。到点由 **MAA 自己** 执行其配置好的任务，工具箱不复制任务队列，也不直接调用任务执行 API。

准备阶段若发现 Waydroid 跑在错误的显示会话上（例如手动 `waydroid session start` 落在 Niri 的 `wayland-1`），会先停止该会话再通过 HUD 重新拉起 `waydroid-mangoapp`，因为 MuMu 截图接口只在 Weston 持有会话时可用，且运行中的 Android 合成器无法在 Wayland socket 之间搬迁。每次准备也会校正 MAA 连接配置里的 `Config`/`TouchMode`/`EnableBridgeConnection`——MAA 的首次引导和 MuMu 保活探测会把这些字段改坏，表现为「触控模式不可用」。正在运行的 MAA 不会被重启，改动在下次启动生效。

启用「任务完成后自动关闭」后，常驻的 `arknights-toolbox-watcher.service` 跟随 MAA 日志：出现 `BeginRun` 后一旦队列排空（`Idle: true to true (called from ProcTaskChainMsg)`），就关闭 MAA 并执行 `waydroid session stop`。手动中止的任务不会触发，避免误关。

启动的图形应用使用独立的 systemd 用户服务，定时准备结束后继续运行；HUD 启动器会回收失去 Weston 的残留 Gamescope，避免启动锁阻塞。Android 与 Wine 冷启动的短暂查询失败会自动重试。

工具箱窗口不必常驻；关闭窗口后也不会立刻重新弹出。电脑需要已登录图形桌面并保持唤醒；不自动开机或唤醒睡眠，不补跑错过的时间。MAA 的半选单次定时仍由 MAA 管理，不转换成每日重复任务。

定时单元为 `arknights-toolbox-slot-0.timer` 等，仅管理自己的 0–7 号槽位；提醒单元为 `arknights-toolbox-slot-0-remind.timer`。配置读取失败时保留已有定时器。准备失败可在日志页面查看，不能据此保证游戏任务成功完成。

## 构建和安装

构建工具：Flutter（开发时使用 3.47.7，可通过 AUR `flutter-bin` 安装）、CMake、Ninja、GTK3 开发文件、GCC、libX11、Wine/winegcc。运行需要 Python 3、Pillow、systemd 用户会话，以及已初始化的 Waydroid、Lutris、Wine；HUD 另需 Gamescope、MangoApp、Weston 和 Xwayland。

先在 Waydroid 安装游戏并确认能正常运行；x86 主机运行 ARM 游戏需要自行配置兼容翻译层。本项目不包含 Android 镜像、游戏资源、MAA、Wine 前缀、字体或 MuMu 软件。

```bash
flutter pub get
flutter analyze
flutter test
python backend/test_toolbox.py
python hud/test_lifecycle.py
flutter build linux --release

# Arch/CachyOS 本地打包（尚未发布 AUR）
python packaging/build_bundle.py
cd packaging
makepkg --noconfirm
sudo pacman -U arknights-waydroid-toolbox-bin-*.pkg.tar.zst
cd ..

# 可选：编译并安装当前用户的截图兼容层与 HUD 启动器
make -C capture
python scripts/install-integrations.py
```

集成安装器会备份被替换的当前用户文件、安装启动脚本并启用截图服务。它不会安装游戏、授权 ADB、启用定时或自动切换 MAA 连接方式。不要使用 sudo 运行此安装器。

开发运行：

```bash
ARKNIGHTS_TOOLBOX_BACKEND="$PWD/backend/toolbox.py" flutter run -d linux
```

安装后运行 `arknights-toolbox`，或从应用菜单打开「方舟 Waydroid 工具箱」。

## 首次配置

将 MAA Windows 版及官方 Windows platform-tools 放入自己的目录，并在 Lutris 中配置 MAA Wine 游戏项。根据实际情况编辑 `~/.config/arknights-toolbox/settings.json`；路径均为 Linux 绝对路径：

```json
{
  "maa_dir": "/home/YOUR_USER/.local/share/maa-wine/app",
  "wine_prefix": "/home/YOUR_USER/.local/share/lutris/prefixes/maa",
  "hud_launcher": "/home/YOUR_USER/.local/bin/arknights-waydroid-hud",
  "sdk_dir": "/home/YOUR_USER/.local/share/waydroid-mumu-capture/sdk",
  "lutris_game_id": 2,
  "lead_minutes": 5,
  "scheduler_enabled": false
}
```

其中 `lutris_game_id` 必须是你自己 MAA 项目的 ID。MAA 至少启动过一次，生成 `config/gui.new.json`。在 MAA 中设置 Windows `adb.exe` 路径和 Waydroid 实际 IP:5555，完成 Android ADB 授权，使用 **MaaTouch** 和 ADB Lite。不要开启 MuMu 触控增强。

先手动确认 HUD、MAA 和连接测试成功，再在工具箱启用截图增强及 Linux 定时。HUD 启动脚本首次启动会停止已有 Waydroid 会话以切换显示环境；定时准备检测到其他 Waydroid 显示会话时会先停止它，再通过 HUD 重新拉起 `waydroid-mangoapp`。

## 截图兼容层的原理和范围

[`capture/`](capture/) 是独立实现，不包含 MuMu 的专有 DLL。winegcc 构建的 Winelib DLL 实现 MAA 已有的部分 MuMu SDK ABI，经当前用户专用 Unix socket 请求原生服务即时读取内层 Weston 的 X11 窗口。MAA 从自己的连接设置加载它；不替换 MAA.exe、MaaCore.dll 或修改资源文件。输入仍使用 MaaTouch。

目前固定为本项目启动器中的单个 1280×720 Weston 窗口，不支持任意分辨率、多开或 Android 安全画面。每次请求执行新的 XGetImage，不预先生成或重复发送缓存图片。服务故障与 MAA 进程隔离，systemd 会重新探测专用 Weston。

开发机上的未修改 MAA v6.19.0 曾测得截图 8–22 ms，GUI 记录 21 ms（MumuExtras）。这是取得已呈现画面的接口耗时，**不是游戏帧率、输入总延迟，也不是其他设备的性能保证**。

## 维护与卸载

```bash
systemctl --user list-timers 'arknights-toolbox-*'
journalctl --user -u 'arknights-toolbox-*' -u waydroid-mumu-capture.service
python /usr/lib/arknights-waydroid-toolbox/toolbox.py prepare --slot 0 --dry-run
```

用户配置：`~/.config/arknights-toolbox/settings.json`。日志、截图预览、MAA 连接配置备份：`~/.local/state/arknights-toolbox/`。MAA 连接方式切换会先请求关闭 MAA，再备份配置；请在任务空闲时操作。

卸载前先在工具箱关闭 Linux 定时并切回普通 ADB 截图，然后：

```bash
systemctl --user disable --now waydroid-mumu-capture.service
sudo pacman -Rns arknights-waydroid-toolbox-bin
```

截图服务、SDK 与 HUD 是独立的用户级安装，可根据安装器输出的备份手动恢复或移除。不要在 MAA 仍引用该 SDK 时删除它。

## 来源与许可证

本项目代码采用 [MIT](LICENSE)。Flutter 等依赖保留各自许可证；构建包中包含 Flutter 的依赖许可证信息。本项目与鹰角、MAA、MuMu、Waydroid 无官方隶属关系。

- MuMu ABI 对接参考：MAA 项目的 `src/MaaCore/Controller/MumuExtras.cpp`（https://github.com/MaaAssistantArknights/MaaAssistantArknights）。
- MAA 使用文档：https://docs.maa.plus/zh-cn/manual/connection.html
- [`capture/README.md`](capture/README.md) 记录截图测试范围。

## 已知的 vendor 镜像问题

20260927 MAINLINE vendor 在开发机出现显示焦点/HWC 死锁，Android watchdog 在准备后重启框架，随后网络栈失效。症状是 Linux 准备曾成功，而 MAA 到点报连接失败。与上游 https://github.com/waydroid/waydroid/issues/2436 的阻塞栈一致；开发机只回退官方 20260428 MAINLINE vendor 进行验证，未替换 system 或删除用户数据。不要在不了解兼容性时自动回退其他设备；更新镜像前核对上游进展并备份。

启动成功仅表示当时连接正常，不表示之后 MAA 游戏任务完成。排查时要同时查看 Android watchdog/ANR、MAA 日志和 systemd 日志，并覆盖后台等待与窗口焦点切换。
