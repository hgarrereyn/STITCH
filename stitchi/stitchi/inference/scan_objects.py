from __future__ import annotations

from pydantic import BaseModel
import json
import os
from typing import List, Optional, TYPE_CHECKING

from ..data.metadata import UsageTracker
from .data import ObjectInfo
from .util import call_llm

if TYPE_CHECKING:
    from .ui import TokenProgress


PROMPT_SCAN_OBJECTS = open(os.path.join(os.path.dirname(__file__), 'prompts/scan_objects.md')).read()


class ObjectsResponse(BaseModel):
    objects: List[ObjectInfo]


def scan_objects(code: str, usage: UsageTracker, token_progress: Optional[TokenProgress] = None) -> List[ObjectInfo]:
    parsed: ObjectsResponse = call_llm(
        input=[
            {"role": "developer", "content": PROMPT_SCAN_OBJECTS},
            {"role": "user", "content": code},
        ],
        text_format=ObjectsResponse,
        usage=usage,
        task='scan-objects',
        reasoning={"effort": "low"},
        token_progress=token_progress,
    )
    return parsed.objects


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
