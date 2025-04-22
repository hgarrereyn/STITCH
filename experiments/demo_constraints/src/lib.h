

typedef struct {
    int refcount;
    int data;
} obj_t;


// Initialize the API
void obj_init_api(void) {
    // do nothing
}

// Initialize an object, with a refcount of 1
obj_t *obj_create(int data) {
    obj_t *obj = (obj_t *)malloc(sizeof(obj_t));
    obj->refcount = 1;
    obj->data = data;
    return obj;
}

// Increment the refcount of an object
void obj_ref(obj_t *obj) {
    obj->refcount++;
}

// Decrement the refcount of an object
void obj_unref(obj_t *obj) {
    obj->refcount--;
}

// Delete an object (refcount must be 1)
void obj_delete(obj_t *obj) {
    if (obj->refcount != 1) {
        __builtin_trap();
    }
    free(obj);
}

// Shallow copy an object, copy is only valid until obj is deleted
void obj_clone(obj_t **obj_copy, obj_t *obj) {
    *obj_copy = obj;
}

// Get the data of an object
int obj_get_data(obj_t *obj) {
    return obj->data;
}

// Set the data of an object
void obj_set_data(obj_t *obj, int data) {
    obj->data = data;
}
