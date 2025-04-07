from typing import Union, Annotated, Optional, Literal, List, Tuple
from pydantic import BaseModel
from langgraph.graph import StateGraph, START, END
from langchain_openai import ChatOpenAI
import operator
from langchain_core.tools import tool
from pathlib import Path
from langgraph.prebuilt import ToolNode
from ..utils.sqlitecache import SQLiteCacheFixed
from langchain.globals import set_llm_cache
import subprocess
import tempfile
import random

from ..utils.search import get_example_usage
from ..rag.rag import RAG
from ..utils import repro
from ..utils.colors import Colors
from ..utils.info import ProjectInfo


class FilterAttempt(BaseModel):
    filter_function: str
    explanation: str
    summary_line: str

class FilterResult(BaseModel):
    filter_attempt: FilterAttempt
    precision: float
    recall: float
    f1_score: float

    def pprint(self):
        print(f"{Colors.BOLD}Filter Result:{Colors.END}")
        print(f"{Colors.YELLOW}Filter:{Colors.END}\n{self.filter_attempt.filter_function}")
        print(f"{Colors.YELLOW}Explanation:{Colors.END}\n{self.filter_attempt.explanation}")
        print(f"{Colors.YELLOW}Summary Line:{Colors.END}\n{self.filter_attempt.summary_line}")
        print(f"{Colors.YELLOW}Precision:{Colors.END} {self.precision}")
        print(f"{Colors.YELLOW}Recall:{Colors.END} {self.recall}")
        print(f"{Colors.YELLOW}F1 Score:{Colors.END} {self.f1_score}")

class PatchSequenceAgentState(BaseModel):
    info: ProjectInfo
    model: str
    schema: str  # The fuzzer schema
    analysis: str  # Analysis of the crash
    testcase: str  # The crashing testcase
    fuzzer: str  # The fuzzer
    ex_valid: str  # Path containing valid testcases
    ex_invalid: str  # Path containing invalid testcases

    # Message history
    messages: Annotated[list, operator.add]

    # Output
    best_filter: Optional[FilterResult] = None

    # Internal
    last_filter_attempt: Optional[FilterAttempt] = None
    regenerate_attempts: int = 5
    filter_false_positives: List[str] = []
    filter_false_negatives: List[str] = []
    filter_error: Optional[str] = None
    filter_results: Annotated[List[FilterResult], operator.add] = []

FIX_SEQUENCE_PROMPT = """
Now that you have identified the relevant endpoints, write a filter function in Python that will run on every test case to determine if it should be run.

The filter function accepts a `Testcase` object:
```py
@dataclass
class Variable:
    type: str
    id: int

@dataclass
class Endpoint:
    name: str
    variables: Dict[str, Variable]

@dataclass
class Testcase:
    endpoints: List[Endpoint]
```

The filter function should return `True` if the test case should be run, and `False` otherwise.

Name the function `graphfuzz_filter`.

Tips:
- Ensure that you check all relevant endpoints (and any others that cover similar functionality)
- Be careful about tracking the correct objects and their lifetimes
- Do not make assumptions about testcase structure or ids (they are generated randomly)
- The filter function should be concise and to the point

Also include an explanation of the filter function and a concise summary line that describes what the filter does in a single sentence.
""".strip()


def test_filter(fuzzer, filter_function, testcase) -> bool | str:
    res = subprocess.run([
        fuzzer,
        '--test-filter', str(filter_function),
        '--test-filter-input', str(testcase)
    ], capture_output=True, text=True, check=True)
    stdout = res.stdout.strip()
    if stdout == '1':
        return True
    elif stdout == '0':
        return False
    else:
        return res.stderr

def py_repr(fuzzer, testcase) -> str:
    res = subprocess.run([
        fuzzer,
        '--to-py-str-repr', testcase
    ], capture_output=True, text=True, check=True)
    return res.stdout.strip()


def initialize_filter(state: PatchSequenceAgentState) -> dict:
    llm = ChatOpenAI(model=state.model)
    structured_llm = llm.with_structured_output(FilterAttempt)

    # Read valid and invalid testcases
    valid_testcases = [x for x in Path(state.ex_valid).glob('*') if x.is_file() and not x.name.startswith('.')]
    invalid_testcases = [x for x in Path(state.ex_invalid).glob('*') if x.is_file() and not x.name.startswith('.')]

    # Randomly sample up to 20 valid and 20 invalid testcases
    if len(valid_testcases) > 20:
        valid_testcases = random.sample(valid_testcases, 20)
    if len(invalid_testcases) > 20:
        invalid_testcases = random.sample(invalid_testcases, 20)

    valid_repr = [py_repr(state.fuzzer, x) for x in valid_testcases]
    invalid_repr = [py_repr(state.fuzzer, x) for x in invalid_testcases]

    examples = f'''
Here are examples of valid and invalid testcases. The filter function should return `True` for all valid testcases, and `False` for all invalid testcases.

```py
valid = [{', '.join(valid_repr)}]
invalid = [{', '.join(invalid_repr)}]
```
    '''

    m = [
        {
            'role': 'user',
            'content': FIX_SEQUENCE_PROMPT
        },
        {
            'role': 'user',
            'content': examples
        }
    ]
    res = structured_llm.invoke(state.messages + m)

    return {
        "last_filter_attempt": res,
        "messages": m + [{
            "role": "assistant",
            "content": res.model_dump_json(),
        }]
    }

def regenerate_filter(state: PatchSequenceAgentState) -> dict:
    """Regenerate the filter."""
    print('[*] Regenerating filter')
    llm = ChatOpenAI(model=state.model)
    structured_llm = llm.with_structured_output(FilterAttempt)

    if state.filter_error is not None:
        regenerate_prompt = f'''
The generated filter was not perfect.

It threw an error:
```
{state.filter_error}
```

Please fix the filter function.
        '''
    else:
        false_positives = [py_repr(state.fuzzer, x) for x in state.filter_false_positives]
        false_negatives = [py_repr(state.fuzzer, x) for x in state.filter_false_negatives]

        regenerate_prompt = f'''
The generated filter was not perfect.

It incorrectly filtered out these (valid) testcases:
```py
valid = [{', '.join(false_positives)}]
```

It failed to filter out these (invalid) testcases:
```py
invalid = [{', '.join(false_negatives)}]
```

Please fix the filter function.
        '''

    m = [
        {
            'role': 'user',
            'content': regenerate_prompt
        }
    ]
    res = structured_llm.invoke(state.messages + m)

    return {
        "last_filter_attempt": res,
        "messages": m + [{
            "role": "assistant",
            "content": res.model_dump_json(),
        }],
        "regenerate_attempts": state.regenerate_attempts - 1,
    }

def validate_filter(state: PatchSequenceAgentState) -> dict:
    """Validate the filter."""
    print('[*] Validating filter')

    # Read valid and invalid testcases
    valid_testcases = [x for x in Path(state.ex_valid).glob('*') if x.is_file() and not x.name.startswith('.')]
    invalid_testcases = [x for x in Path(state.ex_invalid).glob('*') if x.is_file() and not x.name.startswith('.')]

    false_positives = []
    false_negatives = []

    filter_error = None

    with tempfile.TemporaryDirectory() as tmpdir:
        # Write filter function to file
        filter_path = Path(tmpdir) / 'filter.py'
        with open(filter_path, 'w') as f:
            f.write(state.last_filter_attempt.filter_function)

        print('Filter function:')
        print('-' * 40)
        print(open(filter_path).read())
        print('-' * 40)

        tp = 0 # crash skipped correctly
        fp = 0 # queue entry skipped incorrectly
        tn = 0 # queue not skipped correctly
        fn = 0 # crash not skipped incorrectly
        err = 0

        # Run fuzzer on valid testcases
        print(f'Running fuzzer on {len(valid_testcases)} valid testcases ...')
        for testcase in valid_testcases:
            print(f'Checking {testcase} ... ', end='')
            res = test_filter(state.fuzzer, filter_path, testcase)
            if type(res) is str:
                filter_error = res
                print(f'{Colors.RED}ERROR{Colors.END}')
                print(res)
                err += 1
            elif res:
                print(f'{Colors.GREEN}RUN{Colors.END}')
                tn += 1
            else:
                print(f'{Colors.RED}SKIP{Colors.END}')
                false_positives.append(str(testcase))
                fp += 1

        # Run fuzzer on invalid testcases
        print(f'Running fuzzer on {len(invalid_testcases)} invalid testcases ...')
        for testcase in invalid_testcases:
            print(f'Checking {testcase} ... ', end='')
            res = test_filter(state.fuzzer, filter_path, testcase)
            if type(res) is str:
                filter_error = res
                print(f'{Colors.RED}ERROR{Colors.END}')
                print(res)
                err += 1
            elif res:
                print(f'{Colors.RED}RUN{Colors.END}')
                false_negatives.append(str(testcase))
                fn += 1
            else:
                print(f'{Colors.GREEN}SKIP{Colors.END}')
                tp += 1

    print(f'{Colors.YELLOW}TP:{Colors.END} {tp}')
    print(f'{Colors.YELLOW}FP:{Colors.END} {fp}')
    print(f'{Colors.YELLOW}TN:{Colors.END} {tn}')
    print(f'{Colors.YELLOW}FN:{Colors.END} {fn}')
    print(f'{Colors.YELLOW}ERR:{Colors.END} {err}')

    # Calculate precision and recall
    precision = (tp / (tp + fp)) if (tp + fp) > 0 else 0
    recall = (tp / (tp + fn)) if (tp + fn) > 0 else 0
    print(f'{Colors.YELLOW}Precision:{Colors.END} {precision}')
    print(f'{Colors.YELLOW}Recall:{Colors.END} {recall}')

    # Calculate F1 score
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) > 0 else 0
    print(f'{Colors.YELLOW}F1 Score:{Colors.END} {f1}')

    # If there are any errors, score is 0
    if err > 0:
        f1 = 0

    return {
        "filter_false_positives": false_positives,
        "filter_false_negatives": false_negatives,
        "filter_error": filter_error,
        "filter_results": [FilterResult(filter_attempt=state.last_filter_attempt, precision=precision, recall=recall, f1_score=f1)],
    }

def check_regenerate(state: PatchSequenceAgentState) -> Literal['regenerate_filter', 'finalize_filter']:
    if state.regenerate_attempts <= 0:
        return 'finalize_filter'

    if state.filter_error is not None or len(state.filter_false_positives) > 0 or len(state.filter_false_negatives) > 0:
        return 'regenerate_filter'
    else:
        return 'finalize_filter'

def finalize_filter(state: PatchSequenceAgentState) -> dict:
    # Find the best filter
    best_filter = max(state.filter_results, key=lambda x: x.f1_score)

    print(f'{Colors.YELLOW}Best Filter:{Colors.END}')
    best_filter.pprint()

    if best_filter.f1_score > 0.9:
        return {
            "best_filter": best_filter,
        }
    else:
        return {
            "best_filter": None,
        }

def build_sequence_agent():
    workflow = StateGraph(PatchSequenceAgentState)

    # Add nodes
    workflow.add_node('initialize_filter', initialize_filter)
    workflow.add_node('regenerate_filter', regenerate_filter)
    workflow.add_node('validate_filter', validate_filter)
    workflow.add_node('finalize_filter', finalize_filter)

    # Add edges
    workflow.add_edge('initialize_filter', 'validate_filter')
    workflow.add_edge('regenerate_filter', 'validate_filter')
    workflow.add_conditional_edges('validate_filter', check_regenerate)
    workflow.add_edge('finalize_filter', END)

    workflow.set_entry_point('initialize_filter')
    
    return workflow.compile()

def run_patch_sequence_agent(info: ProjectInfo, model: str, schema: str, analysis: str, testcase: str, fuzzer: str, ex_valid: str, ex_invalid: str) -> FilterResult:
    state = PatchSequenceAgentState(
        info=info,
        model=model,
        schema=schema,
        analysis=analysis,
        testcase=testcase,
        fuzzer=fuzzer,
        ex_valid=ex_valid,
        ex_invalid=ex_invalid,
        messages=[],
    )
    graph = build_sequence_agent()
    res = graph.invoke(state)
    return res['best_filter']
