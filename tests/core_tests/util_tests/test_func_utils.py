import pytest

from cloudbot.util import func_utils


def test_call_with_args():
    args = []

    def func(arg1, arg2=None, _arg3=None):
        nonlocal args
        args = [arg1, arg2, _arg3]

    func_utils.call_with_args(func, {"arg1": 1, "arg2": 3})
    assert args == [1, 3, None]

    with pytest.raises(func_utils.ParameterError):
        func_utils.call_with_args(func, {})


def test_call_with_args_passes_keyword_only_params_by_name():
    def func(arg1, *, opt=None, req):
        return arg1, opt, req

    assert func_utils.call_with_args(func, {"arg1": 1, "opt": 2, "req": 3}) == (
        1,
        2,
        3,
    )
