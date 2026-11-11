/* Stand-in for MuMu's MuMuManager.exe.
 *
 * MAA >= 6.3 only enables the MuMu external-renderer input path when it can
 * run `<mumu_path>/nx_main/MuMuManager.exe version` and parse a version of at
 * least 6.3.2.0 from it. Without the binary it logs
 *
 *     MuMuManager not found, disable extras input
 *     MuMu extras input is not available, fallback to minitouch
 *
 * and minitouch cannot find a touch device inside Waydroid, so every task
 * aborts with "触控模式不可用". Reporting the version is therefore the whole
 * contract this file has to satisfy: the input calls themselves go through
 * nemu_input_event_finger_touch_down/up in external_renderer_ipc.dll.
 */
#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#include <stdio.h>
#include <string.h>

int main(int argc, char **argv) {
    if (argc >= 2 && strcmp(argv[1], "version") == 0) {
        /* Newer MuMu prints a small JSON object; MAA parses the version field. */
        printf("{\"version\": \"6.3.2.0\"}\n");
        fflush(stdout);
        return 0;
    }
    /* Other subcommands are outside what MAA needs; fail loudly so a future
     * caller notices instead of acting on bogus output. */
    fputs("MuMuManager stub: only `version` is implemented\n", stderr);
    return 1;
}
