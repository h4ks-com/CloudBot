"""Typed command arguments declared as ``Annotated[T, Arg(...)]`` parameters."""

import inspect
import json
import types
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from typing import Annotated, Literal, Union, get_args, get_origin

ArgKind = Literal["user", "channel"]
OptionType = Literal["string", "int", "number", "bool", "user", "channel"]
ArgValue = str | int | float | bool | None
ArgType = type[str] | type[int] | type[float] | type[bool]

# We name it without the "+" client prefix so servers never relay it from users.
NAMED_ARGS_TAG = "cloudbot/args"
DEFAULT_CHANTYPES = "#&+!"

_OPTION_TYPES: dict[ArgType, OptionType] = {
    str: "string",
    int: "int",
    float: "number",
    bool: "bool",
}
_JSON_TYPES: dict[OptionType, str] = {
    "string": "string",
    "int": "integer",
    "number": "number",
    "bool": "boolean",
    "user": "string",
    "channel": "string",
}
_BOOL_WORDS = {
    "yes": True,
    "true": True,
    "on": True,
    "1": True,
    "no": False,
    "false": False,
    "off": False,
    "0": False,
}


class CommandArgError(ValueError):
    pass


def encode_named_args(options: Mapping[str, object]) -> str:
    return json.dumps(dict(options), separators=(",", ":"))


def decode_named_args(value: str | None) -> dict[str, object] | None:
    if not value:
        return None
    options = json.loads(value)
    if not isinstance(options, dict):
        raise CommandArgError("named arguments must be a JSON object")
    return options


@dataclass(frozen=True)
class Arg:
    help: str = ""
    kind: ArgKind | None = None
    # We give the rest of the line to the last plain text Arg while this is None; False keeps it to one word.
    rest: bool | None = None
    choices: Sequence[str] | None = None


OptionalChannel = Annotated[
    str | None, Arg("Channel, the current one by default", kind="channel")
]


@dataclass(frozen=True)
class ArgSpec:
    name: str
    type: ArgType
    required: bool
    default: ArgValue = None
    help: str = ""
    kind: ArgKind | None = None
    choices: tuple[str, ...] = ()
    rest: bool = False

    @property
    def option_type(self) -> OptionType:
        return self.kind or _OPTION_TYPES[self.type]

    @property
    def usage(self) -> str:
        label = "|".join(self.choices) or self.name
        return f"<{label}>" if self.required else f"[{label}]"

    @property
    def self_identifying(self) -> bool:
        return bool(self.choices) or self.kind == "channel"

    def identifies(self, word: str, chantypes: str) -> bool:
        if self.choices:
            return word.casefold() in {c.casefold() for c in self.choices}
        return self.kind == "channel" and word[:1] in chantypes

    @property
    def json_schema(self) -> dict[str, object]:
        schema: dict[str, object] = {
            "type": _JSON_TYPES[self.option_type],
            "description": self.help or self.name,
        }
        if self.choices:
            schema["enum"] = list(self.choices)
        return schema

    def convert(self, raw: str) -> ArgValue:
        if self.choices:
            for choice in self.choices:
                if choice.casefold() == raw.casefold():
                    return choice
            raise CommandArgError(
                f"{self.name} must be one of {', '.join(self.choices)}"
            )
        if self.type is bool:
            if raw.casefold() not in _BOOL_WORDS:
                raise CommandArgError(f"{self.name} must be yes or no")
            return _BOOL_WORDS[raw.casefold()]
        try:
            return self.type(raw)
        except ValueError as e:
            raise CommandArgError(
                f"{self.name} must be of type {self.option_type}"
            ) from e

    def accept(self, value: object) -> ArgValue:
        if isinstance(value, str):
            return self.convert(value)
        if type(value) is self.type or (
            self.type is float and type(value) is int
        ):
            return self.type(value)
        raise CommandArgError(f"{self.name} must be of type {self.option_type}")


@dataclass(frozen=True)
class CommandSpec:
    args: tuple[ArgSpec, ...] = ()

    @property
    def names(self) -> list[str]:
        return [arg.name for arg in self.args]

    def parse_text(
        self, text: str, chantypes: str = DEFAULT_CHANTYPES
    ) -> dict[str, ArgValue]:
        """Fill the Args from words in order. An optional Literal or channel
        Arg before a required one takes the next word only when that word
        matches it, and a Literal after the rest-of-line Arg takes the last
        word only when it matches."""
        rest_at = next(
            (i for i, arg in enumerate(self.args) if arg.rest), len(self.args)
        )
        given: dict[ArgSpec, str] = {}
        remaining = text.strip()
        for flag in reversed(self.args[rest_at + 1 :]):
            words = remaining.rsplit(None, 1)
            keeps_rest = len(words) == 2 or not self.args[rest_at].required
            if words and keeps_rest and flag.identifies(words[-1], chantypes):
                given[flag] = words[-1]
                remaining = words[0] if len(words) == 2 else ""
        for i, arg in enumerate(self.args[: rest_at + 1]):
            if arg.rest:
                if remaining:
                    given[arg] = remaining
                remaining = ""
                break
            words = remaining.split(None, 1)
            if not words:
                break
            if self._skips(i, words[0], chantypes):
                continue
            given[arg] = words[0]
            remaining = words[1] if len(words) == 2 else ""
        if remaining:
            raise CommandArgError("too many arguments")
        return self._fill(given)

    def _skips(self, index: int, word: str, chantypes: str) -> bool:
        arg = self.args[index]
        return (
            not arg.required
            and arg.self_identifying
            and any(later.required for later in self.args[index + 1 :])
            and not arg.identifies(word, chantypes)
        )

    @property
    def reads_chantypes(self) -> bool:
        return any(
            arg.kind == "channel" and not arg.required for arg in self.args
        )

    def parse_named(self, options: Mapping[str, object]) -> dict[str, ArgValue]:
        by_name = {arg.name: arg for arg in self.args}
        unknown = sorted(set(options) - set(by_name))
        if unknown:
            raise CommandArgError(f"unknown options: {', '.join(unknown)}")
        given = {
            by_name[name]: value
            for name, value in options.items()
            if value is not None and value != ""
        }
        return self._fill(given)

    def _fill(self, given: Mapping[ArgSpec, object]) -> dict[str, ArgValue]:
        values: dict[str, ArgValue] = {}
        for arg in self.args:
            if arg in given:
                values[arg.name] = arg.accept(given[arg])
            elif arg.required:
                raise CommandArgError(f"missing {arg.name}")
            else:
                values[arg.name] = arg.default
        return values

    def usage(self, description: str | None) -> str:
        line = " ".join(arg.usage for arg in self.args)
        return f"{line} - {description}" if description else line

    @property
    def json_schema(self) -> dict[str, object]:
        return {
            "type": "object",
            "properties": {arg.name: arg.json_schema for arg in self.args},
            "required": [arg.name for arg in self.args if arg.required],
            "additionalProperties": False,
        }


def _unwrap_optional(annotation: object) -> object:
    if get_origin(annotation) in (Union, types.UnionType):
        members = [a for a in get_args(annotation) if a is not type(None)]
        if len(members) == 1:
            return members[0]
    return annotation


def _arg_spec(
    func: Callable[..., object], param: inspect.Parameter, marker: Arg
) -> ArgSpec:
    annotation = _unwrap_optional(get_args(param.annotation)[0])
    choices: tuple[str, ...] = ()
    if get_origin(annotation) is Literal:
        choices = get_args(annotation)
        if not all(isinstance(choice, str) for choice in choices):
            raise TypeError(
                f"{func.__qualname__}: Literal choices of {param.name!r} must be strings"
            )
        annotation = str
    if marker.choices is not None:
        if choices:
            raise TypeError(
                f"{func.__qualname__}: {param.name!r} has both a Literal and choices="
            )
        choices = tuple(marker.choices)
        if not choices or not all(isinstance(c, str) for c in choices):
            raise TypeError(
                f"{func.__qualname__}: choices of {param.name!r} must be non-empty strings"
            )
    if annotation not in _OPTION_TYPES:
        raise TypeError(
            f"{func.__qualname__}: unsupported type {annotation!r} for "
            f"command argument {param.name!r}, use str, int, float, bool or Literal"
        )
    if param.kind not in (
        inspect.Parameter.POSITIONAL_OR_KEYWORD,
        inspect.Parameter.KEYWORD_ONLY,
    ):
        raise TypeError(
            f"{func.__qualname__}: command argument {param.name!r} must be a named parameter"
        )
    if marker.rest and (annotation is not str or choices):
        raise TypeError(
            f"{func.__qualname__}: rest-of-line argument {param.name!r} must be a str"
        )
    required = param.default is inspect.Parameter.empty
    return ArgSpec(
        name=param.name,
        type=annotation,
        required=required,
        default=None if required else param.default,
        help=marker.help,
        kind=marker.kind,
        choices=choices,
        rest=marker.rest is True,
    )


def build_spec(func: Callable[..., object]) -> CommandSpec:
    """Collect the ``Annotated[T, Arg(...)]`` parameters of a command, in
    signature order. Raises TypeError for an annotation we cannot parse."""
    args: list[ArgSpec] = []
    markers: list[Arg] = []
    for param in inspect.signature(func).parameters.values():
        if isinstance(param.annotation, str) and "Arg(" in param.annotation:
            raise TypeError(
                f"{func.__qualname__}: command argument {param.name!r} has a "
                "string annotation, drop `from __future__ import annotations`"
            )
        if get_origin(param.annotation) is not Annotated:
            continue
        marker = next(
            (m for m in get_args(param.annotation)[1:] if isinstance(m, Arg)),
            None,
        )
        if marker:
            markers.append(marker)
            args.append(_arg_spec(func, param, marker))
    for i, arg in enumerate(args):
        if (
            not arg.required
            and not arg.self_identifying
            and any(later.required for later in args[i + 1 :])
        ):
            raise TypeError(
                f"{func.__qualname__}: optional command argument {arg.name!r} "
                "comes before a required one, only a Literal or "
                'kind="channel" argument can'
            )
    rests = [i for i, arg in enumerate(args) if arg.rest]
    if rests:
        flags = args[rests[0] + 1 :]
        if len(rests) > 1 or any(f.required or not f.choices for f in flags):
            raise TypeError(
                f"{func.__qualname__}: only optional Literal arguments can "
                "follow the rest-of-line argument"
            )
    elif (
        args
        and args[-1].type is str
        and not args[-1].choices
        and args[-1].kind is None
        and markers[-1].rest is None
    ):
        args[-1] = replace(args[-1], rest=True)
    return CommandSpec(tuple(args))
