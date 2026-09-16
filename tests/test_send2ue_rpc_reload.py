import importlib.util
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest import mock


RPC_PATH = Path(__file__).resolve().parents[1] / 'src/addons/send2ue/dependencies/rpc'
PACKAGE = '_send2ue_rpc_reload_tests'
package = types.ModuleType(PACKAGE)
package.__path__ = [str(RPC_PATH)]
sys.modules[PACKAGE] = package
spec = importlib.util.spec_from_file_location(PACKAGE + '.factory', RPC_PATH / 'factory.py')
factory = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = factory
spec.loader.exec_module(factory)


class TestRPCReload(unittest.TestCase):
    def test_decorated_call_uses_current_function_after_source_lines_move(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'reload_fixture.py'
            original = 'class Calls:\n    @staticmethod\n    def probe():\n        return 1\n'
            path.write_text(original, encoding='utf-8')
            module = types.ModuleType('_send2ue_reload_fixture')
            module.__file__ = str(path)
            sys.modules[module.__name__] = module
            try:
                exec(compile(original, str(path), 'exec'), module.__dict__)
                old_function = module.Calls.probe
                new_source = '\n' * 25 + original.replace('return 1', 'return 2')
                path.write_text(new_source, encoding='utf-8')
                exec(compile(new_source, str(path), 'exec'), module.__dict__)
                client = mock.Mock(marshall_exceptions=False)
                client.proxy.probe.return_value = 2
                rpc = factory.RPCFactory(client)
                self.assertEqual(rpc.run_function_remotely(old_function, ()), 2)
                name, source, _ = client.proxy.add_new_callable.call_args.args
                self.assertEqual(name, 'probe')
                scope = {}
                exec(compile(source, '<rpc>', 'exec'), scope)
                self.assertEqual(scope['probe'](), 2)
            finally:
                sys.modules.pop(module.__name__, None)

    def test_generated_imports_do_not_modify_shared_defaults(self):
        defaults = ['import unreal']
        rpc = factory.RPCFactory(mock.Mock(), default_imports=defaults)
        rpc._get_callstack_references(['def probe():', '    return Path()'],
                                     self.test_generated_imports_do_not_modify_shared_defaults)
        self.assertEqual(defaults, ['import unreal'])


if __name__ == '__main__':
    unittest.main()
