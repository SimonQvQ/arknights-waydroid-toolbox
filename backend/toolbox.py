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
import signal
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


def maa_running(root='/proc'):
    """True while MAA is up, including the Wine bootstrap phase.

    Wine reports the process as `wine` until MAA.exe itself is loaded, and on a
    cold prefix `wineboot --init` can hold that name for well over a minute, so
    comm alone is not enough. Callers that shell out to MAA names are filtered
    out by requiring a Wine-ish comm, which also stops this scan from matching
    the invoking shell's own command line.
    """
    for proc in Path(root).iterdir():
        if not proc.name.isdigit():
            continue
        try:
            if proc.stat().st_uid != os.getuid():
                continue
            comm = (proc / 'comm').read_text().strip()
            if comm.lower().startswith('maa.exe'):
                return True
            if not comm.lower().startswith('wine'):
                continue
            args = (proc / 'cmdline').read_bytes().split(b'\0')
            if any(arg.replace(b'\\', b'/').lower().endswith(b'/maa.exe') for arg in args):
                return True
        except OSError:
            pass
    return False


def display_stack(root='/proc'):
    """(pid, comm, argv) for this user's waydroid display processes."""
    found = []
    for proc in Path(root).iterdir():
        if not proc.name.isdigit():
            continue
        try:
            if proc.stat().st_uid != os.getuid():
                continue
            comm = (proc / 'comm').read_text().strip()
            # Comm is truncated to 15 chars and gamescope reports as
            # `gamescope-wl`, so match on the stem rather than the exact name.
            if not re.match(r'^(gamescope|weston|Xwayland)', comm):
                continue
            args = [x.decode('utf-8', 'replace') for x in
                    (proc / 'cmdline').read_bytes().split(b'\0') if x]
            found.append((int(proc.name), comm, args))
        except OSError:
            pass
    return found


def stale_display_pids(root='/proc'):
    """PIDs of HUD-spawned display processes that no longer form a valid stack.

    A gamescope left behind by a crashed Weston holds the HUD lock and leaves
    `waydroid-mangoapp` half-open, which makes the next launch die with
    "xdg_surface error 3". Anything HUD-owned without its partner is residue.
    """
    stack = display_stack(root)
    gamescopes = [pid for pid, comm, args in stack
                  if comm.startswith('gamescope')
                  and ('--mangoapp' in args or 'mangoapp' in ' '.join(args))]
    wests = [pid for pid, comm, args in stack
             if comm == 'weston' and '--socket=waydroid-mangoapp' in args]
    if not wests:
        return gamescopes          # orphaned gamescope: its Weston is gone
    if not gamescopes:
        return wests               # nested Weston outlived its parent
    return []


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
    # A detached Unix session still belongs to its parent's systemd cgroup.
    # Give GUI applications their own unit so a completed preparation oneshot
    # cannot kill Waydroid, Lutris or MAA through KillMode=control-group.
    environment = env or desktop_env()
    unit = f'{PREFIX}app-{name}-{time.time_ns()}.service'
    args = ['systemd-run', '--user', '--quiet', '--collect', '--service-type=exec',
            '--unit=' + unit, '--working-directory=' + str(HOME),
            '--property=ExitType=cgroup',
            '--property=StandardOutput=append:' + str(STATE / f'{name}.log'),
            '--property=StandardError=inherit']
    for key in ('PATH', 'DISPLAY', 'WAYLAND_DISPLAY', 'NIRI_SOCKET', 'XDG_RUNTIME_DIR',
                'DBUS_SESSION_BUS_ADDRESS', 'XAUTHORITY', 'XDG_SESSION_TYPE',
                'XDG_CURRENT_DESKTOP', 'XDG_SESSION_DESKTOP', 'PULSE_SERVER',
                'WINEPREFIX', 'WINEDEBUG'):
        if key in environment:
            args.append('--setenv=' + key + '=' + environment[key])
    run(args + ['--'] + command, timeout=15, env=environment)
    return unit


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
    return {'message': '正在启动明日方舟与 HUD', 'unit': launch([str(launcher)], 'waydroid')}


def maa_start(focus=True):
    if maa_running():
        window = next((w for w in niri_windows() if w.get('app_id', '').lower() == 'maa.exe'), None)
        if focus and window:
            run(['niri', 'msg', 'action', 'focus-window', '--id', str(window['id'])])
        return {'message': 'MAA 已运行'}
    game_id = int(settings()['lutris_game_id'])
    if game_id <= 0:
        raise RuntimeError('请设置有效的 lutris_game_id')
    return {'message': '正在通过 Lutris 启动 MAA', 'unit': launch(['lutris', f'lutris:rungameid/{game_id}'], 'maa')}


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


def close_maa(timeout=20.0):
    """Shut MAA down. Prefer a graceful window close, but never leave a
    headless MAA behind: an announcement or guide dialog can hide the main
    window entirely, which used to make this raise instead of closing."""
    if not maa_running():
        return
    matches = [w for w in niri_windows() if w.get('app_id', '').lower() == 'maa.exe']
    for w in matches:
        run(['niri', 'msg', 'action', 'close-window', '--id', str(w['id'])], check=False)
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not maa_running():
            return
        time.sleep(.2)
    # Modal dialogs such as MAA's announcement viewer survive a window close.
    run(['pkill', '-TERM', '-f', 'MAA[.]exe'], check=False)
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        if not maa_running():
            return
        time.sleep(.2)
    run(['pkill', '-KILL', '-f', 'MAA[.]exe'], check=False)
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        if not maa_running():
            return
        time.sleep(.2)
    raise RuntimeError('MAA 未能在超时前退出')


def notify(title, body, urgency='normal'):
    """Desktop notification; failure must never break a scheduled run."""
    run(['notify-send', '-a', '明日方舟 MAA', '-u', urgency,
         '-i', 'applications-games', title, body], timeout=10, check=False)


def remind(slot_id):
    prefs = settings()
    _, config = maa_config()
    item = next((x for x in slots(config, int(prefs['lead_minutes'])) if x['id'] == slot_id), None)
    if not item or not item['enabled']:
        return {'message': '该 MAA 定时已禁用，跳过提醒'}
    notify('MAA 任务 1 分钟后开始',
           f'定时 {item["time"]}，即将自动启动明日方舟与 MAA')
    return {'message': f'已提醒：{item["time"]} 的任务将在 1 分钟后开始'}


def finish_session(reason='任务完成'):
    """Close MAA, then the Waydroid session, once the task chain is over."""
    maa_closed, waydroid_closed = False, False
    try:
        close_maa(timeout=30)
        maa_closed = True
    except (RuntimeError, subprocess.TimeoutExpired):
        pass
    run(['waydroid', 'session', 'stop'], timeout=90, check=False)
    waydroid_closed = waydroid_status().get('Session') not in ('RUNNING', 'FROZEN')
    result = {'message': f'{reason}：已关闭 Waydroid 与 MAA', 'maa_closed': maa_closed,
              'waydroid_closed': waydroid_closed}
    notify('明日方舟任务已结束',
           f'{reason}\nWaydroid 会话已关闭' + ('，MAA 已退出' if maa_closed else '，MAA 仍在运行'))
    try:
        atomic_json(STATE / 'last-finish.json',
                    dict(result, reason=reason, at=dt.datetime.now().isoformat(timespec='seconds')))
    except OSError:
        pass
    return result


# MAA ends a queued run with "Idle: true to true (called from ProcTaskChainMsg)".
# A stop requested mid-run reports SetStopped instead, so this only fires when
# the queue actually drained.
TASK_START = re.compile(r'BeginRun: owner="TaskQueue" \(called from LinkStartWithTasks\)')
TASK_END = re.compile(r'Idle: true to true \(called from ProcTaskChainMsg\)')


def scan_log(lines, running):
    """Fold log lines into the run state; report whether the queue just drained.

    Split out of watch() so the state machine can be tested without a daemon.
    """
    finished = False
    for line in lines:
        if TASK_START.search(line):
            running = True
        elif TASK_END.search(line) and running:
            running = False
            finished = True
    return running, finished


def watch(poll=2.0):
    """Follow MAA's log and close the session when a scheduled run completes."""
    log = Path(settings()['maa_dir']) / 'debug/gui.log'
    running, stream, inode = False, None, 0
    try:
        while True:
            try:
                info = log.stat()
            except OSError:
                time.sleep(poll)
                continue
            if stream is None or info.st_ino != inode:
                if stream is not None:
                    stream.close()
                # Start at EOF so replays of old runs never close a live session.
                stream = log.open('r', errors='replace')
                stream.seek(0, os.SEEK_END)
                inode = info.st_ino
            if stream.tell() > info.st_size:
                stream.seek(0)
            running, finished = scan_log(stream.read().splitlines(), running)
            if finished:
                finish_session('MAA 任务完成')
            time.sleep(poll)
    finally:
        if stream is not None:
            stream.close()


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
            '\n[Service]\nType=oneshot\nTimeoutStartSec=480\n'
            'ExecStart=/usr/bin/python3 "' + executable.replace('"', '\\"') + '" prepare --slot ' + str(item['id']) + '\n')
        result[base + '.timer'] = (
            '[Unit]\nDescription=Waydroid/MAA preparation for ' + item['time'] + '\n'
            '\n[Timer]\nOnCalendar=*-*-* ' + hour + ':' + minute + ':00\n'
            'AccuracySec=1s\nPersistent=false\nUnit=' + base + '.service\n'
            '\n[Install]\nWantedBy=timers.target\n')
        # One-minute warning, counted back from the slot's start time (not from
        # the earlier preparation time).
        start_hour, start_minute = (int(x) for x in item['time'].split(':'))
        remind_at = (dt.datetime.combine(dt.date.today(), dt.time(start_hour, start_minute))
                     - dt.timedelta(minutes=1))
        result[base + '-remind.service'] = (
            '[Unit]\nDescription=Remind one minute before ' + item['time'] + '\n'
            '\n[Service]\nType=oneshot\n'
            'ExecStart=/usr/bin/python3 "' + executable.replace('"', '\\"') + '" remind --slot ' + str(item['id']) + '\n')
        result[base + '-remind.timer'] = (
            '[Unit]\nDescription=One-minute warning for ' + item['time'] + '\n'
            '\n[Timer]\nOnCalendar=*-*-* ' + remind_at.strftime('%H:%M') + ':00\n'
            'AccuracySec=1s\nPersistent=false\nUnit=' + base + '-remind.service\n'
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
            if not re.fullmatch(r'arknights-toolbox-slot-[0-7](-remind)?\.(timer|service)', old.name):
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
    # Long-lived follower that tears the session down once MAA's queue drains.
    (UNITS / f'{PREFIX}watcher.service').write_text(
        '[Unit]\nDescription=Close Waydroid and MAA when a scheduled task finishes\n'
        'After=graphical-session.target\n'
        '\n[Service]\nType=simple\n'
        'ExecStart=/usr/bin/python3 "' + backend + '" watch\n'
        'Restart=always\nRestartSec=5\n'
        '\n[Install]\nWantedBy=default.target\n')
    run(['systemctl', '--user', 'daemon-reload'])
    verb = 'enable' if enabled else 'disable'
    run(['systemctl', '--user', verb, '--now', f'{PREFIX}sync.path'])
    run(['systemctl', '--user', verb, '--now', f'{PREFIX}watcher.service'])
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
        # A running Android compositor cannot be moved between Wayland sockets,
        # and the MuMu capture API only works when Weston owns the session. A
        # stale session on Niri's socket (or one left behind by a previous
        # manual `waydroid session start`) therefore has to be recycled instead
        # of failing the whole scheduled preparation.
        if status.get('Session') in ('RUNNING', 'FROZEN') and status.get('Wayland display') != 'waydroid-mangoapp':
            run(['waydroid', 'session', 'stop'], timeout=60, check=False)
            deadline = time.monotonic() + 30
            while time.monotonic() < deadline:
                if waydroid_status().get('Session') not in ('RUNNING', 'FROZEN'):
                    break
                time.sleep(1)
            else:
                raise RuntimeError('无法停止占用其他显示会话的 Waydroid，请手动关闭后重试')
        # A gamescope that outlived its Weston keeps the HUD's start lock and
        # leaves the nested socket in a half-open state; the next launch then
        # dies with "xdg_surface error 3: must ack the initial configure".
        # Clearing it before launching is what makes the retry below reliable.
        def clear_stale_stack():
            for pid in stale_display_pids():
                for sig in (signal.SIGTERM, signal.SIGKILL):
                    try:
                        os.kill(pid, sig)
                    except OSError:
                        break
                    time.sleep(1)
                    if not Path(f'/proc/{pid}').exists():
                        break
            time.sleep(1)

        clear_stale_stack()
        started = False
        for attempt in (1, 2):
            game_start(focus=False)
            deadline = time.monotonic() + 200
            while time.monotonic() < deadline:
                try:
                    status = waydroid_status()
                    if status.get('Container') == 'RUNNING' and status.get('IP address') not in (None, '', 'UNKNOWN'):
                        boot = run(['waydroid', 'prop', 'get', 'sys.boot_completed'], timeout=5, check=False)
                        if boot == '1':
                            started = True
                            break
                except subprocess.TimeoutExpired:
                    # Binder/property calls can stall briefly during Android boot.
                    pass
                time.sleep(2)
            if started:
                break
            if attempt == 1:
                # Weston died mid-session (its socket stays half-open), so the
                # second attempt needs the residue cleaned, not just a relaunch.
                run(['waydroid', 'session', 'stop'], timeout=60, check=False)
                clear_stale_stack()
        if not started:
            raise RuntimeError('Waydroid 启动超时，请查看启动日志')
        address = status['IP address'] + ':5555'
        env = desktop_env()
        env.update(WINEPREFIX=prefs['wine_prefix'], WINEDEBUG='-all',
                   WINEESYNC='0', WINEFSYNC='0', WINEDLLOVERRIDES='winemenubuilder=')
        adb = Path(prefs['maa_dir']) / 'platform-tools/adb.exe'
        cold_maa = not maa_running()
        # Repair the connection profile on every run, not just cold starts. MAA
        # rewrites EnableBridgeConnection and TouchMode while running (its
        # first-run guide and the MuMu keep-alive probe both do this), and a
        # stale profile is exactly what turns into "触控模式不可用".
        #
        # MumuExtras is correct now that the shim forwards input as well as
        # capture: nemu_input_event_touch_down/up reach a persistent `adb shell`
        # and inject real MotionEvents. It used to abort every task ~20 s in
        # because the input exports returned failure.
        path, config = maa_config()
        changed = False
        for values in config.get('Configurations', {}).values():
            connection = values.get('Gui', {}).get('ConnectSettings')
            if connection is None:
                continue
            extra = connection.setdefault('Extras', {}).setdefault('MuMuEmulator12', {})
            for holder, fields in (
                    (connection, {'Address': address, 'Config': 'MuMuEmulator12',
                                  'TouchMode': 'MumuExtras', 'EnableAdbLite': True}),
                    (extra, {'IsEnabled': True, 'EnableBridgeConnection': True,
                             'EmulatorPath': 'Z:' + prefs['sdk_dir'].replace('/', '\\'),
                             'InstanceIndex': 0})):
                for key, value in fields.items():
                    if holder.get(key) != value:
                        holder[key] = value
                        changed = True
        if changed:
            atomic_json(path, config)
        # Bootstrap Wine in MAA's independent unit, not in this short-lived
        # preparation cgroup (which would take Wine/ADB down when it finishes).
        if cold_maa:
            maa_start(focus=False)
            # A cold Wine prefix runs `wineboot --init` first, which has been
            # measured at 100+ s on this machine; the old 60 s cap aborted the
            # whole preparation while MAA was still legitimately starting.
            deadline = time.monotonic() + 240
            while not maa_running():
                if time.monotonic() >= deadline:
                    raise RuntimeError('MAA 启动超时，请查看 maa.log')
                time.sleep(2)
        # Android may report boot_completed before adbd finishes reconnecting
        # a previously running Windows server, especially after a vendor change.
        deadline = time.monotonic() + 120
        adb_error = 'device 尚未就绪'
        while True:
            try:
                run(['wine', str(adb), 'connect', address], timeout=20, env=env)
                device = run(['wine', str(adb), '-s', address, 'get-state'], timeout=10, env=env)
                if device == 'device':
                    break
                adb_error = device or 'ADB 返回空状态'
            except (subprocess.TimeoutExpired, RuntimeError) as error:
                adb_error = str(error)
            if time.monotonic() >= deadline:
                raise RuntimeError('ADB 未就绪或尚未授权：' + adb_error)
            time.sleep(2)
        touch_ready = warm_touch(adb, address, env) if cold_maa else True
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
                                     'scheduler-off', 'sync', 'prepare', 'remind', 'watch',
                                     'logs', 'config-folder'])
    p.add_argument('--slot', type=int, default=0)
    p.add_argument('--dry-run', action='store_true')
    a = p.parse_args()
    if a.action == 'watch':
        # Long-running: owns the session teardown for the whole login session.
        try:
            watch()
        except KeyboardInterrupt:
            pass
        return
    try:
        actions = {'status': status, 'preview': preview, 'benchmark': benchmark,
                   'game-start': game_start, 'maa-start': maa_start,
                   'capture-on': lambda: capture_mode(True), 'capture-off': lambda: capture_mode(False),
                   'scheduler-on': lambda: scheduler_enable(True), 'scheduler-off': lambda: scheduler_enable(False),
                   'sync': sync_schedule, 'prepare': lambda: prepare(a.slot, a.dry_run),
                   'remind': lambda: remind(a.slot),
                   'game-stop': lambda: {'message': run(['waydroid', 'session', 'stop'], timeout=40) or 'Waydroid 已停止'},
                   'logs': lambda: {'text': run(['journalctl', '--user', '-u', PREFIX+'*', '-u', 'waydroid-mumu-capture.service', '-n', '60', '--no-pager'], check=False)},
                   'config-folder': lambda: {'unit': launch(['xdg-open', str(Path(settings()['maa_dir'])/'config')], 'folder')}}
        print(json.dumps({'ok': True, 'data': actions[a.action]()}, ensure_ascii=False))
    except Exception as e:
        print(json.dumps({'ok': False, 'error': str(e)}, ensure_ascii=False))
        sys.exit(1)


if __name__ == '__main__':
    main()
