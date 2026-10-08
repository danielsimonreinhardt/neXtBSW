/* Minimal gcov runtime for coverage builds with "zig cc --coverage" (TOOL-40).
 *
 * The ziglang package ships clang but no compiler-rt profile runtime, so the
 * llvm_gcda_* symbols emitted by --coverage are undefined at link time. This
 * file provides them: it writes the .gcda counter files in the GCC format the
 * compiler announces in llvm_gcda_start_file(), which gcov then reads.
 */

#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#define GCOV_TAG_FUNCTION       0x01000000u
#define GCOV_TAG_COUNTER_ARCS   0x01a10000u
#define GCOV_TAG_OBJECT_SUMMARY 0xa1000000u
#define GCOV_TAG_PROGRAM_SUMMARY 0xa3000000u

typedef void (*fn_ptr)(void);

#define MAX_WRITEOUT 256
static fn_ptr writeout_fns[MAX_WRITEOUT];
static unsigned writeout_count;
static int atexit_registered;

static FILE* out_file;
static unsigned gcov_version; /* 47 == 4.7, 111 == 11.1 ... */

static void write_32(uint32_t v)
{
    unsigned char b[4];
    b[0] = (unsigned char)(v & 0xffu);
    b[1] = (unsigned char)((v >> 8) & 0xffu);
    b[2] = (unsigned char)((v >> 16) & 0xffu);
    b[3] = (unsigned char)((v >> 24) & 0xffu);
    if (out_file) fwrite(b, 1, 4, out_file);
}

static void write_64(uint64_t v)
{
    write_32((uint32_t)(v & 0xffffffffu));
    write_32((uint32_t)(v >> 32));
}

void llvm_gcda_start_file(const char* orig_filename, uint32_t version, uint32_t checksum)
{
    unsigned char c3 = (unsigned char)(version >> 24);
    unsigned char c2 = (unsigned char)((version >> 16) & 0xffu);
    unsigned char c1 = (unsigned char)((version >> 8) & 0xffu);
    gcov_version = c3 >= 'A' ? (unsigned)((c3 - 'A') * 100 + (c2 - '0') * 10 + (c1 - '0'))
                             : (unsigned)((c3 - '0') * 10 + (c1 - '0'));

    out_file = fopen(orig_filename, "wb");
    if (!out_file)
    {
        fprintf(stderr, "gcov runtime: cannot write %s\n", orig_filename);
        return;
    }
    fwrite("adcg", 1, 4, out_file);
    write_32(version);
    write_32(checksum);
}

void llvm_gcda_emit_function(uint32_t ident, uint32_t func_checksum, uint32_t cfg_checksum)
{
    uint32_t len = gcov_version >= 47 ? 3u : 2u;
    write_32(GCOV_TAG_FUNCTION);
    write_32(len);
    write_32(ident);
    write_32(func_checksum);
    if (gcov_version >= 47) write_32(cfg_checksum);
}

void llvm_gcda_emit_arcs(uint32_t num_counters, const uint64_t* counters)
{
    uint32_t i;
    write_32(GCOV_TAG_COUNTER_ARCS);
    write_32(num_counters * 2u);
    for (i = 0; i < num_counters; ++i) write_64(counters[i]);
}

void llvm_gcda_summary_info(void)
{
    if (gcov_version >= 90)
    {
        write_32(GCOV_TAG_OBJECT_SUMMARY);
        write_32(2);
        write_32(1); /* runs */
        write_32(0); /* sum_max */
    }
    else
    {
        write_32(GCOV_TAG_PROGRAM_SUMMARY);
        write_32(3);
        write_32(0);
        write_32(0);
        write_32(1); /* runs */
    }
}

void llvm_gcda_end_file(void)
{
    if (out_file)
    {
        static const unsigned char eof[8] = {0, 0, 0, 0, 0, 0, 0, 0};
        fwrite(eof, 1, 8, out_file);
        fclose(out_file);
        out_file = NULL;
    }
}

static void writeout_all(void)
{
    unsigned i;
    for (i = 0; i < writeout_count; ++i) writeout_fns[i]();
}

void llvm_gcov_init(fn_ptr wfn, fn_ptr rfn)
{
    (void)rfn;
    if (wfn && writeout_count < MAX_WRITEOUT) writeout_fns[writeout_count++] = wfn;
    if (!atexit_registered)
    {
        atexit_registered = 1;
        atexit(writeout_all);
    }
}

/* gcov API a test might call explicitly */
void __gcov_dump(void) { writeout_all(); }
void __gcov_reset(void) {}
