/* tests/e2e/fake_gbrain/fake_gbrain.c
   Stand-in for the gbrain release binary in Talaria's e2e test (test_e2e_gbrain.py). It
   speaks just enough of the real CLI: --version, init --pglite, config, remember --provenance,
   recall --query, doctor --json, dream and serve --http (GET /health). The "brain" is
   files under $GBRAIN_HOME/.gbrain: config.json, schema (an integer; opening the brain
   raises it to SCHEMA, like a migration), facts and dreams.
   Build: gcc -O2 -static -DVERSION='"0.60.1.0"' -DSCHEMA=1 -o gbrain-linux-x64 fake_gbrain.c
   (static: it runs in distroless/cc, whose glibc is older than the build host's). */
#include <netinet/in.h>
#include <signal.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/socket.h>
#include <sys/stat.h>
#include <unistd.h>

#ifndef VERSION
#define VERSION "0.0.0.0"
#endif
#ifndef SCHEMA
#define SCHEMA 1
#endif

static char home[512];

static const char *path(const char *name) {
    static char buf[1024];
    snprintf(buf, sizeof buf, "%s/.gbrain/%s", home, name);
    return buf;
}

static int read_schema(void) {
    int v = 0;
    FILE *f = fopen(path("schema"), "r");
    if (f) {
        if (fscanf(f, "%d", &v) != 1) v = 0;
        fclose(f);
    }
    return v;
}

static int write_text(const char *name, const char *mode, const char *text) {
    FILE *f = fopen(path(name), mode);
    if (!f) {
        perror(name);
        return 1;
    }
    fputs(text, f);
    return fclose(f) != 0;
}

static int open_brain(void) {
    char v[32];
    if (access(path("config.json"), F_OK) != 0) {
        fprintf(stderr, "Error: no brain at %s; run gbrain init\n", home);
        return 1;
    }
    if (read_schema() >= SCHEMA) return 0;
    snprintf(v, sizeof v, "%d\n", SCHEMA);
    return write_text("schema", "w", v);
}

static void on_term(int sig) {
    (void)sig;
    _exit(0);
}

static int serve(int port) {
    int s = socket(AF_INET, SOCK_STREAM, 0), one = 1;
    struct sockaddr_in a;
    memset(&a, 0, sizeof a);
    a.sin_family = AF_INET;
    a.sin_port = htons((unsigned short)port);
    a.sin_addr.s_addr = htonl(INADDR_ANY);
    if (s < 0 || setsockopt(s, SOL_SOCKET, SO_REUSEADDR, &one, sizeof one) != 0 ||
        bind(s, (struct sockaddr *)&a, sizeof a) != 0 || listen(s, 16) != 0) {
        perror("serve");
        return 1;
    }
    signal(SIGTERM, on_term);
    signal(SIGINT, on_term);
    printf("MCP Server v%s listening on 0.0.0.0:%d\n", VERSION, port);
    fflush(stdout);
    for (;;) {
        char req[2048], body[256], out[512];
        int c = accept(s, NULL, NULL), n, ok;
        if (c < 0) continue;
        n = (int)read(c, req, sizeof req - 1);
        req[n > 0 ? n : 0] = '\0';
        ok = strncmp(req, "GET /health ", 12) == 0;
        if (ok)
            snprintf(body, sizeof body,
                     "{\"status\":\"ok\",\"version\":\"%s\",\"engine\":\"pglite\"}", VERSION);
        else
            snprintf(body, sizeof body, "{\"error\":\"not found\"}");
        n = snprintf(out, sizeof out,
                     "HTTP/1.1 %s\r\nContent-Type: application/json\r\nContent-Length: %zu\r\n"
                     "Connection: close\r\n\r\n%s",
                     ok ? "200 OK" : "404 Not Found", strlen(body), body);
        if (write(c, out, (size_t)n) < 0) perror("write");
        close(c);
    }
}

int main(int argc, char **argv) {
    const char *h = getenv("GBRAIN_HOME");
    const char *cmd = argc > 1 ? argv[1] : "";
    snprintf(home, sizeof home, "%s", h && *h ? h : "/data");
    if (!strcmp(cmd, "--version") || !strcmp(cmd, "version")) {
        printf("gbrain %s\n", VERSION);
        return 0;
    }
    if (!strcmp(cmd, "init")) {
        char dir[1024];
        snprintf(dir, sizeof dir, "%s/.gbrain", home);
        if (mkdir(dir, 0700) != 0 && access(dir, F_OK) != 0) {
            perror(dir);
            return 1;
        }
        return write_text("config.json", "w", "{\"engine\":\"pglite\"}\n") || open_brain();
    }
    if (open_brain() != 0) return 1;
    if (!strcmp(cmd, "config")) return 0;
    if (!strcmp(cmd, "remember") && argc > 2) {
        /* like the real CLI: a fact needs --provenance <source> */
        if (argc < 5 || strcmp(argv[3], "--provenance") != 0) {
            fprintf(stderr, "Error: remember requires --provenance <source>\n");
            return 1;
        }
        return write_text("facts", "a", argv[2]) || write_text("facts", "a", "\n");
    }
    if (!strcmp(cmd, "recall") && argc > 3 && !strcmp(argv[2], "--query")) {
        char line[1024];
        FILE *f;
        /* the real one, without an embedding provider, falls back to keyword search and
           says so on stdout; `search` does not find facts (not implemented here) */
        puts("note: search degraded (keyword_only_no_embedding_provider)");
        f = fopen(path("facts"), "r");
        if (!f) return 0;
        while (fgets(line, sizeof line, f))
            if (strstr(line, argv[3])) fputs(line, stdout);
        fclose(f);
        return 0;
    }
    if (!strcmp(cmd, "doctor")) {
        int v = read_schema();
        /* the real one wraps its JSON in bracketed notices */
        puts("[backup] last backup: never");
        puts("[AGENT] doctor ran offline; no action needed [/AGENT]");
        if (v > SCHEMA)
            printf("{\"status\":\"warn\",\"checks\":[{\"name\":\"schema_version\",\"status\":"
                   "\"warn\",\"message\":\"Version %d is AHEAD of this client's latest known "
                   "version (%d).\"}]}\n", v, SCHEMA);
        else
            printf("{\"status\":\"ok\",\"checks\":[{\"name\":\"schema_version\",\"status\":"
                   "\"ok\",\"message\":\"Version %d (latest: %d)\"}]}\n", v, SCHEMA);
        puts("[backup] run `gbrain backup` to create one");
        return 0;
    }
    if (!strcmp(cmd, "dream")) {
        if (getenv("FAKE_DREAM_FAIL")) {
            fprintf(stderr, "dream: phase synthesize failed\n");
            return 1;
        }
        return write_text("dreams", "a", "dream\n");
    }
    if (!strcmp(cmd, "serve")) {
        int port = 3131;
        for (int i = 2; i + 1 < argc; i++)
            if (!strcmp(argv[i], "--port")) port = atoi(argv[i + 1]);
        return serve(port);
    }
    fprintf(stderr, "fake gbrain: unknown command %s\n", cmd);
    return 2;
}
