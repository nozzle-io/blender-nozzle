cmake_minimum_required(VERSION 3.20)
project(blender_nozzle LANGUAGES C CXX)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF)

# nozzle submodule
# MODULE (shared lib) requires all deps compiled with -fPIC
set(CMAKE_POSITION_INDEPENDENT_CODE ON)
add_subdirectory(nozzle)

# Find Python3 — prefer Blender's bundled Python if BLENDER_PYTHON_PATH is set
if(BLENDER_PYTHON_PATH)
    set(Python3_ROOT_DIR "${BLENDER_PYTHON_PATH}")
endif()
find_package(Python3 REQUIRED COMPONENTS Interpreter Development)

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
    ${Python3_LIBRARIES}
)

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
