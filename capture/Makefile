CC = gcc
CFLAGS = -O3 -Wall -Wextra -Werror

all: build/capture-server build/sdk/shell/sdk/external_renderer_ipc.dll

build/capture-server: capture_server.c protocol.h
	mkdir -p build
	$(CC) $(CFLAGS) -o $@ capture_server.c -lX11

build/sdk/shell/sdk/external_renderer_ipc.dll: external_renderer_ipc.c external_renderer_ipc.spec protocol.h
	mkdir -p build/sdk/shell/sdk
	winegcc -m64 $(CFLAGS) -shared -o $@ external_renderer_ipc.c external_renderer_ipc.spec
	cp $@.so $@

.PHONY: all
