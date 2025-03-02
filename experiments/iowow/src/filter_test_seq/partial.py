def graphfuzz_filter(testcase: Testcase) -> bool:
    # Track ids for IWDB and IWKV instances
    db_id = None
    iwkv_id = None

    for endpoint in testcase.endpoints:
        # Determine the endpoint type
        if endpoint.name == 'new IWKV':
            iwkv_id = endpoint.variables['$object'].id
        elif endpoint.name == 'new IWDB':
            # If a new IWDB is created and there's an IWKV id, track its id
            if iwkv_id is not None:
                db_id = endpoint.variables['$object'].id
        elif endpoint.name == 'iwkv_close':
            # Ensure that iwkv_close should correspond to the iwkv_id
            if iwkv_id is None or endpoint.variables['$iwkvp'].id != iwkv_id:
                return False
        elif endpoint.name == 'iwkv_db_destroy':
            # Ensure that iwkv_db_destroy should only be called for the last IWDB created
            if db_id is not None and endpoint.variables['$dbp'].id == db_id:
                # After calling db_destroy, we shouldn't see db_id again
                db_id = None  # This effectively marks it as destroyed
        elif endpoint.name == 'delete IWDB':
            # Check if there's an attempt to delete the database after destruction
            if db_id is None or endpoint.variables['$object'].id != db_id:
                return False

    # If we get through the loop without finding any issues
    return True
