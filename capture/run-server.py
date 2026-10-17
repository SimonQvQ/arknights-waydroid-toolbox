#!/usr/bin/python3
"""Find this user's nested Weston; isolate X11 failures from MAA."""
import os
from pathlib import Path
import time

root=Path(__file__).resolve().parent
while True:
    for proc in Path('/proc').iterdir():
        if not proc.name.isdigit():
            continue
        try:
            if proc.stat().st_uid != os.getuid():
                continue
            args=(proc/'cmdline').read_bytes().split(b'\0')
            if not args or Path(os.fsdecode(args[0])).name != 'weston':
                continue
            if b'--socket=waydroid-mangoapp' not in args:
                continue
            env=dict(v.split(b'=',1) for v in (proc/'environ').read_bytes().split(b'\0') if b'=' in v)
            display=env.get(b'DISPLAY')
            if display:
                socket=Path(os.environ.get('XDG_RUNTIME_DIR',f'/run/user/{os.getuid()}'))/'waydroid-mumu-capture.sock'
                os.execv(str(root/'capture-server'),[str(root/'capture-server'),os.fsdecode(display),str(socket)])
        except (OSError,ValueError):
            continue
    time.sleep(2)
