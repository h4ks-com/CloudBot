import inspect


class ParameterError(Exception):
    def __init__(self, name, valid_args):
        super().__init__(
            f"{name!r} is not a valid parameter, valid parameters are: {list(valid_args)}"
        )
        self.name = name
        self.valid_args = list(valid_args)


def call_with_args(func, arg_data):
    sig = inspect.signature(func, follow_wrapped=False)
    args = []
    kwargs = {}
    try:
        for key, param in sig.parameters.items():
            if key.startswith("_"):
                continue
            if param.kind is inspect.Parameter.KEYWORD_ONLY:
                kwargs[key] = arg_data[key]
            else:
                args.append(arg_data[key])
    except KeyError as e:
        raise ParameterError(e.args[0], arg_data.keys()) from e

    return func(*args, **kwargs)
