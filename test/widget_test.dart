import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:arknights_waydroid_toolbox/main.dart';

void main() {
  testWidgets('shows MAA schedules and exposes enhancement controls without launching games', (tester) async {
    tester.view.physicalSize = const Size(1200, 900);
    tester.view.devicePixelRatio = 1;
    addTearDown(tester.view.resetPhysicalSize);
    addTearDown(tester.view.resetDevicePixelRatio);
    final calls = <String>[];
    Future<Map<String, dynamic>> fake(String action) async {
      calls.add(action);
      return {
        'maa_running': true, 'waydroid': {'Session': 'RUNNING'},
        'capture_ready': true, 'capture_enabled': true, 'scheduler_enabled': false,
        'lead_minutes': 5, 'maa_capture_result': '21ms (MumuExtras)',
        'slots': [{'id': 0, 'enabled': true, 'time': '03:00', 'prepare': '02:55', 'configuration': 'Default', 'once': false}],
      };
    }
    await tester.pumpWidget(ToolboxApp(invoke: fake));
    await tester.pumpAndSettle();
    expect(find.text('明日方舟'), findsOneWidget);
    await tester.tap(find.text('自动化定时'));
    await tester.pumpAndSettle();
    expect(find.text('03:00'), findsOneWidget);
    expect(find.textContaining('02:55 Linux 准备'), findsOneWidget);
    await tester.tap(find.text('截图增强'));
    await tester.pumpAndSettle();
    expect(find.text('21ms (MumuExtras)'), findsOneWidget);
    expect(find.text('接口测速 · 20 次'), findsOneWidget);
    expect(calls.every((x) => x == 'status'), isTrue);
    expect(tester.takeException(), isNull);
    await tester.pumpWidget(const SizedBox.shrink());
  });
}
