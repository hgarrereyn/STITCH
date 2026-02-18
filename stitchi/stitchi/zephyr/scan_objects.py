from pydantic import BaseModel
import json
import os
from typing import List, Optional

from ..data.metadata import UsageTracker, UsageTokens
from .data import ObjectInfo
from .util import get_model

from openai import OpenAI


PROMPT_SCAN_OBJECTS = open(os.path.join(os.path.dirname(__file__), 'prompts/scan_objects.md')).read()


class ObjectsResponse(BaseModel):
    objects: List[ObjectInfo]


def scan_objects(code: str, usage: UsageTracker) -> List[ObjectInfo]:
    client = OpenAI()
    model = get_model()

    response = client.responses.parse(
        model=model,
        reasoning={ "effort": "low" },
        input=[
            {
                "role": "developer",
                "content": PROMPT_SCAN_OBJECTS
            },
            {
                "role": "user",
                "content": code
            },
        ],
        text_format=ObjectsResponse
    )

    usage.record_model('scan-objects', model, UsageTokens.from_openai(response.usage))
    return response.output_parsed.objects


def main(args):
    code = open(args.file).read()
    usage = UsageTracker()
    objects = scan_objects(code, usage)
    print(usage.cost_by_task())

    objects = [x.model_dump() for x in objects]

    if args.output:
        with open(args.output, 'w') as f:
            f.write(json.dumps(objects, indent=2))
    else:
        print(json.dumps(objects, indent=2))


def register(subparsers):
    scan_parser = subparsers.add_parser('scan-objects')
    scan_parser.add_argument('--file', type=str, required=True)
    scan_parser.add_argument('-o', '--output', type=str, required=False)
    scan_parser.set_defaults(func=main)
