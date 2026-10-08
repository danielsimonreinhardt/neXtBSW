/* newlib system calls without a file system (PLAT-45), family and core neutral, for every ELF of neXtBSW. A normal
 * source of its own (PLAT-66): application (platforms/<family>/port/main/CMakeLists.txt, as objects on the link line),
 * bootloader (bootloader/CMakeLists.txt), updater (bootloader/updater/CMakeLists.txt) and the core build test
 * platforms/common/test/cortexm0plus.
 *
 * Newer newlib (e.g. xPack arm-none-eabi-gcc 11.3.1, tools/ci/versions.toml) references _close/_lseek/_read/_write
 * from its stdio tables (stdio.o, pulled in by exit() of crt0.o). libnosys then satisfies them with stubs that carry
 * a link warning ("... is not implemented and will always fail"), which breaks the warning free build. Nothing in
 * neXtBSW uses newlib file I/O, so with --gc-sections these functions are discarded like the libnosys ones; the
 * NXP GCC 11.4 (older newlib) does not reference them at all, its images stay byte identical.
 *
 * Weak: a port that routes newlib stdio (e.g. _write to a UART) defines its own strong function.
 * No errno: setting it would pull in the reentrancy structure; callers only see the failure.
 * GCC-compatible toolchains only (newlib, weak attribute), like the rest of neXtBSW (generic arm-none-eabi-gcc). */

int _close(int file);
int _lseek(int file, int offset, int whence);
int _read(int file, char* data, int length);
int _write(int file, char const* data, int length);

__attribute__((weak)) int _close(int file)
{
    (void)file;
    return -1;
}

__attribute__((weak)) int _lseek(int file, int offset, int whence)
{
    (void)file;
    (void)offset;
    (void)whence;
    return -1;
}

__attribute__((weak)) int _read(int file, char* data, int length)
{
    (void)file;
    (void)data;
    (void)length;
    return -1;
}

__attribute__((weak)) int _write(int file, char const* data, int length)
{
    (void)file;
    (void)data;
    (void)length;
    return -1;
}
