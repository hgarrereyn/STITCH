import json
import subprocess
import tempfile
from dataclasses import dataclass
from typing import Optional

from .spec import Schema, Endpoint, Error
from .colors import Colors
from ..utils.info import ProjectInfo
from ..build import build_command, synth_command


def try_build_external(endpoint: Endpoint, info: ProjectInfo) -> Optional[Error]:
    code = ''
    code += '#include <cstdio>\n'
    code += '#include <cstdlib>\n'
    code += '#include <cstdint>\n'
    code += '#include <cstring>\n'

    if info.codegen.defines:
        for define in info.codegen.defines:
            code += '#define %s %s\n' % (define.name, define.value)

    for header in info.codegen.headers:
        if header.language == 'c':
            code += 'extern "C" {\n'
            code += '#include "%s"\n' % header.name
            code += '}\n'
        elif header.language == 'cpp':
            code += '#include "%s"\n' % header.name

    code += '#include "gf_util.h"\n'

    # Stub main
    code += 'int main() { return 0; }\n'

    # Endpoint
    code += endpoint.to_function()

    print('-' * 100)
    print(code)
    print('-' * 100)

    with tempfile.TemporaryDirectory() as tmpdir:
        f_code = '%s/code.cpp' % tmpdir
        with open(f_code, 'w') as f:
            f.write(code)

        path = '%s/code' % tmpdir

        res = build_command(path, info, True)
        if res.returncode != 0:
            return Error(
                endpoint=endpoint,
                err_type='compilation',
                error=res.stderr
            )
        return None


def try_build(endpoint: Endpoint, info: ProjectInfo, verbose: bool=False) -> Optional[Error]:
    with tempfile.TemporaryDirectory() as tmpdir:
        f_schema = tmpdir + '/fuzz.json'
        f_harness = tmpdir + '/fuzz.cpp'
        f_path = tmpdir + '/fuzz'

        with open(f_schema, 'w') as f:
            mod_schema = Schema(endpoints=[endpoint])
            f.write(mod_schema.model_dump_json(indent=4))

        res = synth_command(f_path, info)
        if res.returncode != 0:
            if verbose:
                print(f'  Building {Colors.YELLOW}{endpoint.name}{Colors.END}...', end=' ')
                print(f'{Colors.RED}FAIL (Synthesis){Colors.END}')
                print(res.stderr, flush=True)
            return Error(
                endpoint=endpoint,
                err_type='synthesis',
                error=res.stderr
            )

        # Try to build
        res = build_command(f_path, info, False)
        if res.returncode != 0:
            if verbose:
                print(f'  Building {Colors.YELLOW}{endpoint.name}{Colors.END}...', end=' ')
                print(f'{Colors.RED}FAIL (Compilation){Colors.END}')
                print(res.stderr, flush=True)
            return Error(
                endpoint=endpoint,
                err_type='compilation',
                error=res.stderr
            )

        if verbose:
            print(f'  Building {Colors.YELLOW}{endpoint.name}{Colors.END}...', end=' ')
            print(f'{Colors.GREEN}SUCCESS{Colors.END}', flush=True)

        return None


def main(args):
    schema = Schema.model_validate_json(open(args.schema).read())

    print('[*] Found %d endpoints' % len(schema.endpoints))

    valid = []
    errors = []

    for e in schema.endpoints:
        print(f'[*] Trying {Colors.YELLOW}%s{Colors.END}' % e.name, end='... ')
        err = try_build(e, args.build_script, args.invalid_types, args.config)
        print(f'{Colors.GREEN}SUCCESS{Colors.END}' if err is None else f'{Colors.RED}FAIL{Colors.END}')
        if err:
            errors.append(err)
        else:
            valid.append(e)

    print('[*] Found %d valid endpoints' % len(valid))

    if args.output:
        valid_schema = schema.copy()
        valid_schema['endpoints'] = valid
        with open(args.output, 'w') as f:
            f.write(json.dumps(valid_schema, indent=4))

    if args.errors:
        with open(args.errors, 'w') as f:
            f.write(json.dumps(errors, indent=4))


def build_endpoint(args):
    endpoint = Endpoint.model_validate_json(open(args.endpoint).read())
    res = try_build(endpoint, args.build_script, config=args.config)
    if res:
        print(f'{Colors.RED}FAIL{Colors.END}')
        print(res.error)
    else:
        print(f'{Colors.GREEN}SUCCESS{Colors.END}')


def register(subparsers):
    parser = subparsers.add_parser('build')
    parser.add_argument('schema', type=str)
    parser.add_argument('build_script', type=str)
    parser.add_argument('-c', '--config', type=str, default=None)
    parser.add_argument('-o', '--output', type=str, default=None)
    parser.add_argument('-e', '--errors', type=str, default=None)
    parser.add_argument('--invalid-types', type=str)
    parser.set_defaults(func=main)

    # build a single endpoint
    parser = subparsers.add_parser('build-endpoint')
    parser.add_argument('endpoint', type=str)
    parser.add_argument('build_script', type=str)
    parser.add_argument('-c', '--config', type=str, default=None)
    parser.set_defaults(func=build_endpoint)
