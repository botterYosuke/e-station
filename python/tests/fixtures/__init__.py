# Lazy imports to avoid import-time proto issues
def __getattr__(name: str):
    if name == "MockGrpcServer":
        from tests.fixtures.mock_grpc_server import MockGrpcServer
        return MockGrpcServer
    elif name == "ScriptedMockGrpcServer":
        from tests.fixtures.mock_grpc_server import ScriptedMockGrpcServer
        return ScriptedMockGrpcServer
    elif name == "MockIPCServer":
        from tests.fixtures.mock_ipc_server import MockIPCServer
        return MockIPCServer
    raise AttributeError(f"module '{__name__}' has no attribute '{name}'")


__all__ = ["MockGrpcServer", "MockIPCServer", "ScriptedMockGrpcServer"]
