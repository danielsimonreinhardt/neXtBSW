add_library(configuration common/src/busid/BusId.cpp)

# busid/BusId.h takes the bus ids of the project from busid/busIdConfiguration.h (tools/bswgen, ARCH-07).
target_include_directories(configuration PUBLIC include "${BOARD_GENERATED_DIR}")

target_link_libraries(configuration PUBLIC common async)

# python -m blob binary -i routing.jsonl -c blob.routing.table | python -m blob
# header data -n CONFIGURATION_BLOB > include/blob/configuration.h

# python -m blob.routing header ${BLOB_INPUT_FILE} > include/routing/channelId.h

# python -m blob header config-type > include/blob/ConfigType.h
target_include_directories(blobConfiguration INTERFACE include)
target_include_directories(routingConfiguration INTERFACE include)
