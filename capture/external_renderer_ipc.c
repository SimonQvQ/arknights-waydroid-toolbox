#define _GNU_SOURCE
#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#include <sys/socket.h>
#include <sys/un.h>
#include <sys/time.h>
#include <unistd.h>
#include <stdlib.h>
#include <stdio.h>
#include <string.h>
#include <errno.h>
#include "protocol.h"

static int read_all(int fd,void *data,size_t n) {
    char *p=data;
    while(n) {
        ssize_t k=recv(fd,p,n,0);
        if(k<0 && errno==EINTR) continue;
        if(k<=0) return -1;
        p+=k; n-=k;
    }
    return 0;
}
static int request(char command,CapHeader *h,unsigned char *pixels,int capacity) {
    int fd=socket(AF_UNIX,SOCK_STREAM|SOCK_CLOEXEC,0); if(fd<0) return -1;
    struct timeval timeout={.tv_sec=1};
    setsockopt(fd,SOL_SOCKET,SO_SNDTIMEO,&timeout,sizeof(timeout));
    setsockopt(fd,SOL_SOCKET,SO_RCVTIMEO,&timeout,sizeof(timeout));
    struct sockaddr_un addr={.sun_family=AF_UNIX};
    snprintf(addr.sun_path,sizeof(addr.sun_path),"/run/user/%lu/waydroid-mumu-capture.sock",(unsigned long)getuid());
    int ok=connect(fd,(struct sockaddr*)&addr,sizeof(addr))==0 &&
        send(fd,&command,1,MSG_NOSIGNAL)==1 && read_all(fd,h,sizeof(*h))==0 &&
        h->magic==CAP_MAGIC && h->status==0 && h->width==CAP_WIDTH && h->height==CAP_HEIGHT;
    if(ok && command=='C') ok=h->bytes==CAP_BYTES && pixels && capacity>=(int)CAP_BYTES &&
        read_all(fd,pixels,CAP_BYTES)==0;
    close(fd); return ok?0:-1;
}
int __cdecl nemu_connect(const WCHAR *path,int index) {
    (void)path; CapHeader h;
    return index==0 && !request('I',&h,NULL,0)?1:0;
}
void __cdecl nemu_disconnect(int handle) { (void)handle; }
int __cdecl nemu_get_display_id(int handle,const char *package,int app_index) {
    (void)package; return handle==1 && app_index==0?0:-1;
}
int __cdecl nemu_capture_display(int handle,unsigned int display,int size,int *width,int *height,unsigned char *pixels) {
    if(handle!=1 || display!=0 || !width || !height || size<0) return -1;
    CapHeader h;
    if(request(size==0?'I':'C',&h,pixels,size)) return -1;
    *width=h.width; *height=h.height; return 0;
}
/* Screenshot-only adapter. MAA must keep using MaaTouch for all input. */
int __cdecl nemu_input_text(int handle,const char *text) { (void)handle; (void)text; return -1; }
int __cdecl nemu_input_event_touch_down(int handle,int display,int x,int y) {
    (void)handle; (void)display; (void)x; (void)y; return -1;
}
int __cdecl nemu_input_event_touch_up(int handle,int display) { (void)handle; (void)display; return -1; }
int __cdecl nemu_input_event_key_down(int handle,int display,int key) {
    (void)handle; (void)display; (void)key; return -1;
}
int __cdecl nemu_input_event_key_up(int handle,int display,int key) {
    (void)handle; (void)display; (void)key; return -1;
}
