# Waydroid 的 MuMu 接口兼容层

独立原型，使用 MAA 已有 MuMu SDK ABI，不修改 MAA 客户端二进制与资源。

- `external_renderer_ipc.dll` 是 winegcc 构建的 ELF Winelib DLL，导出 `nemu_connect`、`nemu_capture_display` 等接口。
- 截图来自现有 Gamescope 内的 1280×720 Weston X11 窗口，避开 Android 的单次 screencap 进程与编码开销，不包含 MangoHud 覆盖层。
- 原生服务与 DLL 通过只允许当前 UID 的 0600 Unix socket 通信，服务每收到 C 请求后执行新的 XGetImage，返回底部向上的 RGBA，供 MAA 原有的 MuMu 解码逻辑翻转。
- 输入同样已实现：`nemu_input_event_touch_down/up` 与 `nemu_input_event_key_down/up`
  经宿主原生 `adb shell` 注入 Android 13 的 `input motionevent` / `input keyevent`。
  `adb shell` 保持长连接，避免每个事件 fork 一次（单次约 70 ms，拖拽会明显卡顿）。
  因此 MAA 可以直接使用 MuMu 触控增强（`TouchMode: MumuExtras`），不必退回 `MaaTouch`。
- `nemu_input_text` 仍返回失败：当前任务链走 OCR 而非输入框，中文输入用不到。
- 需要宿主安装 `android-tools`；服务端连 `127.0.0.1:5555`，与 MAA 共用容器里同一个
  adbd，不额外开放网络端口。
- X11 生命周期在独立进程中隔离，避免显示服务器退出导致 MAA 进程被 Xlib 终止；systemd 用户服务自动重新探测专用 Weston。

```bash
make
winegcc -m64 -O2 -o build/test-sdk.exe test_sdk.c
winegcc -m64 -O2 -o build/test-input.exe test_input.c
WINEPREFIX="$HOME/.local/share/lutris/prefixes/maa" build/test-sdk.exe 'Z:\path\to\sdk\shell\sdk\external_renderer_ipc.dll'
WINEPREFIX="$HOME/.local/share/lutris/prefixes/maa" build/test-input.exe 'Z:\path\to\sdk\shell\sdk\external_renderer_ipc.dll'
```

`test_input.exe` 输出 `INPUT_OK taps=6 keys=1` 表示输入链路可用；它在真实会话里
点击屏幕中央五次，跑之前确认当前前台程序可以接受点击。

注意 winegcc 产生的 `.exe` 是 shell 启动器，直接执行它，不能把该 shell 文件交给 `wine`。

`test_maacore.c` 使用未修改的 Windows MaaCore.dll 加载资源并测试连接/截图，不添加或启动任何游戏任务。20 次测试观察到 8–22 ms；实时 MAA GUI 随后记录 21 ms（MumuExtras）。这不是全程自动游戏任务或所有图形会话的性能保证。核对静态画面及后台截图，与原版 Android 截图的 RGB 平均差异在测试时小于 1/255。

安装：`~/.local/share/waydroid-mumu-capture/`；服务：`waydroid-mumu-capture.service`；日志：`journalctl --user -u waydroid-mumu-capture.service`。

参考接口：
- https://github.com/MaaAssistantArknights/MaaAssistantArknights/blob/dev/src/MaaCore/Controller/MumuExtras.cpp
- https://docs.maa.plus/zh-cn/manual/connection.html
