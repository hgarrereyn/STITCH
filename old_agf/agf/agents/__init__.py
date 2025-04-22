from . import gen_endpoint
# from . import triage_agent
# from . import patch_agent
from . import scanner
from . import minimizer_agent

def register(subparsers):
    agent_parser = subparsers.add_parser('agent')
    agent_subparsers = agent_parser.add_subparsers()
    gen_endpoint.register(agent_subparsers)
    # triage_agent.register(agent_subparsers)
    # patch_agent.register(agent_subparsers)
    # old_scanner.register(agent_subparsers)
    minimizer_agent.register(agent_subparsers)
