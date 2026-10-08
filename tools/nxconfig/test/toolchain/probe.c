/* Probe for the toolchain configure test: floating point arguments show the float ABI in the object attributes. */
float probe_scale(float value, float factor);

float probe_scale(float value, float factor)
{
    return value * factor;
}
