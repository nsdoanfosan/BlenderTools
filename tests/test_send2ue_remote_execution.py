import ast
import importlib.util
import os
import queue
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

    def open_command_connection(self, node_id, timeout=5.0):
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

    def test_500_second_import_timeout_does_not_delay_missing_editor(self):
        bpy = types.ModuleType('bpy')
        bpy.context = types.SimpleNamespace(preferences=types.SimpleNamespace(
            addons={'send2ue': types.SimpleNamespace(
                preferences=types.SimpleNamespace(rpc_response_timeout=500))}))
        remote_exec = FakeRemoteExecution(nodes_after_polls=None)
        clock = FakeClock()
        monotonic_patch, sleep_patch = self._clock_patches(clock)
        with mock.patch.dict(sys.modules, {'bpy': bpy}), monotonic_patch, sleep_patch:
            with self.assertRaisesRegex(ConnectionError, 'No Unreal Editor responded'):
                UNREAL.run_unreal_python_commands(remote_exec, ['pass'])
        self.assertAlmostEqual(clock.value, 5.0)
        self.assertEqual(remote_exec.command_calls, 0)

    def test_multiple_unresponsive_nodes_share_one_deadline(self):
        remote_exec = FakeRemoteExecution()
        clock = FakeClock()
        attempts = []

        def fail_open(node_id, timeout):
            attempts.append(timeout)
            clock.sleep(timeout)
            raise RuntimeError('No command callback')

        monotonic_patch, sleep_patch = self._clock_patches(clock)
        with (
            monotonic_patch, sleep_patch,
            mock.patch.object(FakeRemoteExecution, 'remote_nodes', new_callable=mock.PropertyMock,
                              return_value=[{'node_id': str(i)} for i in range(5)]),
            mock.patch.object(remote_exec, 'open_command_connection', side_effect=fail_open),
        ):
            with self.assertRaisesRegex(ConnectionError, 'was discovered'):
                UNREAL.run_unreal_python_commands(remote_exec, ['pass'], connection_timeout=2.5)
        self.assertEqual(attempts, [1.0, 1.0, 0.5])
        self.assertAlmostEqual(clock.value, 2.5)
        self.assertEqual(remote_exec.command_calls, 0)

    def test_start_failure_still_stops_session(self):
        remote_exec = FakeRemoteExecution()
        with (
            mock.patch.object(REMOTE_EXECUTION, 'RemoteExecution', return_value=remote_exec),
            mock.patch.object(remote_exec, 'start', side_effect=OSError('Invalid bind address')),
        ):
            with self.assertRaises(OSError):
                UNREAL.run_commands(['pass'])
        self.assertEqual(remote_exec.stop_calls, 1)


class TestCommandSocketDeadline(unittest.TestCase):
    def make_connection(self):
        config = types.SimpleNamespace(command_endpoint=('127.0.0.1', 0),
                                       command_response_timeout=500.0)
        return REMOTE_EXECUTION._RemoteExecutionCommandConnection(config, 'local', 'remote')

    def test_accept_timeout_is_clipped_to_remaining_budget(self):
        connection = self.make_connection()
        clock = FakeClock()
        listener = mock.Mock()
        connection._command_listen_socket = listener

        def timeout_accept():
            clock.sleep(listener.settimeout.call_args.args[0])
            raise TimeoutError()

        listener.accept.side_effect = timeout_accept
        with mock.patch.object(REMOTE_EXECUTION._time, 'monotonic', clock.monotonic):
            with self.assertRaisesRegex(RuntimeError, 'command socket connection'):
                connection._try_accept(mock.Mock(), timeout=1.25)
        self.assertEqual(listener.settimeout.call_args_list, [mock.call(1.0), mock.call(0.25)])
        self.assertEqual(clock.value, 1.25)

    def test_connected_command_keeps_long_import_response_timeout(self):
        connection = self.make_connection()
        listener, channel = mock.Mock(), mock.Mock()
        connection._command_listen_socket = listener
        listener.accept.return_value = (channel, ('127.0.0.1', 12345))
        connection._try_accept(mock.Mock(), timeout=0.2)
        channel.settimeout.assert_called_once_with(500.0)

    def test_failed_open_releases_sockets_and_does_not_appear_connected(self):
        remote_exec = REMOTE_EXECUTION.RemoteExecution(config=types.SimpleNamespace())
        remote_exec._broadcast_connection = mock.Mock()
        connection = mock.Mock()
        connection.open.side_effect = RuntimeError('No callback')
        with mock.patch.object(REMOTE_EXECUTION, '_RemoteExecutionCommandConnection',
                               return_value=connection):
            with self.assertRaises(RuntimeError):
                remote_exec.open_command_connection('remote', timeout=0.25)
        connection.close.assert_called_once_with(remote_exec._broadcast_connection)
        self.assertFalse(remote_exec.has_command_connection())

    def test_real_idle_listener_obeys_short_timeout_and_closes(self):
        connection = self.make_connection()
        broadcast = mock.Mock()
        started = REMOTE_EXECUTION._time.monotonic()
        try:
            with self.assertRaises(RuntimeError):
                connection.open(broadcast, timeout=0.2)
        finally:
            connection.close(broadcast)
        self.assertLess(REMOTE_EXECUTION._time.monotonic() - started, 1.0)
        self.assertIsNone(connection._command_listen_socket)
        self.assertIsNone(connection._command_channel_socket)

    def test_editor_exit_during_response_fails_without_waiting_for_import_timeout(self):
        connection = self.make_connection()
        connection._command_channel_socket = mock.Mock()
        connection._command_channel_socket.recv.return_value = b''
        with self.assertRaisesRegex(RuntimeError, 'valid response'):
            connection.run_command('pass', unattended=False, exec_mode='ExecuteFile')
        connection._command_channel_socket.recv.assert_called_once()


class TestFailedOperatorCleanup(unittest.TestCase):
    def test_lost_editor_cancels_queue_and_local_cleanup_does_not_reconnect(self):
        # Load the real operator class without registering Blender UI or importing
        # unrelated geometry exporters into this standalone network test suite.
        path = DEPENDENCIES_PATH.parent / 'operators.py'
        tree = ast.parse(path.read_text(encoding='utf-8'))
        operator_node = next(node for node in tree.body
                             if isinstance(node, ast.ClassDef) and node.name == 'Send2Ue')
        bpy = types.SimpleNamespace(types=types.SimpleNamespace(
            Operator=object, STATUSBAR_HT_header=mock.Mock()), context=mock.Mock())
        namespace = {'bpy': bpy, 'os': os, 'unreal': UNREAL, 'utilities': mock.Mock()}
        exec(compile(ast.Module(body=[operator_node], type_ignores=[]), str(path), 'exec'), namespace)
        operator = object.__new__(namespace['Send2Ue'])
        operator.done = False
        operator.escape = False
        operator.timer = object()
        operator.max_step = 2
        operator.report = mock.Mock()
        operator.execution_queue = queue.Queue()
        failed_job = mock.Mock(side_effect=ConnectionError('Editor exited'))
        next_job = mock.Mock()
        for job in (failed_job, next_job):
            operator.execution_queue.put((job, (), {}, '{attribute}', 'asset', 'file'))
        operator.post_operation = mock.Mock(side_effect=lambda: UNREAL.run_commands(['save_assets()']))
        context = mock.Mock()
        context.window_manager.send2ue.asset_data = {'asset': {'file': 'hair.fbx'}}
        with (
            mock.patch.dict(os.environ, {'SEND2UE_DEV': ''}),
            mock.patch.object(REMOTE_EXECUTION, 'RemoteExecution') as remote_factory,
        ):
            result = operator.modal(context, types.SimpleNamespace(type='TIMER'))
        self.assertEqual(result, {'CANCELLED'})
        self.assertTrue(operator.execution_queue.empty())
        self.assertIsNone(operator.timer)
        self.assertTrue(operator.done)
        operator.post_operation.assert_called_once()
        operator.report.assert_called_once_with({'ERROR'}, 'Editor exited')
        context.window_manager.event_timer_remove.assert_called_once()
        next_job.assert_not_called()
        remote_factory.assert_not_called()


class TestRPCProbeTimeout(unittest.TestCase):
    def test_probe_transport_timeout_is_separate_from_normal_rpc_calls(self):
        spec = importlib.util.spec_from_file_location('rpc_client_timeout_test', DEPENDENCIES_PATH / 'rpc' / 'client.py')
        client = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(client)
        probe = client.RPCTransport(timeout=1.0)
        normal = client.RPCTransport()
        self.assertEqual(probe.make_connection('127.0.0.1:9998').timeout, 1.0)
        self.assertIsNone(normal.timeout)
        probe.close()
        normal.close()


if __name__ == "__main__":
    unittest.main()
