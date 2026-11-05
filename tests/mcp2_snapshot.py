"""Compare coherent snapshots while allowing wall-clock time to advance."""
def snapshot(value):
    if isinstance(value, dict):
        return {key: snapshot(item) for key, item in value.items() if key != 'server_time'}
    if isinstance(value, tuple):
        return tuple(snapshot(item) for item in value)
    if isinstance(value, list):
        return [snapshot(item) for item in value]
    return value
