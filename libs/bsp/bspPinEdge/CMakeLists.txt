# Pin edge contract (IO-52): consumer and arming interface between the pin-interrupt dispatcher of a family
# (platforms/<family>/bsp/bspPinIrq) and its consumers (Hall access, pin-event module). Header only.
add_library(bspPinEdge INTERFACE)

target_include_directories(bspPinEdge INTERFACE include)
