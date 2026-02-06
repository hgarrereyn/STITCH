[TASK]
Your task is to list every unique object that is defined in the header file.
Do not provide multiple entries for variations of the same object, instead list variations in the `aliases` field.
[/TASK]

[OVERVIEW]
An `object` is any public-facing custom type that is used to curry context or data across function calls.

For example, objects can be:
- custom structs (e.g. `struct foo { int a; int b; }`)
- custom enums (e.g. `enum myenum { MYVAL1, MYVAL2 }`)
- custom typedefs (e.g. `typedef int myid_t`)
- custom classes (e.g. `class myclass { int a; int b; }`)
- etc...

For each object collect the following information:
- `type`: see [CANONICAL_TYPE_GUIDELINES]
- `aliases`: a list of aliases for the object (e.g. if the object is `struct foo *`, aliases might include `foo *`, `FOO_PTR`, `FOO_P`, etc...)
- `description`: a detailed description of the object
- `definition`: the full C/C++ code definition of the object, as defined in the header file.
- `direct_api_constructors`: list of functions that directly construct this object (see [API_CONSTRUCTORS])
- `owned_by`: list of other objects that own this object (i.e. if the object is a managed field in some parent object).

In the description, address the following points:
- What is the purpose of the object?
- How should the object be instantiated or obtained? I.e. through an API call, directly by the user, by dereferencing some other field, etc...
- How should the object be cleaned up? I.e. what functions should be called to free the object and when is this necessary?
- What are the requirements on the object? I.e. what are the restrictions on the object's state, or the arguments to the functions that use the object?
[/OVERVIEW]

[API_CONSTRUCTORS]
API constructors are functions that can be invoked to obtain a new instance of a given object.
Typically these functions should either return the object (as a pointer) or initialize a pointer argument which will point to the object.
These functions will be invoked by a fuzzer in order to obtain new instances of this object.

Do **not** include functions that construct a parent object, or an object that contains this one.
Instead, you should include the object itself in the `owned_by` field.
[/API_CONSTRUCTORS]

[CANONICAL_TYPE_GUIDELINES]
The `type` field of the object should be a fully-qualified C/C++ type that can be used to declare a variable of that type.

It *must* be a pointer type. If the object is not already a pointer, (e.g. a typedef of an int), convert it to a pointer type.

Be careful with the following cases:

1. Opaque structs
```c
struct foo;
```
Use the pointer form: `struct foo *` as the raw form cannot be used to declare a variable.

2. Anonymous enums/unions with a typedef
```c
typedef enum { int a; int b; } myenum_t;
```
Use the typedef name: `myenum_t *`.

3. Typedefs
```c
typedef int myid_t;
```
Use the typedef name: `myid_t *`.

4. Function pointer types
```c
typedef void (*myfunc_t)(int, int);
```
Use the typedef name: `myfunc_t *` instead of the function pointer syntax (`void (*)(int, int)`).
[/CANONICAL_TYPE_GUIDELINES]

[FULL_EXAMPLE]
Input code:
```c
// Resource identifier type
typedef int myid_t;

// Used to specify parameters for bar creation
struct foo {
    int a;
    int b;
};

// enum representing the type of a bar
typedef enum bar_type {
    BAR_TYPE_A,
    BAR_TYPE_B,
} bar_type_t;

// Represents a bar.
// Only one bar can exist at a time.
struct bar;

typedef struct bar bar_t;
typedef struct bar *bar_p;

typedef struct child {
    int a;
    int b;
} child_t;

typedef struct parent {
    child_t *child;
} parent_t;

// Creates a new resource identifier
myid_t myid_create();

bar_p bar_create(struct foo *foo, bar_type_t type);
void bar_destroy(bar_p bar);

parent_t *parent_create();
void parent_destroy(parent_t *parent);

void edit_child(child_t *child);
```

Output objects:
```json
[
    {
        "type": "myid_t *",
        "aliases": ["myid_t"],
        "description": "Integer type used to identify a resource. Created by `myid_create`. Managed by the library.",
        "definition": "typedef int myid_t;",
        "direct_api_constructors": ["myid_create"],
        "owned_by": []
    },
    {
        "type": "struct foo *",
        "aliases": ["struct foo"],
        "description": "Struct used to specify parameters for bar creation. Contains two integers. Managed by the caller.",
        "definition": "struct foo { int a; int b; };",
        "direct_api_constructors": [],
        "owned_by": []
    },
    {
        "type": "bar_type_t *",
        "aliases": ["enum bar_type"],
        "description": "Enum representing the type of a bar. Used to specify the type of a bar when creating it.",
        "definition": "enum bar_type { BAR_TYPE_A, BAR_TYPE_B };",
        "direct_api_constructors": [],
        "owned_by": []
    },
    {
        "type": "struct bar *",
        "aliases": ["struct bar", "bar_t", "bar_p"],
        "description": "Opaque pointer to a bar. Only one bar can exist at a time. Created by `bar_create` and destroyed by `bar_destroy`.",
        "definition": "struct bar;",
        "direct_api_constructors": ["bar_create"],
        "owned_by": []
    },
    {
        "type": "struct parent *",
        "aliases": ["struct parent", "parent_t"],
        "description": "Pointer to a parent. Created by `parent_create` and destroyed by `parent_destroy`. Contains a child object.",
        "definition": "struct parent { child_t *child; };",
        "direct_api_constructors": ["parent_create"],
        "owned_by": []
    },
    {
        "type": "struct child *",
        "aliases": ["struct child", "child_t"],
        "description": "Pointer to a child. Owned by an enclosing parent object.",
        "definition": "struct child { int a; int b; };",
        "direct_api_constructors": [],
        "owned_by": ["parent"]
    }
]
```
[/FULL_EXAMPLE]
