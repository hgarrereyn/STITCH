import argparse
import subprocess
import tempfile
import json
from typing import List
from .utils.info import ProjectInfo
from .utils.validator import validate_function
from .buildcmd import build_command, synth_command, build_full


def fn_build_full(args):
    info = ProjectInfo.model_validate_json(open(args.info).read())
    build_full(args.path, info)

def fn_build(args):
    info = ProjectInfo.model_validate_json(open(args.info).read())
    res = build_command(args.path, info, False, args.verbose)
    if res.returncode != 0:
        raise Exception(res.stderr)

def fn_build_external(args):
    info = ProjectInfo.model_validate_json(open(args.info).read())
    res = build_command(args.path, info, True, args.verbose)
    if res.returncode != 0:
        raise Exception(res.stderr)

def fn_dryrun(args):
    info = ProjectInfo.model_validate_json(open(args.info).read())
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_info = tmpdir + '/test.json'
        with open(tmp_info, 'w') as f:
            f.write(json.dumps({
                "endpoints": [
                    {
                        "name": "test",
                        "variables": [],
                        "template": "// pass"
                    }
                ]
            }))

        synth_command(tmpdir + '/test', info)
        build_command(tmpdir + '/test', info, True)

def fn_test_func(args):
    info = ProjectInfo.model_validate_json(open(args.info).read())
    validate_function(args.function, info, verbose=True)


def register(subparsers):
    parser_build_full = subparsers.add_parser('build-full')
    parser_build_full.add_argument('--info', type=str, default='info.json')
    parser_build_full.add_argument('path', type=str)
    parser_build_full.set_defaults(func=fn_build_full)

    parser_build = subparsers.add_parser('build-endpoint')
    parser_build.add_argument('--info', type=str, default='info.json')
    parser_build.add_argument('path', type=str)
    parser_build.add_argument('--verbose', action='store_true')
    parser_build.set_defaults(func=fn_build)

    parser_build_external = subparsers.add_parser('build-external')
    parser_build_external.add_argument('--info', type=str, default='info.json')
    parser_build_external.add_argument('path', type=str)
    parser_build_external.add_argument('--verbose', action='store_true')
    parser_build_external.set_defaults(func=fn_build_external)

    parser_build_external = subparsers.add_parser('dryrun')
    parser_build_external.add_argument('--info', type=str, default='info.json')
    parser_build_external.set_defaults(func=fn_dryrun)

    parser_build_external = subparsers.add_parser('test-func')
    parser_build_external.add_argument('--info', type=str, default='info.json')
    parser_build_external.add_argument('function', type=str)
    parser_build_external.set_defaults(func=fn_test_func)
