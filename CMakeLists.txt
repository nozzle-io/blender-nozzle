cmake_minimum_required(VERSION 3.20)
project(blender_nozzle LANGUAGES C CXX)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF)

# nozzle submodule
# MODULE (shared lib) requires all deps compiled with -fPIC
set(CMAKE_POSITION_INDEPENDENT_CODE ON)
add_subdirectory(nozzle)

# Find Python3 — prefer Blender's bundled Python if BLENDER_PYTHON_PATH is set.
# On Unix-like platforms, build a Python extension module without linking
# libpython; Blender provides the Python runtime. Windows extension modules need
# the Python import library.
if(BLENDER_PYTHON_PATH)
    set(Python3_ROOT_DIR "${BLENDER_PYTHON_PATH}")
endif()

option(NOZZLE_BLENDER_PIXEL_PEER "Build the independent Metal pixel peer for CI" OFF)
if(NOZZLE_BLENDER_PIXEL_PEER)
    if(NOT APPLE)
        message(FATAL_ERROR "The Blender pixel peer requires Metal")
    endif()
    add_library(blender_pixel_peer SHARED tests/native_pixel_peer.cpp)
    target_link_libraries(blender_pixel_peer PRIVATE nozzle)
    add_executable(blender_pixel_rows tests/pixel_rows.c)
    target_include_directories(blender_pixel_rows PRIVATE nozzle/include)
endif()
if(WIN32)
    find_package(Python3 REQUIRED COMPONENTS Interpreter Development)
else()
    find_package(Python3 REQUIRED COMPONENTS Interpreter Development.Module)
endif()

# Platform detection
if(APPLE)
    set(NOZZLE_PLATFORM_MACOS 1)
elseif(WIN32)
    set(NOZZLE_PLATFORM_WINDOWS 1)
elseif(UNIX)
    set(NOZZLE_PLATFORM_LINUX 1)
endif()

# Native extension module
add_library(_nozzle_native MODULE src/_nozzle_module.c)

target_include_directories(_nozzle_native PRIVATE
    ${Python3_INCLUDE_DIRS}
    nozzle/include
    src
)

target_link_libraries(_nozzle_native PRIVATE
    nozzle
)
if(WIN32)
    target_link_libraries(_nozzle_native PRIVATE ${Python3_LIBRARIES})
endif()

# Must not link libpython on macOS (Blender provides it)
if(APPLE)
    set_target_properties(_nozzle_native PROPERTIES
        LINK_FLAGS "-undefined dynamic_lookup"
    )
endif()

# Extension suffix
execute_process(
    COMMAND "${Python3_EXECUTABLE}" -c "import sysconfig; print(sysconfig.get_config_var('EXT_SUFFIX') or '')"
    OUTPUT_VARIABLE NOZZLE_NATIVE_SUFFIX
    OUTPUT_STRIP_TRAILING_WHITESPACE
)
if(NOT NOZZLE_NATIVE_SUFFIX)
    set(NOZZLE_NATIVE_SUFFIX "${CMAKE_SHARED_MODULE_SUFFIX}")
endif()
set_target_properties(_nozzle_native PROPERTIES
    PREFIX ""
    SUFFIX "${NOZZLE_NATIVE_SUFFIX}"
)

# Nozzle platform deps are already linked via the nozzle target
if(APPLE)
    set_target_properties(_nozzle_native PROPERTIES
        CXX_STANDARD 17
    )
endif()
