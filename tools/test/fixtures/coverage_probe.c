/* Fixture of tools/test/test_coverage.py (TOOL-40): a known amount of covered and uncovered code.
 *
 * The test measures this file with the whole chain (zig cc --coverage, gcov_runtime.c, gcov) and checks that
 * what is not executed really is reported as not executed - a coverage tool that finds everything covered is
 * worthless. It is not a host test of the repository and deliberately does not match the inventory patterns of
 * tools/test/tests.yaml.
 */

#include <stdio.h>

static int classify(int value)
{
    if (value > 10) /* both edges taken */
    {
        return 2;
    }
    if (value < 0) /* never taken: the else edge stays open */
    {
        return 1;
    }
    return 0;
}

static int never_called(int value) /* never executed: four uncovered lines */
{
    int doubled = value * 2;
    return doubled + 1;
}

int main(void)
{
    int sum = classify(20) + classify(5);
    if (sum != 2)
    {
        printf("unexpected %d\n", sum);
        return 1;
    }
    printf("coverage probe: %d checks, 0 failures\n", 1);
    (void)never_called;
    return 0;
}
