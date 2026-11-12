"""Keep the composition root out of application dependencies."""
import ast
import importlib.util
from pathlib import Path
import unittest


APP = Path(__file__).resolve().parents[1] / "app"


def application_imports(path):
    module = "app." + ".".join(path.relative_to(APP).with_suffix("").parts)
    package = module.rpartition(".")[0]
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            yield from (alias.name for alias in node.names if alias.name.startswith("app."))
        elif isinstance(node, ast.ImportFrom):
            name = node.module or ""
            if node.level:
                name = importlib.util.resolve_name("." * node.level + name, package)
            if name == "app" or name.startswith("app."):
                yield name
                yield from (name + "." + alias.name for alias in node.names)


class ArchitectureTest(unittest.TestCase):
    def test_application_import_graph_has_no_cycles_or_dependencies_on_main(self):
        modules = {
            "app." + ".".join(path.relative_to(APP).with_suffix("").parts): path
            for path in APP.rglob("*.py") if path.name != "__init__.py"
        }
        graph = {name: set(application_imports(path)) & modules.keys() for name, path in modules.items()}
        for name, imports in graph.items():
            self.assertNotIn("app.main", imports, name)

        visited = set()

        def visit(name, stack):
            self.assertNotIn(name, stack, " -> ".join([*stack, name]))
            if name in visited:
                return
            for dependency in graph[name]:
                visit(dependency, [*stack, name])
            visited.add(name)

        for name in graph:
            visit(name, [])

    def test_services_do_not_depend_on_http_routes_or_request_binding(self):
        for path in (APP / "services").glob("*.py"):
            with self.subTest(module=path.name):
                self.assertFalse(any(name.startswith("app.routers") for name in application_imports(path)))
                for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
                    if isinstance(node, ast.ImportFrom) and node.module == "fastapi":
                        self.assertFalse({a.name for a in node.names} & {"APIRouter", "Depends", "Query", "Request", "FastAPI"})

    def test_finance_and_banking_routes_do_not_access_sql_or_bank_http_clients(self):
        for name in ("accounts", "categories", "transactions", "reports", "banking"):
            tree = ast.parse((APP / "routers" / f"{name}.py").read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    self.assertFalse({a.name for a in node.names} & {"sqlalchemy", "httpx", "asyncpg"}, name)
                elif isinstance(node, ast.ImportFrom):
                    self.assertFalse((node.module or "").startswith(("sqlalchemy", "httpx", "asyncpg")), name)

