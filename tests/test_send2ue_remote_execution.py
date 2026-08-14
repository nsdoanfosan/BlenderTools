import importlib.util
from pathlib import Path
import sys
import types
import unittest
from unittest import mock


REPO_ROOT = Path(__file__).resolve().parents[1]
DEPENDENCIES_PATH = REPO_ROOT / "src" / "addons" / "send2ue" / "dependencies"
PACKAGE_NAME = "_send2ue_remote_execution_tests"
DEPENDENCIES_PACKAGE_NAME = f"{PACKAGE_NAME}.dependencies"


def _make_package(name, package_name=None):
    package = types.ModuleType(name)
    package.__path__ = []
    package.__package__ = package_name or name
    sys.modules[name] = package
    return package


def _load_dependency_module(name):
    module_name = f"{DEPENDENCIES_PACKAGE_NAME}.{name}"
    spec = importlib.util.spec_from_file_location(
        module_name,
        DEPENDENCIES_PATH / f"{name}.py",
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


_make_package(PACKAGE_NAME, package_name="send2ue")
_make_package(DEPENDENCIES_PACKAGE_NAME)
REMOTE_EXECUTION = _load_dependency_module("remote_execution")
UNREAL = _load_dependency_module("unreal")


class FakeClock:
    def __init__(self):
        self.value = 0.0

    def monotonic(self):
        return self.value

    def sleep(self, seconds):
        self.value += seconds


class FakeRemoteExecution:
    def __init__(
        self,
        nodes_after_polls=1,
        open_error=None,
        command_error=None,
    ):
        self.nodes_after_polls = nodes_after_polls
        self.open_error = open_error
        self.command_error = command_error
        self.poll_count = 0
        self.start_calls = 0
        self.stop_calls = 0
        self.open_calls = 0
        self.close_calls = 0
        self.command_calls = 0
        self.connected = False
        self._config = types.SimpleNamespace(
            multicast_group_endpoint=("239.0.0.1", 6766),
            multicast_bind_address="192.168.0.4",
            command_endpoint=("127.0.0.1", 6776),
        )

    @property
    def remote_nodes(self):
        self.poll_count += 1
        if (
            self.nodes_after_polls is not None
            and self.poll_count >= self.nodes_after_polls
        ):
            return [{"node_id": "unreal-node"}]
        return []

    def start(self):
        self.start_calls += 1

    def stop(self):
        self.stop_calls += 1

    def open_command_connection(self, node_id):
        self.open_calls += 1
        if self.open_error:
            raise self.open_error
        self.connected = True

    def close_command_connection(self):
        self.close_calls += 1
        self.connected = False

    def has_command_connection(self):
        return self.connected

    def run_command(self, command, unattended=False):
        self.command_calls += 1
        if self.command_error:
            raise self.command_error
        return {
            "output": [{"type": "Info", "output": "remote probe ok"}],
            "result": "None",
        }


class TestRemoteExecutionConfig(unittest.TestCase):
    def test_multicast_bind_address_is_independent_from_command_endpoint(self):
        preferences = types.SimpleNamespace(
            multicast_ttl=1,
            multicast_group_endpoint="239.0.0.1:6766",
            multicast_bind_address="192.168.0.4",
            command_endpoint="127.0.0.1:6776",
        )
        bpy = types.ModuleType("bpy")
        bpy.context = types.SimpleNamespace(
            preferences=types.SimpleNamespace(
                addons={
                    "send2ue": types.SimpleNamespace(preferences=preferences),
                }
            )
        )

        with mock.patch.dict(sys.modules, {"bpy": bpy}):
            config = REMOTE_EXECUTION.RemoteExecutionConfig()

        self.assertEqual(config.multicast_bind_address, "192.168.0.4")
        self.assertEqual(config.command_endpoint, ("127.0.0.1", 6776))

    def test_blank_multicast_bind_address_keeps_legacy_command_host(self):
        preferences = types.SimpleNamespace(
            multicast_ttl=0,
            multicast_group_endpoint="239.0.0.1:6766",
            multicast_bind_address="",
            command_endpoint="127.0.0.1:6776",
        )
        bpy = types.ModuleType("bpy")
        bpy.context = types.SimpleNamespace(
            preferences=types.SimpleNamespace(
                addons={
                    "send2ue": types.SimpleNamespace(preferences=preferences),
                }
            )
        )

        with mock.patch.dict(sys.modules, {"bpy": bpy}):
            config = REMOTE_EXECUTION.RemoteExecutionConfig()

        self.assertEqual(config.multicast_bind_address, "127.0.0.1")


class TestRunUnrealPythonCommands(unittest.TestCase):
    def setUp(self):
        UNREAL.unreal_response = ""

    def _clock_patches(self, clock):
        return (
            mock.patch.object(UNREAL.time, "monotonic", clock.monotonic),
            mock.patch.object(UNREAL.time, "sleep", clock.sleep),
        )

    def test_discovers_editor_after_legacy_five_second_window(self):
        remote_exec = FakeRemoteExecution(nodes_after_polls=52)
        clock = FakeClock()
        monotonic_patch, sleep_patch = self._clock_patches(clock)

        with monotonic_patch, sleep_patch:
            response = UNREAL.run_unreal_python_commands(
                remote_exec,
                ["print('probe')"],
                connection_timeout=6.0,
                poll_interval=0.1,
            )

        self.assertEqual(response, "remote probe ok")
        self.assertGreater(clock.value, 5.0)
        self.assertEqual(remote_exec.command_calls, 1)
        self.assertEqual(remote_exec.stop_calls, 0)

    def test_run_commands_stops_session_once(self):
        remote_exec = FakeRemoteExecution()

        with mock.patch.object(
            REMOTE_EXECUTION,
            "RemoteExecution",
            return_value=remote_exec,
        ):
            response = UNREAL.run_commands(["print('probe')"])

        self.assertEqual(response, "remote probe ok")
        self.assertEqual(remote_exec.start_calls, 1)
        self.assertEqual(remote_exec.stop_calls, 1)

    def test_timeout_has_endpoint_diagnostics_and_stops_once(self):
        remote_exec = FakeRemoteExecution(nodes_after_polls=None)
        clock = FakeClock()
        monotonic_patch, sleep_patch = self._clock_patches(clock)

        with (
            monotonic_patch,
            sleep_patch,
            mock.patch.object(
                REMOTE_EXECUTION,
                "RemoteExecution",
                return_value=remote_exec,
            ),
            mock.patch.object(
                UNREAL,
                "REMOTE_EXECUTION_CONNECTION_TIMEOUT",
                0.3,
            ),
        ):
            with self.assertRaises(ConnectionError) as raised:
                UNREAL.run_commands(["print('probe')"])

        self.assertIn("bind=192.168.0.4", str(raised.exception))
        self.assertIn("discovered_nodes=0", str(raised.exception))
        self.assertEqual(remote_exec.start_calls, 1)
        self.assertEqual(remote_exec.stop_calls, 1)

    def test_connection_failure_preserves_original_cause(self):
        root_cause = RuntimeError("Unreal command socket stayed busy")
        remote_exec = FakeRemoteExecution(open_error=root_cause)
        clock = FakeClock()
        monotonic_patch, sleep_patch = self._clock_patches(clock)

        with monotonic_patch, sleep_patch:
            with self.assertRaises(ConnectionError) as raised:
                UNREAL.run_unreal_python_commands(
                    remote_exec,
                    ["print('probe')"],
                    connection_timeout=0.3,
                    poll_interval=0.1,
                )

        self.assertIs(raised.exception.__cause__, root_cause)
        self.assertGreater(remote_exec.close_calls, 0)

    def test_dispatched_command_is_not_retried(self):
        root_cause = RuntimeError("response socket closed")
        remote_exec = FakeRemoteExecution(command_error=root_cause)

        with self.assertRaises(ConnectionError) as raised:
            UNREAL.run_unreal_python_commands(
                remote_exec,
                ["print('probe')"],
                connection_timeout=0.3,
            )

        self.assertIs(raised.exception.__cause__, root_cause)
        self.assertEqual(remote_exec.command_calls, 1)


if __name__ == "__main__":
    unittest.main()
