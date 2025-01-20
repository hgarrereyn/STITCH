from typing import Tuple, List
from multiprocessing import Pool
from threading import Thread

from .utils import find_missing_types
from .llm import LLM
from .spec import Errors, Schema, Endpoint, Error
from .builder import try_build


def register(subparsers):
    parser = subparsers.add_parser('loop', help='Run automatic synthesis loop')
    parser.add_argument('header', help='Path to header file')
    parser.add_argument('build_script', help='Path to build script')
    parser.add_argument('-c', '--config', help='Path to config file')
    parser.add_argument('-o', '--output', help='Path to output file')
    parser.add_argument('-m', '--max-main-steps', type=int, help='Maximum number of main loop steps', default=5)
    parser.add_argument('-f', '--max-fix-steps', type=int, help='Maximum number of fix loop steps', default=5)
    parser.add_argument('-v', '--verbose', action='store_true', help='Verbose output')
    parser.add_argument('--initial-json', help='Path to initial JSON file')
    parser.set_defaults(func=loop_synth)


class Looper(object):
    def __init__(self, config: str, build_script: str, max_main_steps: int, initial: str, max_fix_steps: int, verbose: bool):
        self.config = config
        self.build_script = build_script
        self.max_main_steps = max_main_steps
        self.initial = initial
        self.max_fix_steps = max_fix_steps
        self.verbose = verbose

        self.llm = LLM(model='gpt-4o-mini')

    # Inner loop keeps trying to build and fix errors until none of the endpoints have errors
    def inner_loop_fix(self, header: str, schema: Schema, avoid: List[str]=[]) -> Schema:
        output = Schema(endpoints=[])
        
        for i in range(self.max_fix_steps):
            print(f'[x] Fixing errors {i}')

            # Build and test
            valid, errors = build_and_test(self.build_script, schema, self.config, avoid, verbose=self.verbose)
            output.endpoints.extend(valid.endpoints)

            # If no errors, exit
            if len(errors.errors) == 0:
                print('[+] No errors found, exiting')
                break

            # Otherwise, fix errors and loop again
            schema = self.llm.prompt_fix(header, errors, avoid)

        return output

    def run(self, header) -> Schema:
        print(f'[+] Running looper')

        # Start by generating initial schema if not provided
        schema = None
        if self.initial:
            schema = Schema.model_validate_json(open(self.initial).read())
        else:
            schema = self.llm.prompt_gen(header)

        validated = Schema(endpoints=[])
        avoid = []

        for i in range(self.max_main_steps):
            print(f'[+] Outer loop step {i}')

            schema = self.inner_loop_fix(header, schema, avoid)

            # Merge and check for invalid types
            merged = Schema(endpoints=validated.endpoints + schema.endpoints)
            invalid_types = find_missing_types(merged, self.config)

            if self.verbose:
                print(f'[+] Invalid types: {invalid_types}')

            # If there are no invalid types, exit
            if len(invalid_types) == 0:
                print('[+] No invalid types found, exiting')
                validated = merged
                break

            # Otherwise, rewrite endpoints with invalid types and run again
            rewritten, unchanged = self.llm.prompt_rewrite(header, merged, invalid_types)

            # Update for next cycle
            validated = unchanged
            schema = rewritten
            avoid = invalid_types

        return validated


def loop_synth(args):
    looper = Looper(
        config=args.config,
        build_script=args.build_script,
        max_main_steps=args.max_main_steps,
        initial=args.initial_json,
        max_fix_steps=args.max_fix_steps,
        verbose=args.verbose
    )

    header = open(args.header).read()

    schema = looper.run(header)
    with open(args.output, 'w') as f:
        f.write(schema.model_dump_json(indent=4))


def build_and_test(build_script, schema: Schema, config=None, avoid: List[str]=[], verbose: bool=False) -> Tuple[Schema, Errors]:
    '''Build and test the given endpoints.
    Returns a tuple of (valid, errors)'''
    errors = []
    valid = []

    for endpoint in schema.endpoints:
        error = try_build(endpoint, build_script, avoid, config, verbose=verbose)
        if error:
            errors.append(error)
        else:
            valid.append(endpoint)

    return (Schema(endpoints=valid), Errors(errors=errors))
