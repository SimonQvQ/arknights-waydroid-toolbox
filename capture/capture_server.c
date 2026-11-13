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

/* Waydroid's container address is not fixed across vendors/installs, and MAA
 * already carries the working one in its own config, so read it from there
 * instead of hard-coding a guess. Falling back to the mainline default keeps a
 * fresh install usable. */
#define ADB_SERIAL_DEFAULT "192.168.240.112:5555"
#define MAA_CONFIG "/home/simon/.local/share/maa-wine/app/config/gui.new.json"

static char adb_serial[64] = ADB_SERIAL_DEFAULT;

static void adb_resolve_serial(void) {
    FILE *config = fopen(MAA_CONFIG, "r");
    if (!config) return;
    static char buffer[1 << 16];
    size_t length = fread(buffer, 1, sizeof(buffer) - 1, config);
    fclose(config);
    buffer[length] = '\0';
    /* The connect settings carry a single "Address": "host:port" pair; taking
     * the first occurrence matches the active configuration MAA itself uses. */
    const char *key = "\"Address\"";
    char *found = strstr(buffer, key);
    if (!found) return;
    char *quote = strchr(found + strlen(key), ':');
    if (!quote) return;
    char *start = strchr(quote, '"');
    if (!start) return;
    start++;
    char *end = strchr(start, '"');
    if (!end || end - start <= 0 || (size_t)(end - start) >= sizeof(adb_serial)) return;
    memcpy(adb_serial, start, (size_t)(end - start));
    adb_serial[end - start] = '\0';
}

/* `adb shell` is deliberately NOT kept as a long-lived popen(): writing
 * commands into a shell whose stdout is a pipe nobody drains blocks as soon as
 * one command prints anything, which silently swallowed every tap. Each input
 * event now runs its own short-lived adb invocation. */
/* The private adb server on ADB_PORT starts empty: it is separate from MAA's
 * own server on 5037, so a device connected there is invisible here. Every
 * command therefore reconnects once on failure -- without this the very first
 * tap of a session dies with "device not found" and the game never wakes up. */
static int adb_ensure_device(void) {
    char command[256];
    snprintf(command, sizeof(command),
             "ANDROID_ADB_SERVER_PORT=" ADB_PORT " /usr/bin/adb connect %s "
             ">/dev/null 2>&1", adb_serial);
    return system(command) == 0 ? 0 : -1;
}

static int adb_command(const char *arguments) {
    char command[1024];
    for (int attempt = 0; attempt < 2; attempt++) {
        snprintf(command, sizeof(command),
                 "ANDROID_ADB_SERVER_PORT=" ADB_PORT " /usr/bin/adb -s %s shell %s "
                 ">/dev/null 2>&1", adb_serial, arguments);
        if (system(command) == 0) return 0;
        if (attempt == 0) adb_ensure_device();
    }
    fprintf(stderr, "adb_command failed cmd=[%s] errno=%d\n", command, errno);
    return -1;
}

/* A gesture arrives as a down, zero or more moves and an up. Those must become
 * ONE `input tap`/`input swipe`: `input motionevent DOWN` and `... UP` are two
 * independent injections, each with its own downTime, so the game receives an
 * orphan up and reports no tap at all. That is why Arknights ignored hundreds
 * of perfectly delivered touches and sat on its title screen. */
static int gesture_x = -1, gesture_y = -1, gesture_last_x = -1, gesture_last_y = -1;

static int inject_input(char cmd, const CapHeader *in, const char *text) {
    char arguments[512];
    int x = (int)in->width, y = (int)in->height;
    switch (cmd) {
    case 'D':
        gesture_x = x; gesture_y = y; gesture_last_x = -1; gesture_last_y = -1;
        return 0;
    case 'M':
        gesture_last_x = x; gesture_last_y = y;
        return 0;
    case 'U': {
        if (gesture_x < 0) return -1;
        int x0 = gesture_x, y0 = gesture_y;
        int x1 = gesture_last_x >= 0 ? gesture_last_x : x;
        int y1 = gesture_last_y >= 0 ? gesture_last_y : y;
        gesture_x = gesture_y = gesture_last_x = gesture_last_y = -1;
        if (x1 != x0 || y1 != y0)
            snprintf(arguments, sizeof(arguments),
                     "input swipe %d %d %d %d 300", x0, y0, x1, y1);
        else
            snprintf(arguments, sizeof(arguments), "input tap %d %d", x0, y0);
        break;
    }
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
    adb_resolve_serial();
    /* Warm the private adb server before the socket exists. The client side
     * allows a tap request only a short timeout, and the very first `adb`
     * invocation of a session pays for starting the daemon and the device
     * handshake. Doing it here means the first real tap is as cheap as the
     * rest. Failure is not fatal: adb_command retries with a reconnect. */
    adb_ensure_device();
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
    fprintf(stderr,"Ready display=%s socket=%s adb=%s; fresh 1280x720 bottom-up RGBA\n",
            argv[1],argv[2],adb_serial);
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
            if(inject_input(cmd,&in,text)) {
                h.status=1;
                fprintf(stderr,"input %c failed\n",cmd);
            }
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
