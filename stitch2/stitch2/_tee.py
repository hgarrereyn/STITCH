import os
import sys


def tee_log(path: str):
    log = open(path, "w")
    os.dup2(log.fileno(), sys.stdout.fileno())
    os.dup2(log.fileno(), sys.stderr.fileno())
    return log
