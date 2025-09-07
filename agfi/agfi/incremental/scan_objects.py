from pydantic import BaseModel
import os
from typing import List, Optional

from ..data.metadata import UsageTracker, UsageMetrics
from ..data.project_info import ProjectInfo
from ..build.validator import validate_object
from .data import ObjectStub

from langchain_openai import ChatOpenAI
from langchain_core.callbacks import get_usage_metadata_callback

SPEC_OBJECTS = open(os.path.join(os.path.dirname(__file__), 'spec_objects.md')).read()

PROMPT_SCAN_CHUNK = """
You are an expert C/C++ developer.

Your task is to examine the following C/C++ header file and list all the different public-facing custom object types that are defined in the file.
For each custom object, generate a detailed description, following the guidelines below.

Only list objects that are defined in the file; do not list objects that are only used in the file (and defined elsewhere).

[OBJECT_GUIDELINES]
{guidelines}
[/OBJECT_GUIDELINES]

[CODE]
{code}
[/CODE]
"""


class ObjectsResponse(BaseModel):
    objects: List[ObjectStub]


def scan_objects(model: str, code: str, usage: UsageTracker) -> List[ObjectStub]:
    llm = ChatOpenAI(model=model).with_structured_output(ObjectsResponse, strict=True)
    with get_usage_metadata_callback() as cb:
        res: ObjectsResponse = llm.invoke(PROMPT_SCAN_CHUNK.format(
            guidelines=SPEC_OBJECTS,
            code=code
        ))
        usage.record('scan-objects', UsageMetrics.from_usage(cb.usage_metadata))
    return res.objects


def _find_valid_object_type(info: ProjectInfo, object: ObjectStub) -> Optional[str]:
    """Find a valid object type for the given object."""
    if validate_object(object.type, info, True) is None:
        return object.type
    
    # Check if any of the aliases are valid
    for alias in object.aliases:
        if validate_object(alias, info, True) is None:
            return alias

    return None


def run_validate_object(info: ProjectInfo, obj: ObjectStub) -> List[ObjectStub]:
    type = _find_valid_object_type(info, obj)
    
    if type is None:
        return []
    
    return [ObjectStub(
        type=type,
        aliases=obj.aliases,
        description=obj.description,
        definition=obj.definition,
        direct_api_constructors=obj.direct_api_constructors,
        owned_by=obj.owned_by
    )]
# 