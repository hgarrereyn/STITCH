import json
import subprocess
import tempfile
from dataclasses import dataclass
from typing import Optional

from .spec import Schema, Endpoint, Error
from .colors import Colors


def try_build_external(endpoint: Endpoint, build_script_external: str, config: str) -> Optional[Error]:
    
    config = json.loads(open(config).read())

    code = ''
    code += '#include <cstdio>\n'
    code += '#include <cstdlib>\n'
    code += '#include <cstdint>\n'
    code += '#include <cstring>\n'

    for header in config['headers']:
        if 'CHeader' in header:
            code += 'extern "C" {\n'
            code += '#include "%s"\n' % header['CHeader']
            code += '}\n'
        elif 'CppHeader' in header:
            code += '#include "%s"\n' % header['CppHeader']

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

        res = subprocess.run([build_script_external, path], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        if res.returncode != 0:
            return Error(
                endpoint=endpoint,
                err_type='compilation',
                error=res.stderr.decode()
            )
        return None


def try_build(endpoint: Endpoint, build_script, invalid_types=None, config=None, verbose: bool=False) -> Optional[Error]:
    with tempfile.TemporaryDirectory() as tmpdir:
        f_schema = '%s/schema.json' % tmpdir
        f_harness = '%s/fuzz.cpp' % tmpdir

        with open(f_schema, 'w') as f:
            mod_schema = Schema(endpoints=[endpoint])
            f.write(mod_schema.model_dump_json(indent=4))

        # Prepare the gf_synth command
        synth_command = ['gf_synth', f_schema, '-o', f_harness]
        if config:
            synth_command.extend(['--config', config])
        if invalid_types:
            f_invalid_types = '%s/invalid_types.json' % tmpdir
            with open(f_invalid_types, 'w') as f:
                f.write(json.dumps(invalid_types, indent=4))
            synth_command.extend(['--invalid-types', f_invalid_types])

        res = subprocess.run(synth_command, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        if res.returncode != 0:
            if verbose:
                print(f'  Building {Colors.YELLOW}{endpoint.name}{Colors.END}...', end=' ')
                print(f'{Colors.RED}FAIL (Synthesis){Colors.END}')
                print(res.stderr.decode(), flush=True)
            return Error(
                endpoint=endpoint,
                err_type='synthesis',
                error=res.stderr.decode()
            )

        # Try to build
        res = subprocess.run([build_script, '%s/fuzz' % tmpdir], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        if res.returncode != 0:
            if verbose:
                print(f'  Building {Colors.YELLOW}{endpoint.name}{Colors.END}...', end=' ')
                print(f'{Colors.RED}FAIL (Compilation){Colors.END}')
                print(res.stderr.decode(), flush=True)
            return Error(
                endpoint=endpoint,
                err_type='compilation',
                error=res.stderr.decode()
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
