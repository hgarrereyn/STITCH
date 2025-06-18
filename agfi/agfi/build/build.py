import tempfile
import json

from .buildcmd import build_command, synth_command, build_full
from ..data.project_info import ProjectInfo
from .validator import validate_function, validate_object


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
    if validate_function(args.function, info, verbose=True) is not None:
        exit(1)


def fn_test_obj(args):
    info = ProjectInfo.model_validate_json(open(args.info).read())
    if validate_object(args.object, info, verbose=True) is not None:
        exit(1)


def register(subparsers):
    sub = subparsers.add_parser('build')
    sub_sub = sub.add_subparsers()

    parser_build_full = sub_sub.add_parser('full', help='Build the full fuzzer')
    parser_build_full.add_argument('--info', type=str, default='info.json')
    parser_build_full.add_argument('path', type=str)
    parser_build_full.set_defaults(func=fn_build_full)

    parser_build = sub_sub.add_parser('endpoint', help='Build a single endpoint')
    parser_build.add_argument('--info', type=str, default='info.json')
    parser_build.add_argument('path', type=str)
    parser_build.add_argument('--verbose', action='store_true')
    parser_build.set_defaults(func=fn_build)

    parser_build_external = sub_sub.add_parser('external', help='Build an external test case')
    parser_build_external.add_argument('--info', type=str, default='info.json')
    parser_build_external.add_argument('path', type=str)
    parser_build_external.add_argument('--verbose', action='store_true')
    parser_build_external.set_defaults(func=fn_build_external)

    parser_dryrun = sub_sub.add_parser('dryrun', help='Dry run the build config')
    parser_dryrun.add_argument('--info', type=str, default='info.json')
    parser_dryrun.set_defaults(func=fn_dryrun)

    parser_test_func = sub_sub.add_parser('test-func', help='Test a function')
    parser_test_func.add_argument('--info', type=str, default='info.json')
    parser_test_func.add_argument('function', type=str)
    parser_test_func.set_defaults(func=fn_test_func)

    parser_test_obj = sub_sub.add_parser('test-obj', help='Test an object')
    parser_test_obj.add_argument('--info', type=str, default='info.json')
    parser_test_obj.add_argument('object', type=str)
    parser_test_obj.set_defaults(func=fn_test_obj)
