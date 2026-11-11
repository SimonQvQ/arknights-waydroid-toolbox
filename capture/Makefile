CC = gcc
CFLAGS = -O3 -Wall -Wextra -Werror

all: build/capture-server build/sdk/shell/sdk/external_renderer_ipc.dll build/sdk/nx_main/MuMuManager.exe

build/capture-server: capture_server.c protocol.h
	mkdir -p build
	$(CC) $(CFLAGS) -o $@ capture_server.c -lX11

build/sdk/shell/sdk/external_renderer_ipc.dll: external_renderer_ipc.c external_renderer_ipc.spec protocol.h
	mkdir -p build/sdk/shell/sdk
	winegcc -m64 $(CFLAGS) -shared -o $@ external_renderer_ipc.c external_renderer_ipc.spec
	cp $@.so $@

# MAA probes `nx_main/MuMuManager.exe version` before enabling MuMu input, and
# falls back to minitouch (which cannot find a touch device in Waydroid) when
# the binary is missing.
#
# This must be a real PE executable, not winegcc's `#!/bin/sh` launcher: MAA
# spawns it with Windows CreateProcess, which cannot run a shell script.
MINGW = x86_64-w64-mingw32-gcc
build/sdk/nx_main/MuMuManager.exe: mumumanager_stub.c
	mkdir -p build/sdk/nx_main
	$(MINGW) -O2 -Wall -Wextra -Werror -o $@ mumumanager_stub.c -static -s

.PHONY: all
