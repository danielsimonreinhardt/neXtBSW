#include "bl_main.h"

int main(void)
{
    bl_init();
    for (;;)
    {
        bl_poll();
    }
}
