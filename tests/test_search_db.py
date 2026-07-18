import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from agent_utils import search_db


class SearchDbTests(unittest.TestCase):
    @patch.object(search_db.dotenv, "load_dotenv")
    def test_loads_default_environment(self, load_dotenv):
        search_db.load_environment()

        load_dotenv.assert_called_once_with(
            Path(search_db.__file__).resolve().parent.parent / ".env",
            override=False,
        )

    @patch.object(search_db.dotenv, "load_dotenv")
    def test_loads_prod_environment_with_override(self, load_dotenv):
        search_db.load_environment(prod=True)

        load_dotenv.assert_called_once_with(
            Path(search_db.__file__).resolve().parent.parent / ".env.prod.env",
            override=True,
        )

    @patch.object(search_db.psycopg2, "connect")
    @patch.object(search_db, "load_environment")
    def test_prod_connection_selects_prod_environment(self, load_environment, connect):
        connection = MagicMock()
        connect.return_value = connection

        search_db.get_connection(prod=True)

        load_environment.assert_called_once_with(prod=True)
        connection.set_session.assert_called_once_with(readonly=True)


if __name__ == "__main__":
    unittest.main()
