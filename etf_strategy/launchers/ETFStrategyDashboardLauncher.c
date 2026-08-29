#include <errno.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <sys/wait.h>
#include <time.h>

static void write_log(const char *message) {
    const char *home = getenv("HOME");
    if (home == NULL || home[0] == '\0') {
        home = "/Users/zhoulin";
    }

    char dir[1024];
    snprintf(dir, sizeof(dir), "%s/Library/Logs/ETFStrategy", home);
    mkdir(dir, 0755);

    char path[1200];
    snprintf(path, sizeof(path), "%s/launcher_app.log", dir);
    FILE *file = fopen(path, "a");
    if (file == NULL) {
        return;
    }

    time_t now = time(NULL);
    struct tm local_time;
    localtime_r(&now, &local_time);
    char stamp[64];
    strftime(stamp, sizeof(stamp), "%Y-%m-%d %H:%M:%S", &local_time);
    fprintf(file, "[%s] %s\n", stamp, message);
    fclose(file);
}

int main(void) {
    const char *command_file = "/Users/zhoulin/Desktop/ETFStrategy Dashboard.command";
    char command[1400];

    write_log("app launcher invoked");
    snprintf(command, sizeof(command), "/usr/bin/open '%s'", command_file);

    int result = system(command);
    if (result == -1) {
        char message[256];
        snprintf(message, sizeof(message), "system failed: %s", strerror(errno));
        write_log(message);
        return 1;
    }

    if (WIFEXITED(result)) {
        int code = WEXITSTATUS(result);
        char message[128];
        snprintf(message, sizeof(message), "script exited with code %d", code);
        write_log(message);
        return code;
    }

    write_log("script did not exit normally");
    return 1;
}
