#!/usr/bin/python3
"""Per-user Waydroid/MAA orchestration. Never starts a MAA task directly."""
from __future__ import annotations
import argparse
import datetime as dt
import fcntl
import json
import os
from pathlib import Path
import re
import select
import shutil
import socket
import statistics
import struct
import subprocess
import sys
import time

HOME = Path.home()
CONFIG = HOME / '.config/arknights-toolbox/settings.json'
STATE = HOME / '.local/state/arknights-toolbox'
UNITS = HOME / '.config/systemd/user'
PREFIX = 'arknights-toolbox-'
HEADER = struct.Struct('<6I3Q')
STATE.mkdir(parents=True, exist_ok=True)


def settings():
    defaults = {'maa_dir': str(HOME / '.local/share/maa-wine/app'),
                'wine_prefix': str(HOME / '.local/share/lutris/prefixes/maa'),
                'hud_launcher': str(HOME / '.local/bin/arknights-waydroid-hud'),
                'sdk_dir': str(HOME / '.local/share/waydroid-mumu-capture/sdk'),
                'lutris_game_id': 2,
                'lead_minutes': 5, 'scheduler_enabled': False}
    if CONFIG.exists():
        defaults.update(json.loads(CONFIG.read_text()))
    return defaults


def atomic_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + '.tmp')
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2))
    temporary.replace(path)


def run(args, timeout=15, check=True, env=None):
    r = subprocess.run(args, capture_output=True, text=True, timeout=timeout, env=env)
    if check and r.returncode:
        raise RuntimeError((r.stderr or r.stdout or f'{args[0]} failed').strip())
    return r.stdout.strip()


def maa_config():
    path = Path(settings()['maa_dir']) / 'config/gui.new.json'
    for attempt in range(4):
        try:
            return path, json.loads(path.read_text())
        except (OSError, json.JSONDecodeError):
            if attempt == 3:
                raise RuntimeError(f'无法读取 MAA 配置：{path}')
            time.sleep(.1)


def slots(config, lead=5):
    current = config.get('Current', 'Default')
    custom = config.get('Timers', {}).get('CustomConfig', False)
    result = []
    for item in config.get('Timers', {}).get('List', []):
        slot_id, hour, minute = item.get('Id'), item.get('Hour'), item.get('Minute')
        if type(slot_id) is not int or not 0 <= slot_id < 8:
            continue
        valid = type(hour) is int and type(minute) is int and 0 <= hour < 24 and 0 <= minute < 60
        enabled = item.get('IsEnabled') is True and valid
        total = (hour * 60 + minute - lead) % 1440 if valid else 0
        selected = (item.get('Config') or current) if custom else current
        result.append({'id': slot_id, 'enabled': enabled,
                       'time': f'{hour:02d}:{minute:02d}' if valid else '未设置',
                       'prepare': f'{total // 60:02d}:{total % 60:02d}',
                       'configuration': selected,
                       'once': item.get('IsEnabled') is None})
    return result


def niri_windows():
    try:
        return json.loads(run(['niri', 'msg', '-j', 'windows'], timeout=4))
    except (RuntimeError, subprocess.TimeoutExpired):
        return []


def maa_running():
    # Wine windows may report the Xwayland PID, so inspect this user's processes.
    for proc in Path('/proc').iterdir():
        if not proc.name.isdigit():
            continue
        try:
            if proc.stat().st_uid == os.getuid() and (proc / 'comm').read_text().strip() == 'MAA.exe':
                return True
        except OSError:
            pass
    return False


def waydroid_status():
    text = run(['waydroid', 'status'], timeout=8, check=False)
    parsed = {}
    for line in text.splitlines():
        key, sep, value = line.partition(':')
        if sep:
            parsed[key.strip()] = value.strip()
    return parsed


def read_exact(stream, length):
    chunks = bytearray()
    while len(chunks) < length:
        data = stream.recv(length - len(chunks))
        if not data:
            raise RuntimeError('截图服务中断')
        chunks.extend(data)
    return bytes(chunks)


def capture(frame=True):
    path = Path(os.environ.get('XDG_RUNTIME_DIR', f'/run/user/{os.getuid()}')) / 'waydroid-mumu-capture.sock'
    with socket.socket(socket.AF_UNIX) as stream:
        stream.settimeout(2)
        stream.connect(str(path))
        stream.sendall(b'C' if frame else b'I')
        h = HEADER.unpack(read_exact(stream, HEADER.size))
        if h[0] != 0x57444331 or h[1] or h[2:4] != (1280, 720):
            raise RuntimeError('未找到正在显示的 720p Waydroid 窗口')
        if frame:
            if h[4] != 1280 * 720 * 4:
                raise RuntimeError('截图格式错误')
            return read_exact(stream, h[4]), h
        return b'', h


def preview():
    from PIL import Image
    data, h = capture()
    path = STATE / 'preview.png'
    Image.frombytes('RGBA', (h[2], h[3]), data).transpose(Image.Transpose.FLIP_TOP_BOTTOM).save(path)
    return {'path': str(path), 'sequence': h[6]}


def benchmark():
    samples = []
    for _ in range(20):
        start = time.perf_counter()
        capture()
        samples.append((time.perf_counter() - start) * 1000)
    result = {'median': round(statistics.median(samples), 1), 'min': round(min(samples), 1),
              'max': round(max(samples), 1), 'count': len(samples), 'at': dt.datetime.now().isoformat(timespec='seconds')}
    atomic_json(STATE / 'benchmark.json', result)
    return result


def desktop_env():
    env = os.environ.copy()
    # systemd user manager already imports Niri's session environment on this host.
    if not env.get('WAYLAND_DISPLAY'):
        for proc in Path('/proc').iterdir():
            try:
                if proc.stat().st_uid != os.getuid() or (proc/'comm').read_text().strip() != 'niri':
                    continue
                values = dict(x.split(b'=', 1) for x in (proc/'environ').read_bytes().split(b'\0') if b'=' in x)
                for key in ('DISPLAY', 'WAYLAND_DISPLAY', 'NIRI_SOCKET', 'DBUS_SESSION_BUS_ADDRESS', 'XDG_RUNTIME_DIR'):
                    if key.encode() in values:
                        env[key] = os.fsdecode(values[key.encode()])
            except OSError:
                continue
    return env


def launch(command, name, env=None):
    log = (STATE / f'{name}.log').open('a')
    p = subprocess.Popen(command, env=env or desktop_env(), stdout=log, stderr=subprocess.STDOUT,
                         start_new_session=True, cwd=HOME)
    log.close()
    return p.pid


def game_start(focus=True):
    # Avoid invoking the launcher again: its second-instance behavior changes focus.
    window = next((w for w in niri_windows() if w.get('app_id') == 'gamescope' and 'Weston Compositor' in w.get('title', '')), None)
    if window:
        if focus:
            run(['niri', 'msg', 'action', 'focus-window', '--id', str(window['id'])])
        return {'message': 'Waydroid HUD 已运行'}
    launcher = Path(settings()['hud_launcher'])
    if not launcher.is_file():
        raise RuntimeError(f'找不到启动器：{launcher}')
    return {'message': '正在启动明日方舟与 HUD', 'pid': launch([str(launcher)], 'waydroid')}


def maa_start(focus=True):
    if maa_running():
        window = next((w for w in niri_windows() if w.get('app_id', '').lower() == 'maa.exe'), None)
        if focus and window:
            run(['niri', 'msg', 'action', 'focus-window', '--id', str(window['id'])])
        return {'message': 'MAA 已运行'}
    game_id = int(settings()['lutris_game_id'])
    if game_id <= 0:
        raise RuntimeError('请设置有效的 lutris_game_id')
    return {'message': '正在通过 Lutris 启动 MAA', 'pid': launch(['lutris', f'lutris:rungameid/{game_id}'], 'maa')}


def warm_touch(adb, address, env):
    """Warm Java/InputManager on cold boots without injecting any input."""
    source = Path(settings()['maa_dir']) / 'resource/minitouch/maatouch/minitouch'
    remote = '/data/local/tmp/arknights-toolbox-touch-warmup.jar'
    base = ['wine', str(adb), '-s', address]
    run(base + ['push', str(source), remote], timeout=20, env=env)
    p = subprocess.Popen(base + ['shell', f'CLASSPATH={remote} app_process /data/local/tmp com.shxyke.MaaTouch.App'],
                         env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    output = bytearray()
    try:
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            readable, _, _ = select.select([p.stdout], [], [], .2)
            if readable:
                chunk = os.read(p.stdout.fileno(), 2048)
                if not chunk:
                    break
                output.extend(chunk)
                if b'$' in output:
                    return True
            if p.poll() is not None:
                break
        return False
    finally:
        try:
            p.stdin.close()
        except OSError:
            pass
        try:
            p.wait(timeout=2)
        except subprocess.TimeoutExpired:
            p.terminate()
        run(base + ['shell', 'rm', '-f', remote], timeout=10, check=False, env=env)


def close_maa():
    if not maa_running():
        return
    matches = [w for w in niri_windows() if w.get('app_id', '').lower() == 'maa.exe']
    if not matches:
        raise RuntimeError('MAA 正在后台运行，请先退出 MAA 再切换截图模式')
    for w in matches:
        run(['niri', 'msg', 'action', 'close-window', '--id', str(w['id'])])
    for _ in range(80):
        if not maa_running():
            return
        time.sleep(.1)
    raise RuntimeError('MAA 尚未退出；请处理它的关闭提示后重试')


def capture_mode(enabled):
    was_running = maa_running()
    close_maa()
    path, config = maa_config()
    backups = STATE / 'backups'
    backups.mkdir(exist_ok=True)
    shutil.copy2(path, backups / f'gui-{time.time_ns()}.json')
    c = config['Configurations'][config.get('Current', 'Default')]['Gui']['ConnectSettings']
    extra = c.setdefault('Extras', {}).setdefault('MuMuEmulator12', {})
    extra.update({'IsEnabled': enabled, 'EmulatorPath': 'Z:' + settings()['sdk_dir'].replace('/', '\\'),
                  'EnableBridgeConnection': True, 'InstanceIndex': 0})
    c['Config'] = 'MuMuEmulator12' if enabled else 'General'
    c['TouchMode'] = 'MaaTouch'
    c['EnableAdbLite'] = True
    atomic_json(path, config)
    if enabled:
        run(['systemctl', '--user', 'start', 'waydroid-mumu-capture.service'])
    if was_running:
        maa_start()
    return {'message': '已启用 MuMu 接口截图增强' if enabled else '已切回普通 ADB 截图'}


def scheduler_units(config, prefs, executable=None):
    executable = executable or str(Path(__file__).resolve())
    result = {}
    for item in slots(config, int(prefs['lead_minutes'])):
        if not item['enabled']:
            continue
        base = f'{PREFIX}slot-{item["id"]}'
        hour, minute = item['prepare'].split(':')
        result[base + '.service'] = (
            '[Unit]\nDescription=Prepare Waydroid and MAA before ' + item['time'] + '\n'
            '\n[Service]\nType=oneshot\nTimeoutStartSec=240\n'
            'ExecStart=/usr/bin/python3 "' + executable.replace('"', '\\"') + '" prepare --slot ' + str(item['id']) + '\n')
        result[base + '.timer'] = (
            '[Unit]\nDescription=Waydroid/MAA preparation for ' + item['time'] + '\n'
            '\n[Timer]\nOnCalendar=*-*-* ' + hour + ':' + minute + ':00\n'
            'AccuracySec=1s\nPersistent=false\nUnit=' + base + '.service\n'
            '\n[Install]\nWantedBy=timers.target\n')
    return result


def sync_schedule():
    prefs = settings()
    _, config = maa_config()  # Invalid/partial files must leave existing units intact.
    desired = scheduler_units(config, prefs) if prefs['scheduler_enabled'] else {}
    UNITS.mkdir(parents=True, exist_ok=True)
    with (STATE / 'sync.lock').open('w') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        changed = []
        removed = False
        for old in UNITS.glob(f'{PREFIX}slot-*.*'):
            if not re.fullmatch(r'arknights-toolbox-slot-[0-7]\.(timer|service)', old.name):
                continue
            if old.name not in desired:
                if old.suffix == '.timer':
                    run(['systemctl', '--user', 'disable', '--now', old.name], check=False)
                old.unlink()
                removed = True
        for name, content in desired.items():
            path = UNITS / name
            if not path.exists() or path.read_text() != content:
                path.write_text(content)
                changed.append(name)
        if changed or removed:
            run(['systemctl', '--user', 'daemon-reload'])
        timers = [n for n in desired if n.endswith('.timer')]
        for timer in timers:
            if run(['systemctl', '--user', 'is-enabled', timer], check=False) != 'enabled':
                run(['systemctl', '--user', 'enable', '--now', timer])
            if timer in changed:
                run(['systemctl', '--user', 'restart', timer])
            elif run(['systemctl', '--user', 'is-active', timer], check=False) != 'active':
                run(['systemctl', '--user', 'start', timer])
    return {'message': f'已同步 {len(timers)} 个 Linux 定时器', 'timers': timers}


def scheduler_enable(enabled):
    prefs = settings()
    prefs['scheduler_enabled'] = enabled
    atomic_json(CONFIG, prefs)
    backend = str(Path(__file__).resolve()).replace('"', '\\"')
    path, _ = maa_config()
    UNITS.mkdir(parents=True, exist_ok=True)
    (UNITS / f'{PREFIX}sync.service').write_text(
        '[Unit]\nDescription=Sync Linux timers from MAA settings\n'
        '\n[Service]\nType=oneshot\nExecStart=/usr/bin/python3 "' + backend + '" sync\n'
        '\n[Install]\nWantedBy=default.target\n')
    (UNITS / f'{PREFIX}sync.path').write_text(
        '[Unit]\nDescription=Watch MAA schedule changes\n\n[Path]\nPathChanged=' + str(path) + '\n'
        'Unit=' + PREFIX + 'sync.service\n\n[Install]\nWantedBy=default.target\n')
    run(['systemctl', '--user', 'daemon-reload'])
    verb = 'enable' if enabled else 'disable'
    run(['systemctl', '--user', verb, '--now', f'{PREFIX}sync.path'])
    # The oneshot is enabled for login, but starting it here would race our own sync.
    run(['systemctl', '--user', verb, f'{PREFIX}sync.service'])
    return sync_schedule()


def prepare(slot_id, dry_run=False):
    prefs = settings()
    _, config = maa_config()
    item = next((x for x in slots(config, int(prefs['lead_minutes'])) if x['id'] == slot_id), None)
    if not item or not item['enabled']:
        return {'message': '该 MAA 定时已禁用，跳过准备'}
    if dry_run:
        return {'message': '仅验证准备流程，不启动程序或游戏任务', 'slot': item,
                'steps': ['start HUD if needed', 'wait for Android boot', 'connect ADB', 'start MAA if needed']}
    with (STATE / 'prepare.lock').open('w') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return {'message': '另一个定时准备正在进行'}
        status = waydroid_status()
        if status.get('Session') == 'RUNNING' and status.get('Wayland display') != 'waydroid-mangoapp':
            raise RuntimeError('Waydroid 在其他显示会话中运行，未自动中断它')
        game_start(focus=False)
        deadline = time.monotonic() + 200
        while time.monotonic() < deadline:
            status = waydroid_status()
            if status.get('Container') == 'RUNNING' and status.get('IP address') not in (None, '', 'UNKNOWN'):
                boot = run(['waydroid', 'prop', 'get', 'sys.boot_completed'], timeout=5, check=False)
                if boot == '1':
                    break
            time.sleep(2)
        else:
            raise RuntimeError('Waydroid 启动超时，请查看启动日志')
        address = status['IP address'] + ':5555'
        env = desktop_env()
        env.update(WINEPREFIX=prefs['wine_prefix'], WINEDEBUG='-all')
        adb = Path(prefs['maa_dir']) / 'platform-tools/adb.exe'
        run(['wine', str(adb), 'connect', address], timeout=20, env=env)
        # Preserve an active MAA configuration; only update its address while stopped.
        if not maa_running():
            touch_ready = warm_touch(adb, address, env)
            path, config = maa_config()
            for values in config.get('Configurations', {}).values():
                connection = values.get('Gui', {}).get('ConnectSettings')
                if connection is not None:
                    connection['Address'] = address
            atomic_json(path, config)
            maa_start(focus=False)
        else:
            touch_ready = True
        result = {'message': f'已为 {item["time"]} 准备好 Waydroid 与 MAA；任务由 MAA 到点执行', 'slot': item}
        if not touch_ready:
            result['warning'] = 'MaaTouch 预热未完成，请检查 MAA 连接测试'
        atomic_json(STATE / 'last-prepare.json', dict(result, at=dt.datetime.now().isoformat(timespec='seconds')))
        return result


def status():
    prefs = settings()
    path, config = maa_config()
    current = config.get('Current', 'Default')
    c = config['Configurations'][current]['Gui']['ConnectSettings']
    try:
        capture(False)
        ready = True
    except (OSError, RuntimeError):
        ready = False
    result = {'maa_running': maa_running(), 'waydroid': waydroid_status(), 'capture_ready': ready,
              'capture_enabled': c.get('Extras', {}).get('MuMuEmulator12', {}).get('IsEnabled', False),
              'scheduler_enabled': prefs['scheduler_enabled'] and run(['systemctl', '--user', 'is-enabled', PREFIX+'sync.path'], check=False) == 'enabled', 'lead_minutes': prefs['lead_minutes'],
              'slots': slots(config, int(prefs['lead_minutes'])), 'maa_config': str(path),
              'address': c.get('Address', ''), 'configuration': current, 'touch': c.get('TouchMode', ''),
              'timers': run(['systemctl', '--user', 'list-timers', '--all', PREFIX+'slot-*.timer', '--no-pager'], check=False)}
    for key, name in [('benchmark', 'benchmark.json'), ('last_prepare', 'last-prepare.json')]:
        if (STATE/name).exists():
            result[key] = json.loads((STATE/name).read_text())
    log = Path(prefs['maa_dir']) / 'debug/gui.log'
    if log.exists():
        lines = log.read_text(errors='replace').splitlines()[-250:]
        matches = [x for x in lines if '截图耗时:' in x]
        result['maa_capture_result'] = matches[-1].split('截图耗时:', 1)[-1].strip() if matches else ''
    return result


def main():
    p = argparse.ArgumentParser()
    p.add_argument('action', choices=['status', 'preview', 'benchmark', 'game-start', 'maa-start',
                                     'game-stop', 'capture-on', 'capture-off', 'scheduler-on',
                                     'scheduler-off', 'sync', 'prepare', 'logs', 'config-folder'])
    p.add_argument('--slot', type=int, default=0)
    p.add_argument('--dry-run', action='store_true')
    a = p.parse_args()
    try:
        actions = {'status': status, 'preview': preview, 'benchmark': benchmark,
                   'game-start': game_start, 'maa-start': maa_start,
                   'capture-on': lambda: capture_mode(True), 'capture-off': lambda: capture_mode(False),
                   'scheduler-on': lambda: scheduler_enable(True), 'scheduler-off': lambda: scheduler_enable(False),
                   'sync': sync_schedule, 'prepare': lambda: prepare(a.slot, a.dry_run),
                   'game-stop': lambda: {'message': run(['waydroid', 'session', 'stop'], timeout=40) or 'Waydroid 已停止'},
                   'logs': lambda: {'text': run(['journalctl', '--user', '-u', PREFIX+'*', '-u', 'waydroid-mumu-capture.service', '-n', '60', '--no-pager'], check=False)},
                   'config-folder': lambda: {'pid': launch(['xdg-open', str(Path(settings()['maa_dir'])/'config')], 'folder')}}
        print(json.dumps({'ok': True, 'data': actions[a.action]()}, ensure_ascii=False))
    except Exception as e:
        print(json.dumps({'ok': False, 'error': str(e)}, ensure_ascii=False))
        sys.exit(1)


if __name__ == '__main__':
    main()
