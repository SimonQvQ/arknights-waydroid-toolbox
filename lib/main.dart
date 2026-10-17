import 'dart:async';
import 'dart:convert';
import 'dart:io';
import 'package:flutter/material.dart';
import 'package:flutter_localizations/flutter_localizations.dart';

typedef Invoke = Future<Map<String, dynamic>> Function(String action);
const accent = Color(0xff9bded2);
const background = Color(0xff101414);
const panel = Color(0xff1a2020);

Future<Map<String, dynamic>> invokeBackend(String action) async {
  final backend = Platform.environment['ARKNIGHTS_TOOLBOX_BACKEND'] ??
      '/usr/lib/arknights-waydroid-toolbox/toolbox.py';
  final result = await Process.run('/usr/bin/python3', [backend, action])
      .timeout(const Duration(minutes: 4));
  final response = jsonDecode(result.stdout.toString()) as Map<String, dynamic>;
  if (response['ok'] != true) {
    throw Exception(response['error'] ?? result.stderr.toString());
  }
  return response['data'] as Map<String, dynamic>;
}

void main() => runApp(const ToolboxApp());

class ToolboxApp extends StatelessWidget {
  final Invoke? invoke;
  const ToolboxApp({super.key, this.invoke});
  @override
  Widget build(BuildContext context) => MaterialApp(
    title: '方舟 · Waydroid 工具箱',
    debugShowCheckedModeBanner: false,
    locale: const Locale('zh', 'CN'),
    supportedLocales: const [Locale('zh', 'CN')],
    localizationsDelegates: GlobalMaterialLocalizations.delegates,
    theme: ThemeData(
      useMaterial3: true,
      brightness: Brightness.dark,
      scaffoldBackgroundColor: background,
      colorScheme: ColorScheme.fromSeed(
        seedColor: accent, brightness: Brightness.dark,
        primary: accent, surface: panel,
      ),
      fontFamily: 'Noto Sans CJK SC',
      cardTheme: const CardThemeData(
        color: panel, margin: EdgeInsets.zero, elevation: 0,
        shape: RoundedRectangleBorder(borderRadius: BorderRadius.all(Radius.circular(22))),
      ),
      filledButtonTheme: FilledButtonThemeData(style: FilledButton.styleFrom(
        padding: const EdgeInsets.symmetric(horizontal: 22, vertical: 18),
        shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(14)),
      )),
      outlinedButtonTheme: OutlinedButtonThemeData(style: OutlinedButton.styleFrom(
        padding: const EdgeInsets.symmetric(horizontal: 18, vertical: 18),
        shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(14)),
      )),
    ),
    home: ToolboxHome(invoke: invoke ?? invokeBackend),
  );
}

class ToolboxHome extends StatefulWidget {
  final Invoke invoke;
  const ToolboxHome({super.key, required this.invoke});
  @override
  State<ToolboxHome> createState() => _ToolboxHomeState();
}

class _ToolboxHomeState extends State<ToolboxHome> {
  Map<String, dynamic> data = {};
  int page = 0;
  bool busy = false;
  bool refreshing = false;
  String? error;
  String? previewPath;
  int previewVersion = 0;
  Timer? timer;
  final titles = ['总览', '自动化定时', '截图增强', '运行日志'];
  final icons = [Icons.dashboard_rounded, Icons.schedule_rounded,
    Icons.filter_center_focus_rounded, Icons.article_outlined];
  String logs = '点击“刷新日志”查看最近的准备与截图服务记录。';

  @override
  void initState() {
    super.initState();
    refresh();
    timer = Timer.periodic(const Duration(seconds: 10), (_) {
      if (!busy) refresh();
    });
  }
  @override
  void dispose() { timer?.cancel(); super.dispose(); }

  Future<void> refresh() async {
    if (refreshing) return;
    refreshing = true;
    try {
      final value = await widget.invoke('status');
      if (mounted) setState(() { data = value; error = null; });
    } catch (e) {
      if (mounted) setState(() => error = e.toString());
    } finally { refreshing = false; }
  }

  Future<void> action(String command) async {
    if (busy) return;
    setState(() { busy = true; error = null; });
    try {
      final result = await widget.invoke(command);
      if (!mounted) return;
      if (command == 'preview') {
        final path = result['path'] as String;
        await FileImage(File(path)).evict();
        if (!mounted) return;
        setState(() { previewPath = path; previewVersion++; });
      } else if (command == 'logs') {
        setState(() => logs = result['text'] as String? ?? '暂无日志');
      }
      if (result['message'] != null && mounted) {
        ScaffoldMessenger.of(context).showSnackBar(SnackBar(
          content: Text(result['message'].toString()),
        ));
      }
      await refresh();
    } catch (e) {
      if (mounted) setState(() => error = e.toString());
    } finally {
      if (mounted) setState(() => busy = false);
    }
  }

  List<Map<String, dynamic>> get schedules =>
      (data['slots'] as List? ?? []).cast<Map<String, dynamic>>();
  List<Map<String, dynamic>> get enabled => schedules.where((x) => x['enabled'] == true).toList();
  bool get running => (data['waydroid'] as Map?)?['Session'] == 'RUNNING';
  bool get maaRunning => data['maa_running'] == true;
  Widget gap([double size = 18]) => SizedBox(height: size);

  @override
  Widget build(BuildContext context) => Scaffold(
    body: SafeArea(child: LayoutBuilder(builder: (context, constraints) {
      final wide = constraints.maxWidth >= 1050;
      return Row(children: [
        Container(
          width: wide ? 216 : 84,
          padding: EdgeInsets.symmetric(horizontal: wide ? 18 : 10, vertical: 28),
          decoration: const BoxDecoration(border: Border(right: BorderSide(color: Color(0xff2a3231)))),
          child: Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
            const Icon(Icons.change_history_rounded, size: 38, color: accent),
            if (wide) ...[
              gap(10), const Center(child: Text('方舟工具箱', style: TextStyle(fontSize: 19, fontWeight: FontWeight.w700))),
              gap(4), const Center(child: Text('WAYDROID', style: TextStyle(fontSize: 11, letterSpacing: 3, color: Colors.white38))),
            ],
            gap(40),
            for (int i = 0; i < titles.length; i++) Padding(
              padding: const EdgeInsets.only(bottom: 10),
              child: Tooltip(message: titles[i], child: TextButton(
                style: TextButton.styleFrom(
                  alignment: wide ? Alignment.centerLeft : Alignment.center,
                  foregroundColor: page == i ? accent : Colors.white60,
                  backgroundColor: page == i ? const Color(0xff263b37) : Colors.transparent,
                  minimumSize: const Size(50, 52),
                  shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(14)),
                ),
                onPressed: () => setState(() => page = i),
                child: Row(mainAxisSize: MainAxisSize.min, children: [
                  Icon(icons[i], size: 22),
                  if (wide) ...[const SizedBox(width: 14), Text(titles[i])],
                ]),
              )),
            ),
            const Spacer(),
            if (wide) const Text('CachyOS · Niri\n由本机服务管理', textAlign: TextAlign.center,
              style: TextStyle(fontSize: 11, height: 1.8, color: Colors.white38)),
          ]),
        ),
        Expanded(child: Column(children: [
          if (busy) const LinearProgressIndicator(minHeight: 3),
          Expanded(child: SingleChildScrollView(
            padding: EdgeInsets.all(wide ? 30 : 22),
            child: Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
              Row(children: [
                Expanded(child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
                  Text(titles[page], style: const TextStyle(fontSize: 28, fontWeight: FontWeight.w700)),
                  gap(6), const Text('Waydroid、MAA 与每日自动化', style: TextStyle(color: Colors.white54)),
                ])),
                IconButton(tooltip: '刷新状态', onPressed: busy ? null : refresh,
                  icon: const Icon(Icons.refresh_rounded)),
              ]),
              gap(26),
              if (error != null) ...[
                Card(color: const Color(0xff4c2928), child: Padding(
                  padding: const EdgeInsets.all(16), child: SelectableText(error!),
                )), gap(),
              ],
              if (data.isEmpty && error == null) const Center(child: CircularProgressIndicator()),
              if (data.isNotEmpty) ...switch (page) {
                0 => overview(), 1 => automation(), 2 => enhancement(), _ => logging(),
              },
            ]),
          )),
        ])),
      ]);
    })),
  );

  Widget box({required Widget child, Color? color}) => Card(
    color: color, child: Padding(padding: const EdgeInsets.all(22), child: child),
  );
  Widget heading(String title, String subtitle, IconData icon) => Row(children: [
    Icon(icon, color: accent), const SizedBox(width: 12),
    Expanded(child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
      Text(title, style: const TextStyle(fontSize: 18, fontWeight: FontWeight.w600)), gap(5),
      Text(subtitle, style: const TextStyle(fontSize: 12, color: Colors.white54)),
    ])),
  ]);
  Widget pill(String text, bool active) => Container(
    padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 6),
    decoration: BoxDecoration(color: active ? const Color(0xff29453d) : const Color(0xff303635),
      borderRadius: BorderRadius.circular(20)),
    child: Text(text, style: TextStyle(fontSize: 12, color: active ? accent : Colors.white54)),
  );
  Widget button(String text, IconData icon, String command, {bool primary = false}) => primary
    ? FilledButton.icon(onPressed: busy ? null : () => action(command), icon: Icon(icon, size: 20), label: Text(text))
    : OutlinedButton.icon(onPressed: busy ? null : () => action(command), icon: Icon(icon, size: 20), label: Text(text));

  List<Widget> overview() => [
    box(color: const Color(0xff203630), child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
      Row(children: [const Icon(Icons.sports_esports_rounded, size: 38, color: accent),
        const Spacer(), pill('720P · MangoHud', true)]), gap(22),
      const Text('明日方舟', style: TextStyle(fontSize: 30, fontWeight: FontWeight.w700)), gap(8),
      Text(running ? '显示全屏游戏并查看性能监控。' : '启动 Waydroid、全屏游戏与性能监控。',
        style: const TextStyle(color: Colors.white60)), gap(24),
      Wrap(spacing: 12, runSpacing: 12, children: [
        button(running ? '显示游戏' : '启动游戏与 HUD', Icons.play_arrow_rounded, 'game-start', primary: true),
        button(maaRunning ? '打开 MAA' : '启动 MAA', Icons.smart_toy_outlined, 'maa-start'),
        if (running) button('停止 Waydroid', Icons.stop_rounded, 'game-stop'),
      ]),
    ])), gap(),
    LayoutBuilder(builder: (context, c) {
      final cards = [
        statusTile('Waydroid', running ? '正在运行' : '已停止', Icons.layers_outlined, running),
        statusTile('MAA', maaRunning ? '已运行' : '未运行', Icons.smart_toy_outlined, maaRunning),
        statusTile('截图增强', data['capture_ready'] == true ? '接口就绪' : '等待游戏启动',
          Icons.bolt_rounded, data['capture_ready'] == true),
      ];
      return Wrap(spacing: 14, runSpacing: 14, children: [
        for (final card in cards) SizedBox(width: c.maxWidth >= 610 ? (c.maxWidth - 28) / 3 : c.maxWidth, child: card),
      ]);
    }), gap(),
    box(child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
      heading('每日定时', '跟随 MAA 中启用的时间', Icons.schedule_rounded), gap(20),
      Wrap(spacing: 12, runSpacing: 12, children: [
        for (final s in enabled) pill(s['time'] as String, true),
        if (enabled.isEmpty) const Text('MAA 中还没有启用的定时。'),
      ]), gap(16),
      Text(data['scheduler_enabled'] == true ? 'Linux 定时已启用 · 提前 ${data['lead_minutes']} 分钟准备环境' : 'Linux 定时尚未启用',
        style: const TextStyle(color: Colors.white54, fontSize: 12)),
    ])),
  ];

  Widget statusTile(String label, String value, IconData icon, bool active) => box(child: Column(
    crossAxisAlignment: CrossAxisAlignment.start, children: [
      Icon(icon, color: active ? accent : Colors.white38), gap(16),
      Text(label, style: const TextStyle(color: Colors.white54, fontSize: 12)), gap(6),
      Text(value, style: const TextStyle(fontWeight: FontWeight.w600)),
    ],
  ));

  List<Widget> automation() => [
    box(child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
      Row(children: [
        Expanded(child: heading('Linux 定时准备', '关闭工具箱窗口后仍然有效', Icons.event_available_rounded)),
        Switch(value: data['scheduler_enabled'] == true,
          onChanged: busy ? null : (v) => action(v ? 'scheduler-on' : 'scheduler-off')),
      ]), gap(20),
      Text('提前 ${data['lead_minutes']} 分钟启动游戏与 MAA，到点执行 MAA 中勾选的任务。',
        style: const TextStyle(height: 1.8, color: Colors.white70)), gap(10),
      const Text('需保持桌面已登录、电脑处于唤醒状态。不会自动开机或唤醒睡眠中的电脑。',
        style: TextStyle(fontSize: 12, height: 1.8, color: Colors.white38)), gap(18),
      Wrap(spacing: 12, runSpacing: 12, children: [
        button('立即同步配置', Icons.sync_rounded, 'sync'),
        button('打开 MAA', Icons.open_in_new_rounded, 'maa-start'),
      ]),
    ])), gap(),
    for (final s in schedules) ...[
      box(child: Row(children: [
        Container(width: 46, height: 46, decoration: BoxDecoration(
          color: s['enabled'] == true ? const Color(0xff29453d) : const Color(0xff292e2d),
          borderRadius: BorderRadius.circular(14)),
          child: Icon(Icons.alarm_rounded, color: s['enabled'] == true ? accent : Colors.white30)),
        const SizedBox(width: 18),
        Expanded(child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
          Text(s['time'] as String, style: const TextStyle(fontSize: 23, fontWeight: FontWeight.w600)), gap(4),
          Text(s['enabled'] == true ? '${s['prepare']} Linux 准备 · ${s['configuration']}' : '在 MAA 中启用后自动同步',
            style: const TextStyle(color: Colors.white54, fontSize: 12)),
        ])),
        pill(s['once'] == true ? '单次 · MAA 管理' : s['enabled'] == true ? '每天' : '未启用', s['enabled'] == true),
      ])), gap(12),
    ],
    if (data['last_prepare'] is Map) box(child: Text(
      '上次准备：${(data['last_prepare'] as Map)['at']}\n${(data['last_prepare'] as Map)['message']}',
      style: const TextStyle(height: 1.8, fontSize: 12, color: Colors.white54))),
  ];

  List<Widget> enhancement() {
    final measurement = data['benchmark'] as Map?;
    return [
      box(child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
        Row(children: [
          Expanded(child: heading('MuMu 接口截图增强', '为当前 720p Waydroid 提供快速取图', Icons.bolt_rounded)),
          Switch(value: data['capture_enabled'] == true,
            onChanged: busy ? null : (v) => action(v ? 'capture-on' : 'capture-off')),
        ]), gap(20),
        const Text('使用独立兼容层接入 MAA 的 MuMu 截图接口，控制仍使用 MaaTouch。',
          style: TextStyle(color: Colors.white60, height: 1.8)), gap(8),
        const Text('切换截图模式会重启 MAA。游戏画面需通过本工具箱的 HUD 启动器运行。',
          style: TextStyle(fontSize: 12, color: Colors.white38, height: 1.8)), gap(22),
        Wrap(spacing: 12, runSpacing: 12, children: [
          button('接口测速 · 20 次', Icons.speed_rounded, 'benchmark', primary: true),
          button('截图预览', Icons.photo_outlined, 'preview'),
        ]),
      ])), gap(),
      box(child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
        const Text('接口响应中位数', style: TextStyle(color: Colors.white54)), gap(12),
        Text(measurement == null ? '等待测速' : '${measurement['median']} ms',
          style: const TextStyle(fontSize: 38, fontWeight: FontWeight.w700, color: accent)),
        if (measurement != null) ...[
          gap(8), Text('最低 ${measurement['min']} ms · 最高 ${measurement['max']} ms · ${measurement['count']} 次',
            style: const TextStyle(color: Colors.white54, fontSize: 12)),
        ], gap(22),
        const Divider(color: Color(0xff303a37)), gap(12),
        const Text('MAA 最近截图测试', style: TextStyle(color: Colors.white54, fontSize: 12)), gap(6),
        Text((data['maa_capture_result'] as String? ?? '').isEmpty ? '在 MAA 连接设置中运行截图测试' : data['maa_capture_result'] as String,
          style: const TextStyle(fontSize: 18, fontWeight: FontWeight.w600)),
      ])), gap(),
      if (previewPath != null) box(child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
        heading('当前截图', '1280 × 720 · 不含 MangoHud 覆盖层', Icons.image_outlined), gap(18),
        ClipRRect(borderRadius: BorderRadius.circular(12), child: Image.file(
          File(previewPath!), key: ValueKey(previewVersion), fit: BoxFit.contain,
          errorBuilder: (context, error, trace) => const Text('预览文件不可用，请重新截图'),
        )),
      ])),
    ];
  }

  List<Widget> logging() => [
    box(child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
      heading('运行记录', '定时准备与截图兼容服务', Icons.article_outlined), gap(20),
      Wrap(spacing: 12, runSpacing: 12, children: [
        button('刷新日志', Icons.refresh_rounded, 'logs'),
        button('MAA 配置目录', Icons.folder_open_rounded, 'config-folder'),
      ]), gap(22),
      Container(width: double.infinity, padding: const EdgeInsets.all(18),
        decoration: BoxDecoration(color: background, borderRadius: BorderRadius.circular(14)),
        child: SelectableText(logs, style: const TextStyle(fontFamily: 'monospace', fontSize: 12, height: 1.7))),
    ])), gap(),
    box(child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
      const Text('当前连接', style: TextStyle(fontSize: 16, fontWeight: FontWeight.w600)), gap(12),
      SelectableText('ADB  ${data['address']}\n触控  ${data['touch']}\n配置  ${data['configuration']}',
        style: const TextStyle(height: 2, color: Colors.white60)),
    ])),
  ];
}
