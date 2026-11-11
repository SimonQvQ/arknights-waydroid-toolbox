#define _GNU_SOURCE
#include <X11/Xlib.h>
#include <X11/Xutil.h>
#include <sys/socket.h>
#include <sys/un.h>
#include <sys/file.h>
#include <sys/stat.h>
#include <sys/time.h>
#include <fcntl.h>
#include <unistd.h>
#include <signal.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include <errno.h>
#include "protocol.h"

static int xerror;
static uint64_t sequence;
static int trap(Display *d,XErrorEvent *e) { (void)d; xerror=e->error_code; return 0; }
static uint64_t now_ns(void) {
    struct timespec ts; clock_gettime(CLOCK_MONOTONIC,&ts);
    return (uint64_t)ts.tv_sec*1000000000ull+ts.tv_nsec;
}
static Window find_window(Display *d,Window parent,int depth) {
    XClassHint cls={0}; XWindowAttributes a; int matches=0;
    if(XGetClassHint(d,parent,&cls)) {
        matches=cls.res_class && !strcmp(cls.res_class,"Weston Compositor");
        if(cls.res_class) XFree(cls.res_class);
        if(cls.res_name) XFree(cls.res_name);
    }
    if(matches && XGetWindowAttributes(d,parent,&a) && a.map_state==IsViewable &&
       a.width==CAP_WIDTH && a.height==CAP_HEIGHT) return parent;
    if(depth>5) return 0;
    Window root,p,*kids=NULL,result=0; unsigned int n=0;
    if(XQueryTree(d,parent,&root,&p,&kids,&n)) {
        for(unsigned int i=0;i<n && !result;i++) result=find_window(d,kids[i],depth+1);
        if(kids) XFree(kids);
    }
    return result;
}
static int send_all(int fd,const void *data,size_t n) {
    const char *p=data;
    while(n) {
        ssize_t k=send(fd,p,n,MSG_NOSIGNAL);
        if(k<0 && errno==EINTR) continue;
        if(k<=0) return -1;
        p+=k; n-=k;
    }
    return 0;
}
/* ---- Input injection ----------------------------------------------------
 *
 * MuMuExtras calls nemu_input_event_touch_down/up and the key/text exports for
 * every tap. The original shim returned failure, which made MAA abort each
 * task with "触控模式不可用". Android 13's `input` command can inject the same
 * events, so the payload is forwarded to a long-lived `adb shell` instead of
 * spawning a process per event (a fork+exec costs ~70 ms, which is far too
 * slow for dragging and multi-finger gestures).
 *
 * The server gets its own adb daemon on port 5038. Sharing 5037 with MAA's
 * adb.exe does not work: two servers race for the port, and whichever loses
 * talks to a socket whose keys were never authorised, which surfaces as
 * "device unauthorized" mid-run. A separate port keeps both sides independent.
 */
#define ADB_PORT "5038"
#define ADB_SERIAL "127.0.0.1:5555"
static FILE *adb_shell = NULL;

static void adb_offline(void) {
    if (adb_shell) { pclose(adb_shell); adb_shell = NULL; }
}

static int adb_open(void) {
    if (adb_shell) return 0;
    char command[256];
    /* `connect` is idempotent and revives the link after a Waydroid restart,
     * which happens on every scheduled run. */
    snprintf(command, sizeof(command),
             "ANDROID_ADB_SERVER_PORT=" ADB_PORT " /usr/bin/adb connect " ADB_SERIAL
             " >/dev/null 2>&1; ANDROID_ADB_SERVER_PORT=" ADB_PORT
             " /usr/bin/adb -s " ADB_SERIAL " shell 2>/dev/null");
    adb_shell = popen(command, "w");
    return adb_shell ? 0 : -1;
}

static int adb_command(const char *arguments) {
    if (adb_open()) return -1;
    if (fprintf(adb_shell, "%s\n", arguments) < 0) { adb_offline(); return -1; }
    if (fflush(adb_shell) != 0) { adb_offline(); return -1; }
    return 0;
}

static int inject_input(char cmd, const CapHeader *in, const char *text) {
    char arguments[512];
    int x = (int)in->width, y = (int)in->height;
    switch (cmd) {
    case 'D': snprintf(arguments, sizeof(arguments),
                       "input motionevent DOWN %d %d", x, y); break;
    case 'U': snprintf(arguments, sizeof(arguments),
                       "input motionevent UP %d %d", x, y); break;
    case 'M': snprintf(arguments, sizeof(arguments),
                       "input motionevent MOVE %d %d", x, y); break;
    case 'K': snprintf(arguments, sizeof(arguments),
                       "input keyevent %u", in->bytes); break;
    case 'T':
        if (!text || !*text) return -1;
        snprintf(arguments, sizeof(arguments), "input text '%s'", text);
        break;
    default: return -1;
    }
    return adb_command(arguments);
}

static int capture(Display *d,Window w,unsigned char *out) {
    xerror=0;
    XImage *im=XGetImage(d,w,0,0,CAP_WIDTH,CAP_HEIGHT,AllPlanes,ZPixmap);
    XSync(d,False);
    if(!im || xerror) { if(im) XDestroyImage(im); return -1; }
    if(im->bits_per_pixel!=32 || im->byte_order!=LSBFirst ||
       im->red_mask!=0xff0000 || im->green_mask!=0xff00 || im->blue_mask!=0xff) {
        XDestroyImage(im); return -1;
    }
    /* MuMu's SDK returns bottom-up RGBA; MAA flips it back to top-down. */
    for(unsigned int y=0;y<CAP_HEIGHT;y++) {
        const uint32_t *src=(const uint32_t*)(im->data+y*im->bytes_per_line);
        uint32_t *dst=(uint32_t*)(out+(CAP_HEIGHT-1-y)*CAP_WIDTH*4);
        for(unsigned int x=0;x<CAP_WIDTH;x++) {
            uint32_t v=src[x];
            dst[x]=0xff000000u | ((v&0xff)<<16) | (v&0xff00) | ((v>>16)&0xff);
        }
    }
    XDestroyImage(im); return 0;
}
int main(int argc,char **argv) {
    if(argc!=3) { fprintf(stderr,"usage: capture-server DISPLAY SOCKET\n"); return 2; }
    signal(SIGPIPE,SIG_IGN); XSetErrorHandler(trap); umask(0077);
    char lockpath[256]; snprintf(lockpath,sizeof(lockpath),"%s.lock",argv[2]);
    int lockfd=open(lockpath,O_CREAT|O_RDWR|O_CLOEXEC,0600);
    if(lockfd<0 || flock(lockfd,LOCK_EX|LOCK_NB)) return 3;
    Display *d=XOpenDisplay(argv[1]); if(!d) return 4;
    int fd=socket(AF_UNIX,SOCK_STREAM|SOCK_CLOEXEC,0);
    struct sockaddr_un addr={.sun_family=AF_UNIX};
    if(strlen(argv[2])>=sizeof(addr.sun_path)) return 5;
    strcpy(addr.sun_path,argv[2]); unlink(addr.sun_path);
    if(fd<0 || bind(fd,(struct sockaddr*)&addr,sizeof(addr)) || listen(fd,4)) return 6;
    unsigned char *pixels=malloc(CAP_BYTES); if(!pixels) return 7;
    fprintf(stderr,"Ready display=%s socket=%s; fresh 1280x720 bottom-up RGBA\n",argv[1],argv[2]);
    for(;;) {
        int client=accept4(fd,NULL,NULL,SOCK_CLOEXEC);
        if(client<0) { if(errno==EINTR) continue; break; }
        struct timeval timeout={.tv_sec=1};
        setsockopt(client,SOL_SOCKET,SO_SNDTIMEO,&timeout,sizeof(timeout));
        setsockopt(client,SOL_SOCKET,SO_RCVTIMEO,&timeout,sizeof(timeout));
        struct ucred cred; socklen_t clen=sizeof(cred); char cmd=0;
        if(getsockopt(client,SOL_SOCKET,SO_PEERCRED,&cred,&clen) || cred.uid!=getuid() ||
           recv(client,&cmd,1,MSG_WAITALL)!=1) { close(client); continue; }
        if(cmd=='D' || cmd=='U' || cmd=='M' || cmd=='K' || cmd=='T') {
            /* Input: the client sends a header carrying the payload. This
             * process must not capture here, so it stays cheap enough for the
             * sub-millisecond event stream MAA's MuMuExtras path produces. */
            CapHeader in;
            if(recv(client,&in,sizeof(in),MSG_WAITALL)!=(ssize_t)sizeof(in) || in.magic!=CAP_MAGIC) {
                close(client); continue;
            }
            CapHeader h={.magic=CAP_MAGIC,.status=0,.width=CAP_WIDTH,.height=CAP_HEIGHT};
            char text[256];
            text[0]='\0';
            if(cmd=='T') {
                ssize_t got=recv(client,text,sizeof(text)-1,MSG_WAITALL);
                text[got>0?got:0]='\0';
            }
            if(inject_input(cmd,&in,text)) h.status=1;
            send_all(client,&h,sizeof(h));
            close(client); continue;
        }
        if(cmd!='I' && cmd!='C') { close(client); continue; }
        CapHeader h={.magic=CAP_MAGIC,.status=1,.width=CAP_WIDTH,.height=CAP_HEIGHT};
        Window w=find_window(d,DefaultRootWindow(d),0);
        if(w) {
            if(cmd=='I') h.status=0;
            else {
                h.capture_start_ns=now_ns();
                int result=capture(d,w,pixels);
                h.capture_end_ns=now_ns();
                if(!result) { h.status=0; h.bytes=CAP_BYTES; h.sequence=++sequence; }
            }
        }
        if(!send_all(client,&h,sizeof(h)) && h.bytes) send_all(client,pixels,h.bytes);
        close(client);
        if(cmd=='C' && (sequence<4 || sequence%100==0))
            fprintf(stderr,"frame=%lu status=%u capture=%.3fms\n",(unsigned long)sequence,h.status,
                (h.capture_end_ns-h.capture_start_ns)/1e6);
    }
    free(pixels); close(fd); unlink(addr.sun_path); XCloseDisplay(d); close(lockfd); return 1;
}
