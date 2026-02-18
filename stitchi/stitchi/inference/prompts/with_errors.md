# Task Context
- Use the existing stubs to help guide the generation of new stubs, pay attention to attribute usage.
- Use the error messages from failed stubs to guide the generation of correct new stubs.

# Unharnessable Functions
- For any target function that, after considering the existing stubs and errors, truly cannot be or meaningfully harnessed under the given constraints, do not invent a stub.
- Instead, mark it as unharnessable by adding an entry to the `unharnessable` list in your structured response with:
  - `name`: the exact function name from `target_functions`
  - `reason`: a single concise sentence explaining why this function cannot be harnessed (e.g., \"incompatible toolchain\" or \"requires external global state we cannot control\").
- Every function listed in `target_functions` must be accounted for either by:
  - A valid entry in the `functions` list with a proper stub, or
  - A corresponding entry in `unharnessable`.
- Prefer marking a function as unharnessable only when repeated compilation or validation failures are due to fundamental limitations (e.g., incompatible toolchain), not just minor stub mistakes.
