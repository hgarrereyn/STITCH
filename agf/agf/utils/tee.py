import os, sys

def tee_log(path: str):
    log = open(path, 'w')
    # Duplicate log.fileno() onto the process’s stdout (1) and stderr (2)
    os.dup2(log.fileno(), sys.stdout.fileno())
    os.dup2(log.fileno(), sys.stderr.fileno())
    return log
