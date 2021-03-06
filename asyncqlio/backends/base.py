"""
The base implementation of a backend. This provides some ABC classes.
"""
import asyncio
import collections
import typing
from abc import abstractmethod
from collections import OrderedDict
from urllib.parse import ParseResult, parse_qs

from asyncqlio.meta import AsyncABC


class BaseDialect:
    """
    The base class for a SQL dialect describer.

    This class signifies what features the SQL dialect can use, and as such can be used to customize
    query creation for faster results on certain servers, or new features on certain servers, etc.

    By default, all ``has_`` properties will default to False, so that none of them need be
    implemented. Regular methods will raise NotImplementedError, however.
    """

    @property
    def has_checkpoints(self) -> bool:
        """
        Returns True if this dialect can use transaction checkpoints.
        """
        return False

    @property
    def has_serial(self) -> bool:
        """
        Returns True if this dialect can use the SERIAL datatype.
        """
        return False

    @property
    def has_returns(self) -> bool:
        """
        Returns True if this dialect has RETURNS.
        """
        return False

    @property
    def has_ilike(self) -> bool:
        """
        Returns True if this dialect has ILIKE.
        """
        return False

    @property
    def has_default(self) -> bool:
        """
        Returns True if this dialect has DEFAULT.
        """
        return False

    @property
    def has_truncate(self) -> bool:
        """
        Returns TRUE if this dialect has TRUNCATE.
        """
        return False

    @property
    def lastval_method(self):
        """
        The last value method for a dialect. For example, in PostgreSQL this is LASTVAL();
        """
        raise NotImplementedError

    def get_primary_key_index_name(self, table_name: str) -> str:
        """
        Get the name a dialect gives to a table's primary key index.
        """
        raise NotImplementedError

    def get_unique_column_index_name(self, table_name: str, column_name: str) -> str:
        """
        Get the name a dialect gives to a unique column index.

        :param table_name: The name of the table to use.
        :param column_name: The name of the column to use.
        """
        raise NotImplementedError

    def get_column_sql(self, table_name: str = None,
                       *, emitter: 'typing.Callable[[str], str]') -> str:
        """
        Get a query to find information on all columns, optionally limiting by table.

        :param table_name: The name of the table to use.
        :param emitter: The emitter to use.
        """
        raise NotImplementedError

    def get_index_sql(self, table_name: str = None,
                      *, emitter: 'typing.Callable[[str], str]') -> str:
        """
        Get a query to find information on all indexes, optionally limiting by table.

        :param table_name: The name of the table to use.
        :param emitter: The emitter to use.
        """
        raise NotImplementedError

    def get_upsert_sql(self, table_name: str,
                       *, on_conflict_update: bool=True) -> 'typing.Tuple[str, set]':
        """
        Get a formattable query and a set of required params to execute upsert-like functionality.

        :param table_name: The name of the table to upsert into.
        :param on_conflict_update: If this is to update on conflict.
        """
        raise NotImplementedError

    def transform_columns_to_indexes(self, *rows: 'DictRow', table_name: str):
        """
        Transform appropriate database rows to Column objects.

        :param rows: A list of :class:`.DictRow` objects returned from the database.
        :param table_name: The name of the table being transformed.
        """
        raise NotImplementedError

    def transform_rows_to_indexes(self, *rows: 'DictRow'):
        """
        Transform appropriate database rows to Index objects.

        :param rows: A list of :class:`.DictRow` objects returned from the database.
        """
        raise NotImplementedError


class BaseResultSet(collections.AsyncIterator, AsyncABC):
    """
    The base class for a result set. This represents the results from a database query, as an async
    iterable.

    Children classes must implement:

        - :attr:`.BaseResultSet.keys`
        - :attr:`.BaseResultSet.fetch_row`
        - :attr:`.BaseResultSet.fetch_many`
    """

    @property
    @abstractmethod
    def keys(self) -> typing.Iterable[str]:
        """
        :return: An iterable of keys that this query contained.
        """

    @abstractmethod
    async def fetch_row(self) -> 'DictRow':
        """
        Fetches the **next row** in this query.

        This should return None if the row could not be fetched.
        """

    @abstractmethod
    async def fetch_many(self, n: int) -> 'DictRow':
        """
        Fetches the **next N rows** in this query.

        :param n: The number of rows to fetch.
        """

    @abstractmethod
    async def close(self):
        """
        Closes this result set.
        """

    async def __anext__(self) -> 'DictRow':
        res = await self.fetch_row()
        if not res:
            raise StopAsyncIteration

        return res

    async def flatten(self) -> 'typing.List[DictRow]':
        """
        Flattens this ResultSet.

        :return: A list of :class:`.DictRow` objects.
        """
        rows = []
        async for row in self:
            rows.append(row)

        return rows

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.close()
        return False


class BaseTransaction(AsyncABC):
    """
    The base class for a transaction. This represents a database transaction (i.e SQL statements
    guarded with a BEGIN and a COMMIT/ROLLBACK).

    Children classes must implement:

        - :meth:`.BaseTransaction.begin`
        - :meth:`.BaseTransaction.rollback`
        - :meth:`.BaseTransaction.commit`
        - :meth:`.BaseTransaction.execute`
        - :meth:`.BaseTransaction.cursor`
        - :meth:`.BaseTransaction.close`

    Additionally, some extra methods can be implemented:

        - :meth:`.BaseTransaction.create_savepoint`
        - :meth:`.BaseTransaction.release_savepoint`

    These methods are not required to be implemented, but will raise :class:`NotImplementedError` if
    they are not.

    This class takes one parameter in the constructor: the :class:`.BaseConnector` used to connect
    to the DB server.
    """

    def __init__(self, connector: 'BaseConnector'):
        self.connector = connector

    async def __aenter__(self) -> 'BaseTransaction':
        await self.begin()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        try:
            if exc_type is not None:
                await self.rollback()
                return False

            await self.commit()
            return False
        finally:
            await self.close()

    @abstractmethod
    async def begin(self):
        """
        Begins the transaction, emitting a BEGIN instruction.
        """

    @abstractmethod
    async def rollback(self, checkpoint: str = None):
        """
        Rolls back the transaction.

        :param checkpoint: If provided, the checkpoint to rollback to. Otherwise, the entire \
            transaction will be rolled back.
        """

    @abstractmethod
    async def commit(self):
        """
        Commits the current transaction, emitting a COMMIT instruction.
        """

    @abstractmethod
    async def execute(self, sql: str, params: typing.Union[typing.Mapping, typing.Iterable] = None):
        """
        Executes SQL in the current transaction.

        :param sql: The SQL statement to execute.
        :param params: Any parameters to pass to the query.
        """

    @abstractmethod
    async def close(self, *, has_error: bool = False):
        """
        Called at the end of a transaction to cleanup.
        The connection will be released if there's no error; otherwise it will be closed.

        :param has_error: If the transaction has an error.
        """

    @abstractmethod
    async def cursor(self, sql: str, params: typing.Union[typing.Mapping, typing.Iterable] = None) \
            -> 'BaseResultSet':
        """
        Executes SQL and returns a database cursor for the rows.

        :param sql: The SQL statement to execute.
        :param params: Any parameters to pass to the query.
        :return: The :class:`.BaseResultSet` returned from the query, if applicable.
        """

    def create_savepoint(self, name: str):
        """
        Creates a savepoint in the current transaction.

        .. warning::
            This is not supported in all DB engines. If so, this will raise
            :class:`NotImplementedError`.

        :param name: The name of the savepoint to create.
        """
        raise NotImplementedError

    def release_savepoint(self, name: str):
        """
        Releases a savepoint in the current transaction.

        :param name: The name of the savepoint to release.
        """
        raise NotImplementedError


class BaseConnector(AsyncABC):
    """
    The base class for a connector. This should be used for all connector classes as the parent
    class.

    Children classes must implement:

        - :meth:`.BaseConnector.connect`
        - :meth:`.BaseConnector.close`
        - :meth:`.BaseConnector.emit_param`
        - :meth:`.BaseConnector.get_transaction`
        - :meth:`.BaseConnector.get_db_server_info`
    """

    def __init__(self, dsn: ParseResult, *, loop: asyncio.AbstractEventLoop = None):
        """
        :param dsn: The :class:`urllib.parse.ParseResult` created from parsing a DSN.
        """
        self.loop = loop or asyncio.get_event_loop()

        self._parse_result = dsn
        self.dsn = dsn.geturl()
        self.host = dsn.hostname
        self.port = dsn.port
        self.username = dsn.username
        self.password = dsn.password
        self.db = dsn.path[1:]
        self.params = {k: v[0] for k, v in parse_qs(dsn.query).items()}

    @abstractmethod
    async def connect(self) -> 'BaseConnector':
        """
        Connects the current connector to the database server. This is called automatically by the
        :class:`.DatabaseInterface

        :return: The original BaseConnector instance.
        """

    @abstractmethod
    async def close(self):
        """
        Closes the current Connector.
        """

    @abstractmethod
    def get_transaction(self) -> BaseTransaction:
        """
        Gets a new transaction object for this connection.

        :return: A new :class:`~.BaseTransaction` object attached to this connection.
        """

    @abstractmethod
    def emit_param(self, name: str) -> str:
        """
        Emits a parameter that can be used as a substitute during a query.

        :param name: The name of the parameter.
        :return: A string that represents the substitute to be placed in the query.
        """
    @abstractmethod
    async def get_db_server_version(self) -> str:
        """
        Gets the version of the DB server running.
        """

# python 3.5 dicts are unordered
# so we inherit from OrderedDict instead of dict
# also, python 3.6+ dicts aren't technically ordered
# it's just a side effect
class DictRow(OrderedDict):
    """
    Represents a row returned from a base result set, in dict form.

    This class allows for accessing both via key and index.
    """
    def __getitem__(self, item):
        if isinstance(item, int):
            try:
                return list(self.values())[item]
            except IndexError:
                raise KeyError(item)

        return super().__getitem__(item)

    def __setitem__(self, key, value, **kwargs):
        if isinstance(key, int):
            # find the actual string key at position ``key``
            # then set the item using said dict key
            d_key = list(self.keys())[key]
            return super().__setitem__(d_key, value, **kwargs)

        return super().__setitem__(key, value, **kwargs)
