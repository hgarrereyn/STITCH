

def graphfuzz_filter(testcase):
    has_close = False
    for i in testcase.execution_order:
        if testcase.endpoints[i].name in ["iwkv_close", "delete IWKV"]:
            has_close = True
        if testcase.endpoints[i].name in ["iwkv_db_destroy", "delete IWDB"]:
            if has_close:
                return False
    return True
