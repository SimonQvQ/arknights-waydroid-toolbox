#!/usr/bin/env python3
"""Install the optional capture/HUD integration for the current user only."""
from datetime import datetime
from pathlib import Path
import os
import shutil
import subprocess


def main():
    if os.getuid() == 0:
        raise SystemExit('请以普通桌面用户运行，不要 sudo。')
    root = Path(__file__).resolve().parents[1]
    home = Path.home()
    built = root / 'capture/build'
    files = {
        built / 'capture-server': home / '.local/share/waydroid-mumu-capture/capture-server',
        built / 'sdk/shell/sdk/external_renderer_ipc.dll': home / '.local/share/waydroid-mumu-capture/sdk/shell/sdk/external_renderer_ipc.dll',
        root / 'capture/run-server.py': home / '.local/share/waydroid-mumu-capture/run-server.py',
        root / 'hud/arknights-waydroid-hud': home / '.local/bin/arknights-waydroid-hud',
        root / 'hud/start-android': home / '.local/lib/waydroid-mangoapp/start-android',
        root / 'hud/bin/mangoapp': home / '.local/lib/waydroid-mangoapp/bin/mangoapp',
    }
    for source in files:
        if not source.is_file():
            raise SystemExit(f'缺少 {source}，请先运行 make -C capture。')
    unit = home / '.config/systemd/user/waydroid-mumu-capture.service'
    backup = home / '.local/state/arknights-toolbox/install-backups' / datetime.now().strftime('%Y%m%d-%H%M%S-%f')
    for destination in [*files.values(), unit]:
        if destination.exists():
            target = backup / destination.relative_to(home)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(destination, target)
    for source, destination in files.items():
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)
        destination.chmod(0o755)
    unit.parent.mkdir(parents=True, exist_ok=True)
    unit.write_text('''[Unit]
Description=Waydroid screenshot adapter for the MuMu capture API

[Service]
ExecStart=/usr/bin/python3 "%h/.local/share/waydroid-mumu-capture/run-server.py"
Restart=always
RestartSec=2
TimeoutStopSec=2
UMask=0077

[Install]
WantedBy=default.target
''')
    subprocess.run(['systemctl', '--user', 'daemon-reload'], check=True)
    subprocess.run(['systemctl', '--user', 'enable', '--now', unit.name], check=True)
    print('已安装当前用户的 HUD 启动器与截图服务。已有文件备份于：', backup)
    print('未启用定时或修改 MAA 配置；请先配置 MAA，再在工具箱中启用。')


if __name__ == '__main__':
    main()
