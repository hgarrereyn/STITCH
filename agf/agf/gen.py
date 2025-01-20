from argparse import ArgumentParser
from pathlib import Path
import re
import json

from pydantic import BaseModel
from typing import List, Union, Dict
from openai import OpenAI

from .colors import Colors


PROMPTS = Path(__file__).parent / 'prompts'


def pprint_template(template: str):
    # replace $name with colors
    template = re.sub(r'\$(\w+)', f'{Colors.LIGHT_BLUE}$\\1{Colors.END}', template)
    return template


class Variable(BaseModel):
    name: str
    dir: str
    typ: str

class Endpoint(BaseModel):
    name: str
    variables: List[Variable]
    template: str

class Schema(BaseModel):
    endpoints: List[Endpoint]


def print_schema(schema: Schema):
    for e in schema.endpoints:
        print(f'{Colors.BOLD}{e.name}{Colors.END}')
        for v in e.variables:
            print(f'{Colors.LIGHT_BLUE}{v.name}{Colors.END} {Colors.LIGHT_GREEN}[{v.dir}]{Colors.END} {v.typ}')
        print(f'{Colors.BOLD}Template{Colors.END}')
        print(pprint_template(e.template))
        print()


def gen_new(args):
    print('[*] Generating schema')

    spec = open(PROMPTS / 'spec.txt').read()
    task = open(PROMPTS / 'gen_no_example.txt').read()

    code = open(args.header).read()

    client = OpenAI()
    completion = client.beta.chat.completions.parse(
        model="gpt-4o-mini",
        messages=[
            {"role": "system", "content": spec},
            {"role": "system", "content": task},
            {"role": "user", "content": code}
        ],
        response_format=Schema
    )
    schema = completion.choices[0].message.parsed

    print_schema(schema)

    j = json.loads(schema.model_dump_json())

    h = args.header
    if args.include:
        h = args.include

    j['headers'] = [
        {
            'CppHeader': h
        }
    ]

    if args.output:
        with open(args.output, 'w') as f:
            f.write(json.dumps(j, indent=4))
            print(f'[+] Wrote schema to {args.output}')


def gen_fix(args):
    print('[*] Fixing schema')

    spec = open(PROMPTS / 'spec.txt').read()
    task = open(PROMPTS / 'fix_no_example.txt').read()

    code = open(args.header).read()
    errors = open(args.errors).read()

    # Add handling for invalid types
    invalid_types_prompt = ""
    if args.invalid_types:
        with open(args.invalid_types) as f:
            invalid_types = json.load(f)
            if invalid_types:
                invalid_types_str = ", ".join(invalid_types)
                invalid_types_prompt = f"\nAvoid requesting these types and instead embed them directly into the endpoint specification: {invalid_types_str}"

    client = OpenAI()
    completion = client.beta.chat.completions.parse(
        model="gpt-4o-mini",
        messages=[
            {"role": "system", "content": spec},
            {"role": "system", "content": task + invalid_types_prompt},
            {"role": "user", "content": '// Header\n' + code},
            {"role": "assistant", "content": '// Errors\n' + errors}
        ],
        response_format=Schema
    )
    schema = completion.choices[0].message.parsed

    print_schema(schema)

    j = json.loads(schema.model_dump_json())

    h = args.header
    if args.include:
        h = args.include

    j['headers'] = [
        {
            'CppHeader': h
        }
    ]

    if args.output:
        with open(args.output, 'w') as f:
            f.write(json.dumps(j, indent=4))
            print(f'[+] Wrote schema to {args.output}')


def rewrite_endpoint(endpoint, code, invalid_types):
    print(f'[*] Rewriting endpoint {endpoint["name"]}')
    
    invalid_vars = []
    for var in endpoint['variables']:
        if var['typ'] in invalid_types:
            invalid_vars.append(var)

    spec = open(PROMPTS / 'spec.txt').read()
    
    task = '## Task\nRewrite the endpoint without using the following variables:\n'
    for var in invalid_vars:
        task += f'{var["name"]} ({var["typ"]})\n'

    client = OpenAI()
    completion = client.beta.chat.completions.parse(
        model="gpt-4o-mini",
        messages=[
            {"role": "system", "content": spec},
            {"role": "system", "content": task},
            {"role": "user", "content": '// Header\n' + code},
            {"role": "user", "content": '// Endpoint\n' + json.dumps(endpoint, indent=2)}
        ],
        response_format=Schema
    )
    rewritten_schema = completion.choices[0].message.parsed

    print_schema(rewritten_schema)

    s = json.loads(rewritten_schema.model_dump_json())
    return s['endpoints'][0]


def gen_rewrite(args):
    print('[*] Rewriting schema')
    
    # Load the header code
    code = open(args.header).read()
    
    # Load the original schema
    with open(args.spec) as f:
        original_schema = json.load(f)
    
    # Load list of types to avoid
    with open(args.avoid) as f:
        invalid_types = json.load(f)

    # Track endpoints that need rewriting vs unchanged ones
    needs_rewrite = []
    unchanged = []
    
    # Check each endpoint for invalid types
    for endpoint in original_schema['endpoints']:
        has_invalid_type = False
        for var in endpoint['variables']:
            if var['typ'] in invalid_types:
                has_invalid_type = True
                needs_rewrite.append(endpoint)
                break
        if not has_invalid_type:
            unchanged.append(endpoint)
    
    # If there are endpoints to rewrite, send them to the LLM
    rewritten_schema = []
    for endpoint in needs_rewrite:
        rewritten_schema.append(rewrite_endpoint(endpoint, code, invalid_types))
    
    # Prepare final output
    final_schema = {
        'endpoints': [],
        'headers': [{'CppHeader': args.include if args.include else args.header}]
    }
    
    # Add rewritten endpoints if any
    if rewritten_schema:
        final_schema['endpoints'].extend(rewritten_schema)

    # Write unchanged endpoints to separate file if requested
    if args.unchanged and unchanged:
        unchanged_schema = {
            'endpoints': unchanged,
            'headers': [{'CppHeader': args.include if args.include else args.header}]
        }
        with open(args.unchanged, 'w') as f:
            json.dump(unchanged_schema, f, indent=4)
            print(f'[+] Wrote unchanged endpoints to {args.unchanged}')
    else:
        # If no unchanged file specified, include unchanged endpoints in main output
        final_schema['endpoints'].extend(unchanged)
    
    # Write final schema if output specified
    if args.output:
        with open(args.output, 'w') as f:
            json.dump(final_schema, f, indent=4)
            print(f'[+] Wrote rewritten schema to {args.output}')


def register(subparsers):
    gen_parser = subparsers.add_parser('gen')
    sub = gen_parser.add_subparsers()

    new_parser = sub.add_parser('new')
    new_parser.add_argument('header', type=str)
    new_parser.add_argument('-i', '--include', type=str, default=None)
    new_parser.add_argument('-o', '--output', type=str, default=None)
    new_parser.set_defaults(func=gen_new)

    fix_parser = sub.add_parser('fix')
    fix_parser.add_argument('header', type=str)
    fix_parser.add_argument('errors', type=str)
    fix_parser.add_argument('-i', '--include', type=str, default=None)
    fix_parser.add_argument('-o', '--output', type=str, default=None)
    fix_parser.add_argument('--invalid-types', type=str, help='JSON file containing list of types to avoid')
    fix_parser.set_defaults(func=gen_fix)

    rewrite_parser = sub.add_parser('rewrite')
    rewrite_parser.add_argument('header', type=str)
    rewrite_parser.add_argument('spec', type=str)
    rewrite_parser.add_argument('avoid', type=str)
    rewrite_parser.add_argument('-i', '--include', type=str, default=None)
    rewrite_parser.add_argument('-o', '--output', type=str, default=None)
    rewrite_parser.add_argument('-u', '--unchanged', type=str, default=None)
    rewrite_parser.set_defaults(func=gen_rewrite)
