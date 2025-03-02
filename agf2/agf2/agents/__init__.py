from . import endpoint_usage
from . import triage_agent
from . import patch_agent
from . import scanner

def register(subparsers):
    agent_parser = subparsers.add_parser('agent')
    agent_subparsers = agent_parser.add_subparsers()
    endpoint_usage.register(agent_subparsers)
    triage_agent.register(agent_subparsers)
    patch_agent.register(agent_subparsers)
    scanner.register(agent_subparsers)
