/* Exercise the MuMu input exports the way MAA's MumuExtras path does.
 *
 * Before the port these returned -1 and MAA aborted every task with
 * "触控模式不可用"; the test fails if any of them still reports failure.
 * Run it against a live Waydroid session with the capture service up.
 */
#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#include <stdio.h>
#include <stdlib.h>
#include "protocol.h"

int main(int argc,char **argv) {
    if(argc!=2) {
        printf("usage: test-input.exe <path-to-external_renderer_ipc.dll>\n");
        return 2;
    }
    HMODULE dll=LoadLibraryA(argv[1]);
    if(!dll) { printf("LOAD_FAILED %u\n",(unsigned int)GetLastError()); return 3; }

    int (__cdecl *connect_fn)(const WCHAR*,int)=(void*)GetProcAddress(dll,"nemu_connect");
    int (__cdecl *touch_down)(int,int,int,int)=(void*)GetProcAddress(dll,"nemu_input_event_touch_down");
    int (__cdecl *touch_up)(int,int)=(void*)GetProcAddress(dll,"nemu_input_event_touch_up");
    int (__cdecl *key_down)(int,int,int)=(void*)GetProcAddress(dll,"nemu_input_event_key_down");
    int (__cdecl *key_up)(int,int,int)=(void*)GetProcAddress(dll,"nemu_input_event_key_up");
    if(!connect_fn || !touch_down || !touch_up || !key_down || !key_up) {
        printf("EXPORT_MISSING\n");
        return 4;
    }

    int handle=connect_fn(NULL,0);
    if(handle!=1) { printf("CONNECT_FAILED handle=%d\n",handle); return 5; }

    int failures=0;
    /* A tap in the middle of the 1280x720 screen: down then up. */
    if(touch_down(handle,0,640,360)) { printf("TOUCH_DOWN_FAILED\n"); failures++; }
    if(touch_up(handle,0)) { printf("TOUCH_UP_FAILED\n"); failures++; }

    /* Several taps in a row, which is what a task actually generates. */
    for(int i=0;i<5;i++) {
        int x=100+i*200, y=200+(i%2)*200;
        if(touch_down(handle,0,x,y)) { printf("TOUCH_DOWN_FAILED x=%d y=%d\n",x,y); failures++; }
        if(touch_up(handle,0)) { printf("TOUCH_UP_FAILED x=%d y=%d\n",x,y); failures++; }
    }

    /* Key events are used by MAA for the back button and ESC dialogs. */
    if(key_down(handle,0,4)) { printf("KEY_DOWN_FAILED\n"); failures++; }
    if(key_up(handle,0,4)) { printf("KEY_UP_FAILED\n"); failures++; }

    if(failures) { printf("INPUT_FAILED failures=%d\n",failures); return 6; }
    puts("INPUT_OK taps=6 keys=1");
    return 0;
}
