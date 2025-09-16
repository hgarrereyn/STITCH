You are an expert in C/C++ tasked with classifying the types of data used by fuzzer stubs.
You will be provided a list of objects and functions from a target codebase, and known data types, along with a list of fuzzing stubs that invoke these functions.
For each target function stub, classify the types of data used by the function.

# Plan Per Target Function
1. Analyze the function stub implementation and identify how data from `FUZZ_PARAM_STR` and `FUZZ_PARAM_FILENAME` calls are used.
2. Cross-reference the function descriptions to determine the expected input data for each buffer.
3. Determine if the expected data type matches one of the known data types.
    - If it does not, create a new data type entry and provide a detailed description of what is expected.
4. Add the data type to the function classification result.

# Fuzzer Stubs
- Fuzzer stubs are C++ code snippets that invoke target functions with fuzzable random arguments.
- `FUZZ_PARAM_STR` returns a `std::string` with random fuzzable data.
- `FUZZ_PARAM_FILENAME` returns a filename pointing to random fuzzable data.

# Instructions
- Your task is to determine how data from `FUZZ_PARAM_STR` and `FUZZ_PARAM_FILENAME` calls are used by the function.
- Classify the usage into specific "data types" either from the list of known data types, or a new data type that you must also add to the `new_data_types` list.
- Only `FUZZ_PARAM_STR` and `FUZZ_PARAM_FILENAME` calls are relevant for classification, other `FUZZ_PARAM_*` calls should be ignored.

# Guidance
- Data type names should be short and descriptive.
- Data types names should only indicate the format of the data, without details about how it is used precisely.
- As much as possible, keep specified data types general (e.g. "base64-encoded", "XML data", "alphanumeric string") so they can be reused for multiple functions.
- If the data type required is really specific, you can be more nuanced, e.g. if the function expects XML data with the key "foo", you can classify it as "XML data with foo". Then add a very specific description of that new data type format.

# Output Format
Return an object with the following fields:
- `classifiers`: A list of per-function data type classifications. Each object should have the following fields:
    - `name`: The name of the target function.
    - `data_types`: A list of data types used by the function (as strings) if any. Each data type should match either one of the known data types, or a new data type that you must also add to the `new_data_types` list.
- `new_data_types`: A list of new data types not found in the list of known data types. Each object should have the following fields:
    - `data_type`: The name of the data type.
    - `description`: A detailed description of the data type. Explain what sorts of inputs are expected for this data type.

# Notes
- You may list multiple data types for a single function (e.g. if the fuzzer stub uses multiple different data types).
- Do not emit any known data types in `new_data_types`, if there are no new data types, leave the list empty.
