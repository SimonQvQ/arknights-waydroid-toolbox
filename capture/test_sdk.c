#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#include <stdio.h>
#include <stdlib.h>
#include "protocol.h"
int main(int argc,char **argv) {
    if(argc!=2) ExitProcess(2);
    HMODULE dll=LoadLibraryA(argv[1]);
    if(!dll) { printf("LOAD_FAILED %u\n",(unsigned int)GetLastError()); fflush(stdout); ExitProcess(3); }
    int (__cdecl *connect_fn)(const WCHAR*,int)=(void*)GetProcAddress(dll,"nemu_connect");
    int (__cdecl *cap)(int,unsigned int,int,int*,int*,unsigned char*)=(void*)GetProcAddress(dll,"nemu_capture_display");
    if(!cap || !connect_fn) ExitProcess(4);
    int handle=connect_fn(NULL,0),w=0,h=0;
    if(handle!=1 || cap(handle,0,0,&w,&h,NULL) || w!=1280 || h!=720) ExitProcess(5);
    unsigned char *pixels=malloc(CAP_BYTES);
    if(!pixels) ExitProcess(6);
    if(cap(handle,0,8,&w,&h,pixels)==0) ExitProcess(7);
    LARGE_INTEGER freq,start,end; QueryPerformanceFrequency(&freq);
    for(int i=0;i<30;i++) {
        QueryPerformanceCounter(&start);
        if(cap(handle,0,CAP_BYTES,&w,&h,pixels)) ExitProcess(8);
        QueryPerformanceCounter(&end);
        printf("SDK_CAPTURE %.3f\n",(end.QuadPart-start.QuadPart)*1000.0/freq.QuadPart);
    }
    FILE *f=fopen("sdk-bottom-up.rgba","wb");
    if(f) { fwrite(pixels,1,CAP_BYTES,f); fclose(f); }
    puts("SDK_OK dimensions=1280x720 small_buffer_rejected=1"); fflush(stdout);
    free(pixels); ExitProcess(0);
}
