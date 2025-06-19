import concurrent.futures
from typing import List

from langchain_openai import ChatOpenAI
from langchain_core.callbacks import get_usage_metadata_callback
from pydantic import BaseModel

from ..data.project_info import ProjectInfo
from ..data.metadata import FunctionStubMapping, SchemaMetadata, FunctionArgumentUsage, UsageMetrics, UsageTracker
from ..utils.colors import Colors
from ..utils.general import split_into_chunks
from ..build.validator import validate_function


PROMPT_MAP = """
You are provided with a list of objects and functions that were defined in a codebase.

Your job is to examine each function and generate a new FunctionStubMapping that describes the function's arguments and return value.

For each function:
1. Examine the function's signature and description.
2. For each argument (and the return value), determine:
    - `name`: the name of the argument (if any, or `retval` if it's the return value)
    - `sig_type`: the type of the argument as it appears in the signature
    - `canonical_type`: the matching base type (one of the provided object types), or "none"
    - `usage`: how the argument is used (one of the provided usage types `default`, `new`, `delete`)
3. Return the list of FunctionStubMappings.

[FINDING_CANONICAL_TYPE]
For each argument, find the canonical type that matches the `sig_type`, must be one of the provided `valid_canonical_types` or the string "none".
Note that there may not always be a canonical type that matches the `sig_type`.

Look through the list of aliases for each object to determine if the `sig_type` matches any of them.
Do **NOT** assign a canonical type if the `sig_type` does not match any of the aliases.

<example>
    <objects>
        <object>
            <type>struct foo*</type>
            <aliases>foo, struct foo*</aliases>
        </object>
        <object>
            <type>enum rcode</type>
            <aliases>RCODE, enum rcode</aliases>
        </object>
    </objects>
    <functions>
        <function>
            <name>create_foo</name>
            <signature>int create_foo(struct foo **f, int size, char *name)</signature>
            <description>Creates a new foo with a given size and name</description>
        </function>
        <function>
            <name>delete_foo</name>
            <signature>RCODE delete_foo(struct foo **f)</signature>
            <description>Deletes a foo</description>
        </function>
    </functions>
    <generated_mappings>
        <mapping>
            <name>create_foo</name>
            <arguments>
                <argument>
                    <name>f</name>
                    <sig_type>struct foo**</sig_type>
                    <canonical_type>struct foo*</canonical_type> // matches the alias with different pointer level
                    <usage>new</usage>
                </argument>
                <argument>
                    <name>size</name>
                    <sig_type>int</sig_type>
                    <canonical_type>none</canonical_type>
                    <usage>default</usage>
                </argument>
            </arguments>
            <return_value>
                <name>retval</name>
                <sig_type>int</sig_type>
                <canonical_type>none</canonical_type>
                <usage>default</usage>
            </return_value>
        </mapping>
        <mapping>
            <name>delete_foo</name>
            <arguments>
                <argument>
                    <name>f</name>
                    <sig_type>struct foo**</sig_type>
                    <canonical_type>struct foo*</canonical_type> // matches the alias with different pointer level
                    <usage>delete</usage>
                </argument>
            </arguments>
            <return_value>
                <name>retval</name>
                <sig_type>RCODE</sig_type>
                <canonical_type>enum rcode</canonical_type> // matches the alias
                <usage>default</usage>
            </return_value>
        </mapping>
    </generated_mappings>
</example>
[/FINDING_CANONICAL_TYPE]

[OBJECTS]
{objects}
[/OBJECTS]

[FUNCTIONS]
{functions}
[/FUNCTIONS]

[VALID_CANONICAL_TYPES]
{valid_canonical_types}
[/VALID_CANONICAL_TYPES]
"""


BATCH_SIZE = 40


class StubsResponse(BaseModel):
    functions: List[FunctionStubMapping]


def _validate_mapping(meta: SchemaMetadata, mapping: FunctionStubMapping) -> bool:
    """Ensure the mapping is valid."""
    canonical_types = {obj.type for obj in meta.objects}
    for arg in mapping.arguments:
        if arg.canonical_type == 'none':
            continue
        if arg.canonical_type in canonical_types:
            continue
        return False
    return True


def _map_functions(info: ProjectInfo, meta: SchemaMetadata, model: str, threads: int, usage: UsageTracker) -> List[FunctionStubMapping]:
    def log(msg: str):
        print(f'[{Colors.GREEN}MAP{Colors.END}] {msg}')
    
    functions_to_map = {x.name: x for x in meta.functions}
    validated = []

    log(f'Mapping {Colors.YELLOW}{len(functions_to_map)}{Colors.END} functions...')

    valid_canonical_types = [x.type for x in meta.objects]

    llm = ChatOpenAI(model=model).with_structured_output(StubsResponse, strict=True)

    out_funcs = []

    batches = []
    for idx in range(0, len(meta.functions), BATCH_SIZE):
        batch = meta.functions[idx:idx+BATCH_SIZE]
        batches.append((idx, batch))

    out_funcs = []

    def process_batch(args):
        idx, batch = args
        log(f'Mapping batch {idx // BATCH_SIZE + 1} of {(len(meta.functions) // BATCH_SIZE) + 1}...')
        with get_usage_metadata_callback() as cb:
            res = llm.invoke(PROMPT_MAP.format(objects=meta.objects, functions=batch, valid_canonical_types=valid_canonical_types))
            metrics = UsageMetrics.from_usage(cb.usage_metadata)
        return (idx, res.functions, metrics)

    with concurrent.futures.ThreadPoolExecutor(max_workers=threads) as executor:
        future_to_idx = {executor.submit(process_batch, args): args[0] for args in batches}
        results = []
        for future in concurrent.futures.as_completed(future_to_idx):
            idx = future_to_idx[future]
            try:
                batch_idx, batch_funcs, batch_metrics = future.result()
                out_funcs.extend(batch_funcs)
                usage.record('map-functions', batch_metrics)
                log(f'Found {len(out_funcs)} functions')
            except Exception as exc:
                log(f'Batch {idx // BATCH_SIZE + 1} generated an exception: {exc}')

    out_funcs = [x for x in out_funcs if x is not None]

    log(f'{Colors.GREEN}Validated {Colors.YELLOW}{len(out_funcs)}{Colors.END} functions...')

    for mapping in out_funcs:
        # retval usage is always new
        mapping.return_value.usage = FunctionArgumentUsage.NEW

        if mapping.name not in functions_to_map:
            log(f'{Colors.RED}Function {Colors.YELLOW}{mapping.name}{Colors.END} not found in metadata, skipping...')
            continue
        if _validate_mapping(meta, mapping):
            validated.append(mapping)
            functions_to_map.pop(mapping.name)
        else:
            log(f'Invalid mapping for {Colors.RED}{mapping.name}{Colors.END}, retrying...')

    log(f'{Colors.GREEN}Validated {Colors.YELLOW}{len(validated)}{Colors.END} functions...')
    log(f'{Colors.RED}Failed to validate {Colors.YELLOW}{len(functions_to_map)}{Colors.END} functions...')
    return validated


def main(args):
    info = ProjectInfo.model_validate_json(open(args.info).read())
    meta = SchemaMetadata.model_validate_json(open(args.meta).read())

    stubs = _map_functions(info, meta, args.model, args.threads, meta.usage)
    meta.mapping_functions = stubs

    if args.output:
        with open(args.output, 'w') as f:
            f.write(meta.model_dump_json(indent=2))
    else:
        print(meta.model_dump_json(indent=2))


def register(subparsers):
    parser = subparsers.add_parser('gen-stubs', help='Generate stubs for the codebase')
    parser.add_argument('--info', type=str, default='info.json', help='Path to the project info file')
    parser.add_argument('--meta', type=str, required=True, help='Path to the schema metadata file')
    parser.add_argument('--model', type=str, default='gpt-4.1-mini', help='Model to use for scanning')
    parser.add_argument('--output', type=str, default=None, help='Path to the output file')
    parser.add_argument('--threads', type=int, default=10, help='Number of threads to use for scanning')
    parser.set_defaults(func=main)
