cmake_minimum_required(VERSION 3.26)

project(dancers_orca_wrapper LANGUAGES CXX)

set(RVO2_SOURCE_DIR "" CACHE PATH "Authenticated RVO2 Git checkout root")
if(NOT IS_ABSOLUTE "${RVO2_SOURCE_DIR}")
  message(FATAL_ERROR "RVO2_SOURCE_DIR must be an absolute path")
endif()
get_filename_component(RVO2_SOURCE_REAL "${RVO2_SOURCE_DIR}" REALPATH)
if(NOT IS_DIRECTORY "${RVO2_SOURCE_REAL}")
  message(FATAL_ERROR "RVO2_SOURCE_DIR must identify an existing directory")
endif()

function(require_rvo2_sha256 relative expected)
  set(path "${RVO2_SOURCE_REAL}/${relative}")
  if(NOT EXISTS "${path}" OR IS_DIRECTORY "${path}")
    message(FATAL_ERROR "Required RVO2 file is missing: ${relative}")
  endif()
  file(SHA256 "${path}" actual)
  if(NOT actual STREQUAL expected)
    message(FATAL_ERROR "RVO2 identity differs for ${relative}: ${actual}")
  endif()
endfunction()

require_rvo2_sha256(
  CMakeLists.txt
  10cd10cdaec86aae0e4020122ab088cae80be68757f68d5510c23058528efc32)
require_rvo2_sha256(
  examples/CMakeLists.txt
  93dca8019b08a7913d2c9aa7f2d500eca9229cb9ccab368982ba6eb4ee4be29e)
require_rvo2_sha256(
  doc/CMakeLists.txt
  9754d25717f4f5a9a76928785a4a12e2c07189ca21e82b133b7e3e899882e999)
require_rvo2_sha256(
  src/CMakeLists.txt
  8f0f2e38b2a4d5ddc07f9ec40bb78988336c1ec6fe30a28497e08a5764c8b4b8)
require_rvo2_sha256(
  LICENSE
  cfc7749b96f63bd31c3c42b5c471bf756814053e847c10f3eb003417bc523d30)
require_rvo2_sha256(
  src/Agent.cc
  3febd8325b485bf866e7567f49767ada74eec7041c6973436dc21d43f16e8548)
require_rvo2_sha256(
  src/Agent.h
  838813f7c67299be52be403d150d90c00fc661690a18cfa69e25f51000ebd2df)
require_rvo2_sha256(
  src/Export.h
  7adb0e338dd235cabab366ff49bff7df140bfc23116ff4c0777818d39b8e8da3)
require_rvo2_sha256(
  src/KdTree.cc
  b2ebe50f5800fde8f7901d6b889e25a51e5ca34c9dbc5cc6e523636ec14e68cf)
require_rvo2_sha256(
  src/KdTree.h
  01779fa7ef393bf71229665e420fdb0550066c95dd8edbcf6bb4c8aea824006b)
require_rvo2_sha256(
  src/Line.cc
  a145ab18c104838941da7c4a38278366153b5c4c23841c7e69ebbdf573ce087b)
require_rvo2_sha256(
  src/Line.h
  5d99331853c2897f4a10f729dff18e96eb202b5dd3d8cdeb388fef4845d7e239)
require_rvo2_sha256(
  src/Obstacle.cc
  dd3c7f79b81e76df3322801eb6fcc8d0e309a321b5b6e9b39da054448fc7724d)
require_rvo2_sha256(
  src/Obstacle.h
  9c6911e0813aba9004b9d520ed49134a1e4b8e2e820a45f55c7416dd57c97b36)
require_rvo2_sha256(
  src/RVO.h
  0aa920cd3acbacc387b6ad9d4b230d17b7357ed94bdc3aba9d4ce15587abd1f8)
require_rvo2_sha256(
  src/RVOSimulator.cc
  008ccf1c7866c01c0c1cc7a954dc0feec5462e2302c88da13b53582d35399bc8)
require_rvo2_sha256(
  src/RVOSimulator.h
  fb99d48cfe5988d188db469f93648bf3f16275a715962b5736ec8ce3fd8d5ba3)
require_rvo2_sha256(
  src/Vector2.cc
  72dd527c14577be84cf28576df9975cac424c3ca7878fc84a7f390585f7e747b)
require_rvo2_sha256(
  src/Vector2.h
  90dba081e6179bbb4e94de4c449977344931bf17d0f1e3a05794e2334eeca76d)

find_package(Git REQUIRED)
if(NOT EXISTS "${RVO2_SOURCE_REAL}/.git")
  message(FATAL_ERROR "RVO2_SOURCE_DIR must be the root of an exact Git checkout")
endif()
execute_process(
  COMMAND "${GIT_EXECUTABLE}" -C "${RVO2_SOURCE_REAL}" rev-parse HEAD
  OUTPUT_VARIABLE rvo2_head OUTPUT_STRIP_TRAILING_WHITESPACE
  COMMAND_ERROR_IS_FATAL ANY)
execute_process(
  COMMAND "${GIT_EXECUTABLE}" -C "${RVO2_SOURCE_REAL}" rev-parse HEAD^{tree}
  OUTPUT_VARIABLE rvo2_tree OUTPUT_STRIP_TRAILING_WHITESPACE
  COMMAND_ERROR_IS_FATAL ANY)
execute_process(
  COMMAND "${GIT_EXECUTABLE}" -C "${RVO2_SOURCE_REAL}" status
    --porcelain=v1 --untracked-files=all
  OUTPUT_VARIABLE rvo2_status OUTPUT_STRIP_TRAILING_WHITESPACE
  COMMAND_ERROR_IS_FATAL ANY)
if(NOT rvo2_head STREQUAL "1c25b27258d191e26c2df83ff6e31abc8efbdaed"
    OR NOT rvo2_tree STREQUAL "754114c13a345b1d228871be7e85683e31b3af5d"
    OR NOT rvo2_status STREQUAL "")
  message(FATAL_ERROR "RVO2 checkout commit/tree/cleanliness differs")
endif()

include(CheckCXXSourceCompiles)
unset(ORCA_COMPILER_IS_SERIAL CACHE)
check_cxx_source_compiles(
  "#ifdef _OPENMP\n#error OpenMP must be disabled\n#endif\nint main() { return 0; }"
  ORCA_COMPILER_IS_SERIAL)
if(NOT ORCA_COMPILER_IS_SERIAL)
  message(FATAL_ERROR "The effective compiler flags enable OpenMP")
endif()

set(ORCA_INPUT_MANIFEST_SHA256 "smoke-only-unbound" CACHE STRING
  "Exact publication input manifest SHA-256; smoke-only-unbound forbids run mode")
string(LENGTH "${ORCA_INPUT_MANIFEST_SHA256}" orca_manifest_sha256_length)
if(NOT ORCA_INPUT_MANIFEST_SHA256 STREQUAL "smoke-only-unbound"
    AND (NOT orca_manifest_sha256_length EQUAL 64
      OR NOT ORCA_INPUT_MANIFEST_SHA256 MATCHES "^[0-9a-f]+$"))
  message(FATAL_ERROR "ORCA_INPUT_MANIFEST_SHA256 must be lowercase SHA-256")
endif()

set(BUILD_SHARED_LIBS OFF CACHE BOOL "" FORCE)
set(BUILD_EXAMPLES OFF CACHE BOOL "" FORCE)
set(BUILD_TESTING OFF CACHE BOOL "" FORCE)
set(BUILD_DOCUMENTATION OFF CACHE BOOL "" FORCE)
set(ENABLE_OPENMP OFF CACHE BOOL "" FORCE)
set(ENABLE_INTERPROCEDURAL_OPTIMIZATION OFF CACHE BOOL "" FORCE)
set(ENABLE_INSTALLATION OFF CACHE BOOL "" FORCE)
set(ENABLE_PACKAGING OFF CACHE BOOL "" FORCE)

add_subdirectory("${RVO2_SOURCE_REAL}" rvo2 EXCLUDE_FROM_ALL)

get_filename_component(REPOSITORY_ROOT "${CMAKE_CURRENT_LIST_DIR}/../.." REALPATH)
add_executable(dancers-orca-wrapper
  rvo2_runner.cpp
  "${REPOSITORY_ROOT}/src/Sha256.cpp")
target_compile_features(dancers-orca-wrapper PRIVATE cxx_std_17)
target_compile_definitions(dancers-orca-wrapper PRIVATE
  DANCERS_SHA256_FORCE_PORTABLE=1
  ORCA_INPUT_MANIFEST_SHA256="${ORCA_INPUT_MANIFEST_SHA256}"
  ORCA_RVO2_COMMIT="1c25b27258d191e26c2df83ff6e31abc8efbdaed"
  ORCA_RVO2_TREE="754114c13a345b1d228871be7e85683e31b3af5d")
target_include_directories(dancers-orca-wrapper PRIVATE
  "${REPOSITORY_ROOT}/inc")
target_include_directories(dancers-orca-wrapper SYSTEM PRIVATE
  "${REPOSITORY_ROOT}/thirdparty")
target_link_libraries(dancers-orca-wrapper PRIVATE RVO::RVO)

if(MSVC)
  target_compile_options(dancers-orca-wrapper PRIVATE /W4 /WX)
else()
  target_compile_options(dancers-orca-wrapper PRIVATE
    -Wall -Wextra -Wpedantic -Werror)
endif()

option(ORCA_WRAPPER_ENABLE_TESTS "Enable non-confirmatory wrapper smoke tests" ON)
if(ORCA_WRAPPER_ENABLE_TESTS)
  enable_testing()
  find_package(Python3 REQUIRED COMPONENTS Interpreter)
  add_test(
    NAME dancers-orca-wrapper-smoke
    COMMAND "${Python3_EXECUTABLE}" -m unittest
      tools.orca_baseline.tests.test_rvo2_wrapper -v)
  set_tests_properties(dancers-orca-wrapper-smoke PROPERTIES
    ENVIRONMENT "ORCA_WRAPPER_BINARY=$<TARGET_FILE:dancers-orca-wrapper>"
    WORKING_DIRECTORY "${REPOSITORY_ROOT}")
endif()
