import weakref
from collections import defaultdict
from collections.abc import MutableMapping
from typing import TYPE_CHECKING, Generic, TypeVar, cast

__all__ = (
    "KeyFoldDict",
    "KeyFoldMixin",
    "KeyFoldWeakValueDict",
    "DefaultKeyFoldDict",
)


K = TypeVar("K", bound=str)
V = TypeVar("V")
T = TypeVar("T")


# At type-check time, MapBase aliases to MutableMapping so mypy knows the
# super() dispatches in KeyFoldMixin reach concrete, non-abstract methods
# (the abstract dict-like methods on MutableMapping are implemented by the
# concrete dict / defaultdict / WeakValueDictionary sibling in each MRO).
# At runtime, MapBase is a no-op Generic base — actual dispatch is provided
# by the concrete sibling class in each subclass's MRO.
if TYPE_CHECKING:

    class MapBase(MutableMapping[K, V], Generic[K, V]):
        pass

else:

    class MapBase(Generic[K, V]):
        pass


class KeyFoldMixin(MapBase[K, V]):
    """
    A mixin for Mapping to allow for case-insensitive keys
    """

    def __getitem__(self, item: K) -> V:
        return super().__getitem__(cast(K, item.casefold()))

    def __setitem__(self, key: K, value: V) -> None:
        return super().__setitem__(cast(K, key.casefold()), value)

    def __delitem__(self, key: K) -> None:
        return super().__delitem__(cast(K, key.casefold()))

    def pop(self, key: K, *args) -> V:
        """
        Wraps `dict.pop`
        """
        return super().pop(cast(K, key.casefold()), *args)

    def get(self, key: K, default=None):
        """
        Wrap `dict.get`
        """
        return super().get(cast(K, key.casefold()), default)

    def setdefault(self, key: K, default=None):
        """
        Wrap `dict.setdefault`
        """
        return super().setdefault(cast(K, key.casefold()), default)

    def update(self, *args, **kwargs):
        """
        Wrap `dict.update`
        """
        if args:
            mapping = args[0]
            if hasattr(mapping, "keys"):
                for k in mapping.keys():
                    self[k] = mapping[k]
            else:
                for k, v in mapping:
                    self[k] = v

        for k in kwargs:
            self[k] = kwargs[k]


class KeyFoldDict(KeyFoldMixin, dict):
    """
    KeyFolded dict type
    """


class DefaultKeyFoldDict(KeyFoldMixin, defaultdict):
    """
    KeyFolded defaultdict
    """


class KeyFoldWeakValueDict(KeyFoldMixin, weakref.WeakValueDictionary):
    """
    KeyFolded WeakValueDictionary
    """
