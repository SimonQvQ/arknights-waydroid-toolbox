#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
typedef unsigned char AsstBool;
typedef unsigned long long AsstSize;
typedef void *AsstHandle;
typedef void (__stdcall *AsstApiCallback)(int,const char*,void*);
AsstBool __stdcall AsstSetUserDir(const char*);
AsstBool __stdcall AsstLoadResource(const char*);
AsstHandle __stdcall AsstCreateEx(AsstApiCallback,void*);
void __stdcall AsstDestroy(AsstHandle);
AsstBool __stdcall AsstSetInstanceOption(AsstHandle,int,const char*);
void __stdcall AsstSetConnectionExtras(const char*,const char*);
AsstBool __stdcall AsstConnect(AsstHandle,const char*,const char*,const char*);
int __stdcall AsstAsyncScreencap(AsstHandle,AsstBool);
AsstSize __stdcall AsstGetImage(AsstHandle,void*,AsstSize);
#define LOAD(name) __typeof__(&name) name##_fn=(void*)GetProcAddress(dll,#name); if(!name##_fn) ExitProcess(10)
static int found_fast;
static void __stdcall callback(int msg,const char *json,void *arg) {
    (void)msg; (void)arg;
    if(strstr(json,"FastestWayToScreencap")) {
        printf("FASTEST %s\n",json);
        if(strstr(json,"MumuExtras")) found_fast=1;
    }
    if(strstr(json,"\"what\":\"Screencap\"")) printf("CAPTURE %s\n",json);
    fflush(stdout);
}
int main(int argc,char **argv) {
    if(argc!=5) ExitProcess(2);
    SetDllDirectoryA(argv[1]);
    char path[2048]; snprintf(path,sizeof(path),"%s\\MaaCore.dll",argv[1]);
    HMODULE dll=LoadLibraryA(path);
    if(!dll) { printf("CORE_LOAD_FAILED %u\n",(unsigned int)GetLastError()); fflush(stdout); ExitProcess(3); }
    LOAD(AsstSetUserDir); LOAD(AsstLoadResource); LOAD(AsstCreateEx); LOAD(AsstDestroy);
    LOAD(AsstSetInstanceOption); LOAD(AsstSetConnectionExtras); LOAD(AsstConnect);
    LOAD(AsstAsyncScreencap); LOAD(AsstGetImage);
    if(!AsstSetUserDir_fn(argv[3]) || !AsstLoadResource_fn(argv[1])) ExitProcess(4);
    AsstSetConnectionExtras_fn("MuMuEmulator12",argv[4]);
    AsstHandle h=AsstCreateEx_fn(callback,NULL);
    if(!h) ExitProcess(5);
    AsstSetInstanceOption_fn(h,2,"maatouch"); AsstSetInstanceOption_fn(h,4,"1");
    snprintf(path,sizeof(path),"%s\\platform-tools\\adb.exe",argv[1]);
    if(!AsstConnect_fn(h,path,argv[2],"MuMuEmulator12")) ExitProcess(6);
    for(int i=0;i<20;i++) AsstAsyncScreencap_fn(h,1);
    unsigned char *png=malloc(8*1024*1024);
    AsstSize size=AsstGetImage_fn(h,png,8*1024*1024);
    if(size>0 && size<8*1024*1024 && !memcmp(png,"\x89PNG",4)) {
        FILE *f=fopen("core-screenshot.png","wb");
        if(f) { fwrite(png,1,size,f); fclose(f); }
        puts("CORE_IMAGE_OK");
    } else ExitProcess(7);
    free(png); AsstDestroy_fn(h); Sleep(100);
    printf("CORE_TEST_OK mumu_fastest=%d tasks_started=0\n",found_fast); fflush(stdout);
    ExitProcess(found_fast?0:8);
}
