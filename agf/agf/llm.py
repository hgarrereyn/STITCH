from typing import List, Tuple
from pathlib import Path
import json
from openai import OpenAI

from .spec import Schema, Errors, Endpoint


PROMPTS = Path(__file__).parent / 'prompts'
SPEC = open(PROMPTS / 'spec.txt').read()


class LLM:
    def __init__(self, model: str, max_retries: int = 3):
        self.client = OpenAI()
        self.model = model
        self.max_retries = max_retries

    def query(self, messages: List[str], fmt):
        completion = self.client.beta.chat.completions.parse(
            model=self.model,
            messages=messages,
            response_format=fmt
        )
        return completion.choices[0].message.parsed

    def prompt_gen(self, code: str) -> Schema:
        '''Generate a list of endpoints from the given code.'''
        
        task = '### Task:\nUsing the given header file, generate an endpoint specification for **each API function** in the file. If a function has multiple valid implementations (for example optional arguments), generate an endpoint for each variant.'

        s = self.query([
            {"role": "system", "content": SPEC + '\n\n' + task},
            {"role": "user", "content": '// header:\n' + code}
        ], Schema)

        s.pprint()

        return s


    def prompt_fix(self, code: str, errors: Errors, avoid: List[str]) -> Schema:
        '''Fix the given erroring endpoints.'''
        
        task = '### Task:\nFix each of the given erroring endpoints. Follow the guidelines and examples above to ensure accuracy and completeness.'
        if len(avoid) > 0:
            task += f'\nDo not request variables with the following types: {avoid}'

        to_fix = {e.endpoint.name: json.loads(e.model_dump_json(indent=2)) for e in errors.errors}

        fixed = []

        for _ in range(self.max_retries):
            errs = json.dumps([to_fix[e] for e in to_fix], indent=2)

            s = self.query([
                {"role": "system", "content": SPEC + '\n\n' + task},
                {"role": "user", "content": '// header:\n' + code},
                {"role": "user", "content": errs}
            ], Schema)

            for e in s.endpoints:
                if e.name in to_fix:
                    del to_fix[e.name]
                    fixed.append(e)

            if not to_fix:
                break

        s = Schema(endpoints=fixed)
        s.pprint()

        return s


    def _prompt_rewrite_one(self, code: str, endpoint: Endpoint, avoid: List[str]) -> Endpoint:
        '''Rewrite the given endpoint.'''
        task = '### Task:\nRewrite the given endpoint to not use the following variables:\n'

        invalid_vars = [f'{v.name} ({v.typ})' for v in endpoint.variables if v.typ in avoid]
        task += f'\n{invalid_vars}'

        task += '\nThese variables should be replaced with logic embedded into the template. I.e. if a variable pointed to a struct, construct the struct directly in the template. Make sure all structures are fully and correctly initialized.'

        s = self.query([
            {"role": "system", "content": SPEC + '\n\n' + task},
            {"role": "user", "content": '// header:\n' + code},
            {"role": 'user', 'content': '// endpoint:\n' + endpoint.model_dump_json(indent=2)}
        ], Endpoint)

        return s

    def prompt_rewrite(self, code: str, schema: Schema, avoid: List[str]) -> Tuple[Schema, Schema]:
        '''Rewrite the given endpoints.'''
        unchanged = Schema(endpoints=[])
        rewritten = Schema(endpoints=[])

        for e in schema.endpoints:  
            needs_rewrite = any(v.typ in avoid for v in e.variables)
            if needs_rewrite:
                rewritten.endpoints.append(self._prompt_rewrite_one(code, e, avoid))
            else:
                unchanged.endpoints.append(e)

        rewritten.pprint()

        return rewritten, unchanged
