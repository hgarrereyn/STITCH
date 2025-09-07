#define _GNU_SOURCE
#include <dlfcn.h>
#include <errno.h>
#include <signal.h>
#include <stdarg.h>
#include <stddef.h>
#include <stdint.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include <sys/syscall.h>

/* ---------------- Utilities (no allocation, async-signal-safe) ---------------- */

static inline pid_t gettid_safe(void) {
#ifdef SYS_gettid
    return (pid_t)syscall(SYS_gettid);
#else
    return (pid_t)getpid(); /* fallback; not ideal but ok for signaling */
#endif
}

static void safe_write_str(const char *s) {
    if (!s) return;
    size_t n = 0; while (s[n] != '\0') n++;
    /* Ignore short writes intentionally */
    (void)!write(STDERR_FILENO, s, n);
}

/* Send SIGUSR2 to current thread; keep process state intact */
static void oom_signal(void) {
#ifdef SYS_tgkill
    (void)syscall(SYS_tgkill, (pid_t)getpid(), gettid_safe(), SIGUSR2);
#else
    /* Fallback: process-directed (less precise) */
    (void)syscall(SYS_kill, (pid_t)getpid(), SIGUSR2);
#endif
}

/* ---------------- Real symbol pointers ---------------- */

static void* (*real_malloc)(size_t)                          = NULL;
static void* (*real_calloc)(size_t, size_t)                  = NULL;
static void* (*real_realloc)(void*, size_t)                  = NULL;
static int   (*real_posix_memalign)(void**, size_t, size_t)  = NULL;
static void* (*real_aligned_alloc)(size_t, size_t)           = NULL;
static void  (*real_free)(void*)                             = NULL;

/* Thread-local reentrancy guard (depth counter avoids clobbering nested wrappers) */
static __thread int in_alloc = 0;
#define ENTER_ALLOC()   do { in_alloc++; } while (0)
#define LEAVE_ALLOC()   do { in_alloc--; } while (0)

/* Resolve all symbols up front to avoid dlsym during allocations */
__attribute__((constructor))
static void init_oom_wrapper(void) {
    /* dlsym can allocate; try to keep this minimal and early */
    real_malloc         = (void*(*)(size_t))                         dlsym(RTLD_NEXT, "malloc");
    real_calloc         = (void*(*)(size_t,size_t))                  dlsym(RTLD_NEXT, "calloc");
    real_realloc        = (void*(*)(void*,size_t))                   dlsym(RTLD_NEXT, "realloc");
    real_posix_memalign = (int(*)(void**,size_t,size_t))             dlsym(RTLD_NEXT, "posix_memalign");
    real_aligned_alloc  = (void*(*)(size_t,size_t))                  dlsym(RTLD_NEXT, "aligned_alloc");
    real_free           = (void(*)(void*))                           dlsym(RTLD_NEXT, "free");

    int ok = real_malloc && real_calloc && real_realloc && real_posix_memalign && real_aligned_alloc && real_free;
    if (!ok) {
        safe_write_str("[oom_wrapper] failed to resolve allocator symbols\n");
        /* Best effort: do not abort; allow program to run without interposition */
    }
}

/* ---------------- OOM detection + signaling ---------------- */

static inline void handle_oom_if_needed(int failed, size_t bytes) {
    if (!failed) return;
    if (bytes == 0) return;          /* malloc(0) may return NULL per spec */
    /* Preserve errno; just send a signal */
    oom_signal();
}

/* ---------------- Interposed functions ---------------- */

void* malloc(size_t size) {
    if (!real_malloc) init_oom_wrapper();
    if (!real_malloc) return NULL;

    /* Avoid re-entrancy recursion: if inside, just call real_… without signaling/logging */
    if (in_alloc) return real_malloc(size);

    ENTER_ALLOC();
    void* p = real_malloc(size);
    int failed = (p == NULL);
    int saved = errno;
    handle_oom_if_needed(failed, size);
    errno = saved;
    LEAVE_ALLOC();
    return p;
}

void* calloc(size_t nmemb, size_t size) {
    if (!real_calloc) init_oom_wrapper();
    if (!real_calloc) return NULL;

    if (in_alloc) return real_calloc(nmemb, size);

    ENTER_ALLOC();
    void* p = real_calloc(nmemb, size);
    int failed = (p == NULL);
    int saved = errno;
    /* If nmemb*size overflowed, real_calloc typically sets ENOMEM already */
    handle_oom_if_needed(failed, (size_t)((unsigned __int128)nmemb * (unsigned __int128)size));
    errno = saved;
    LEAVE_ALLOC();
    return p;
}

void* realloc(void* ptr, size_t size) {
    if (!real_realloc) init_oom_wrapper();
    if (!real_realloc) return NULL;

    if (in_alloc) return real_realloc(ptr, size);

    ENTER_ALLOC();
    void* p = real_realloc(ptr, size);
    int failed = (p == NULL && size != 0); /* realloc(ptr,0) may free and return NULL without OOM */
    int saved = errno;
    handle_oom_if_needed(failed, size);
    errno = saved;
    LEAVE_ALLOC();
    return p;
}

int posix_memalign(void** memptr, size_t alignment, size_t size) {
    if (!real_posix_memalign) init_oom_wrapper();
    if (!real_posix_memalign) return ENOMEM;

    if (in_alloc) return real_posix_memalign(memptr, alignment, size);

    ENTER_ALLOC();
    int r = real_posix_memalign(memptr, alignment, size);
    int saved = errno;
    if (r == ENOMEM && size > 0) {
        handle_oom_if_needed(1, size);
    }
    errno = saved;
    LEAVE_ALLOC();
    return r;
}

void* aligned_alloc(size_t alignment, size_t size) {
    if (!real_aligned_alloc) init_oom_wrapper();
    if (!real_aligned_alloc) return NULL;

    if (in_alloc) return real_aligned_alloc(alignment, size);

    ENTER_ALLOC();
    void* p = real_aligned_alloc(alignment, size);
    int failed = (p == NULL && size > 0);
    int saved = errno;
    handle_oom_if_needed(failed, size);
    errno = saved;
    LEAVE_ALLOC();
    return p;
}

void free(void* ptr) {
    if (!real_free) init_oom_wrapper();
    if (!real_free) return;
    /* free should never signal; just pass through, guarding recursion */
    if (in_alloc) { real_free(ptr); return; }
    ENTER_ALLOC();
    real_free(ptr);
    LEAVE_ALLOC();
}

/* ---------------- Optional: DO NOT INTERPOSE MMAP ----------------
   Interposing mmap/mprotect/mremap is fragile and not needed to catch OOM
   when using modern allocators (mimalloc/jemalloc). If you ever decide to
   interpose mmap, never return pointers to static buffers and always set
   errno on MAP_FAILED. Keep it disabled. */
/*
#include <sys/mman.h>
static void* (*real_mmap)(void*, size_t, int, int, int, off_t) = NULL;
void* mmap(void* addr, size_t length, int prot, int flags, int fd, off_t offset) {
    if (!real_mmap) real_mmap = dlsym(RTLD_NEXT, "mmap");
    if (!real_mmap) { errno = ENOSYS; return MAP_FAILED; }
    if (in_alloc) return real_mmap(addr,length,prot,flags,fd,offset);
    ENTER_ALLOC();
    void* r = real_mmap(addr,length,prot,flags,fd,offset);
    int saved = errno;
    if (r == MAP_FAILED && errno == ENOMEM && length > 0) {
        handle_oom_if_needed(1, length);
    }
    errno = saved;
    LEAVE_ALLOC();
    return r;
}
*/

/* ---------------- End ---------------- */
