from cloudbot.util.database import Base, base, metadata


def test_database():
    assert metadata is Base.metadata
    assert base is Base
