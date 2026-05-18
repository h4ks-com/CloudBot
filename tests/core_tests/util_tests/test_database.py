from cloudbot.util import database


def test_database(mock_db):
    database.configure()
    assert database.Session.session_factory.kw.get("bind") is None
    engine = mock_db.engine
    database.configure(engine)
    assert database.Session.session_factory.kw.get("bind") is engine
