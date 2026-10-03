from pixel_station.database import Database, Memory
from pixel_station.vectors import SqliteVectorStore


def test_vector_adapter_retrieval_updates_and_dimension_isolation(tmp_path):
    database = Database(tmp_path)
    database.migrate()
    with database.session() as session:
        first = Memory(text="cats", embedding=[1, 0])
        second = Memory(text="dogs", embedding=[0, 1])
        other_model = Memory(text="different dimensions", embedding=[1, 0, 0])
        session.add_all([first, second, other_model])
        session.commit()
        store = SqliteVectorStore(session, Memory)
        results = store.search([1, 0], 5)
        assert results[0][0] == first.id
        assert other_model.id not in [identity for identity, _ in results]
        assert store.backend == "sqlite-vec"
        store.update(first.id, [0, 1])
        session.flush()
        assert store.search([0, 1])[0][1] > 0.99
        store.delete(first.id)
        session.flush()
        assert first.id not in [identity for identity, _ in store.search([1, 0])]
        assert store.search([0, 0]) == []
