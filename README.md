
Agent components:

- autoschema (header -> endpoint[])
   - `scanner` -- identify the functions and objects in a header file
   - `gen_stub` -- generate stub(s) for a function given list of objects
   - `gen_endpoint` -- generate an endpoint for a given stub

- triage (crash -> analysis)

- patch
   - `assessment` -- identify if fix should be endpoint or sequence
   - `fix_endpoint` -- generate a revised endpoint for a bug
   - `fix_sequence` -- generate a filter for test cases
