#pragma once

#include <Python.h>
#include "nozzle/nozzle_c.h"

#define MAX_HANDLES 256

typedef struct {
    NozzleSender *senders[MAX_HANDLES];
    NozzleReceiver *receivers[MAX_HANDLES];
    NozzleFrame *frames[MAX_HANDLES];
} nozzle_handle_store;

PyMODINIT_FUNC PyInit__nozzle_native(void);
