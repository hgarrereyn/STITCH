def graphfuzz_filter(testcase):
    # Track the status of created and deleted objects
    object_lifetimes = {'IWKV': {}, 'IWDB': {}}

    for endpoint in testcase.endpoints:
        endpoint_name = endpoint.name
        variables = endpoint.variables
        ids = {var_name: var.id for var_name, var in variables.items()}

        if endpoint_name == 'new IWKV':
            # Create a new IWKV object
            object_id = ids['$object']
            object_lifetimes['IWKV'][object_id] = 'created'

        elif endpoint_name == 'new IWDB':
            # Create a new IWDB object
            object_id = ids['$object']
            object_lifetimes['IWDB'][object_id] = 'created'

        elif endpoint_name == 'iwkv_close':
            # Close an IWKV object
            object_id = ids['$iwkvp']
            if object_id in object_lifetimes['IWKV']:
                if object_lifetimes['IWKV'][object_id] != 'created':
                    return False  # Cannot close an uncreated or already closed IWKV
                object_lifetimes['IWKV'][object_id] = 'closed'

        elif endpoint_name in ['iwkv_db_destroy', 'delete IWDB']:
            # Destroy or delete an IWDB object
            object_id = ids['$object']
            if object_id in object_lifetimes['IWDB']:
                del object_lifetimes['IWDB'][object_id]
            else:
                return False  # Trying to destroy a non-existent IWDB

        elif endpoint_name == 'iwkv_db':
            # When creating an IWDB from IWKV
            iwkv_id = ids['$iwkv']
            db_id = ids['$dbp']
            if iwkv_id in object_lifetimes['IWKV'] and object_lifetimes['IWKV'][iwkv_id] == 'created':
                object_lifetimes['IWDB'][db_id] = 'created'
            else:
                return False  # IWDB cannot be created if IWKV is not properly created

        # Check before closing IWKV if any IWDB objects exist
        if object_name == 'iwkv_close':
            if object_lifetimes['IWDB']:
                return False  # Cannot close IWKV with active IWDB objects

    return True