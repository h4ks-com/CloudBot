import ast
from dataclasses import dataclass
from pathlib import Path


@dataclass
class CommandInfo:
    name: str
    aliases: list[str]
    function_name: str
    docstring: str | None
    file_path: str
    line_number: int | None
    plugin_name: str
    status: str = "functional"


@dataclass
class PluginInfo:
    name: str
    file_path: str
    commands: list[CommandInfo]
    status: str = "functional"


class PluginParser:
    def __init__(
        self,
        plugins_dir: str,
        blacklist: set[str] | None = None,
        broken_plugins: set[str] | None = None,
    ):
        self.plugins_dir = Path(plugins_dir)
        self.blacklist = blacklist or {
            "core",
            "admin",
            "permissions",
            "factoids",
            "ignore",
            "chan_track",
            "history",
            "logs",
            "help",
        }
        self.broken_plugins = broken_plugins or set()

    def _extract_hook_commands(
        self, node: ast.AST
    ) -> list[tuple[str, list[str]]]:
        """Extract command names and aliases from @hook.command decorators."""
        commands = []

        if isinstance(node, ast.FunctionDef):
            for decorator in node.decorator_list:
                if self._is_hook_command_decorator(decorator):
                    cmd_names = self._parse_command_decorator(decorator)
                    if cmd_names:
                        primary_name = cmd_names[0]
                        aliases = cmd_names[1:] if len(cmd_names) > 1 else []
                        commands.append((primary_name, aliases))

        return commands

    def _is_hook_command_decorator(self, decorator: ast.AST) -> bool:
        """Check if decorator is @hook.command."""
        if isinstance(decorator, ast.Call):
            if isinstance(decorator.func, ast.Attribute):
                return (
                    isinstance(decorator.func.value, ast.Name)
                    and decorator.func.value.id == "hook"
                    and decorator.func.attr == "command"
                )
        elif isinstance(decorator, ast.Attribute):
            return (
                isinstance(decorator.value, ast.Name)
                and decorator.value.id == "hook"
                and decorator.attr == "command"
            )
        return False

    def _parse_command_decorator(self, decorator: ast.AST) -> list[str]:
        """Parse command names from decorator arguments."""
        names = []

        if isinstance(decorator, ast.Call):
            for arg in decorator.args:
                if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                    names.append(arg.value)

        return names

    def _get_function_docstring(self, node: ast.FunctionDef) -> str | None:
        """Extract docstring from function definition."""
        if (
            node.body
            and isinstance(node.body[0], ast.Expr)
            and isinstance(node.body[0].value, ast.Constant)
            and isinstance(node.body[0].value.value, str)
        ):
            return node.body[0].value.value.strip()
        return None

    def _get_line_number_from_source(
        self, file_path: str, function_name: str
    ) -> int | None:
        """Get line number where function is defined by parsing source."""
        try:
            with open(file_path, encoding="utf-8") as f:
                lines = f.readlines()
                for i, line in enumerate(lines, 1):
                    if f"def {function_name}(" in line:
                        return i
        except (OSError, UnicodeDecodeError):
            pass
        return None

    def parse_plugin_file(self, file_path: str) -> PluginInfo | None:
        """Parse a single plugin file and extract command information."""
        try:
            with open(file_path, encoding="utf-8") as f:
                source = f.read()

            tree = ast.parse(source)
            plugin_name = Path(file_path).stem
            commands = []

            for node in ast.walk(tree):
                if isinstance(node, ast.FunctionDef):
                    hook_commands = self._extract_hook_commands(node)

                    for primary_name, aliases in hook_commands:
                        if primary_name not in self.blacklist:
                            docstring = self._get_function_docstring(node)
                            line_number = self._get_line_number_from_source(
                                file_path, node.name
                            )

                            status = (
                                "broken"
                                if plugin_name in self.broken_plugins
                                else "functional"
                            )

                            command_info = CommandInfo(
                                name=primary_name,
                                aliases=aliases,
                                function_name=node.name,
                                docstring=docstring,
                                file_path=file_path,
                                line_number=line_number,
                                plugin_name=plugin_name,
                                status=status,
                            )
                            commands.append(command_info)

            if commands:
                status = (
                    "broken"
                    if plugin_name in self.broken_plugins
                    else "functional"
                )
                return PluginInfo(
                    name=plugin_name,
                    file_path=file_path,
                    commands=commands,
                    status=status,
                )

        except (SyntaxError, UnicodeDecodeError, OSError):
            pass

        return None

    def parse_all_plugins(self) -> list[PluginInfo]:
        """Parse all plugin files in the plugins directory."""
        plugins = []

        for file_path in self.plugins_dir.rglob("*.py"):
            # Skip core plugins and __init__.py files
            relative_path = file_path.relative_to(self.plugins_dir)
            if relative_path.parts[
                0
            ] in self.blacklist or file_path.name.startswith("__"):
                continue

            plugin_info = self.parse_plugin_file(str(file_path))
            if plugin_info:
                plugins.append(plugin_info)

        return sorted(plugins, key=lambda p: p.name)
