"""
Module containing the actual query
"""
import logging
import typing

from katagawa.engine.base import ResultSet
from katagawa.engine.transaction import Transaction
from katagawa.exceptions import TableConflictException
from katagawa.orm.column import _Operator, _CombinatorOperator
from katagawa.orm.table import Table
from katagawa import session as md_sess
from katagawa.sql.dialects.common import Select, Column, From, Where, Operator

logger = logging.getLogger("Katagawa.query")


class BaseQuery(object):
    """
    A BaseQuery object is used to query the database with SELECT statements or otherwise.

    It is produced from a :meth:`.Session.query` and is used to actually query the database.
    """
    def __init__(self, session: 'md_sess.Session', table: 'Table', **kwargs):
        """
        Creates a new BaseQuery.

        :param session: The session to bind this query to.
        """
        self.session = session  # type: md_sess.Session

        #: The table being queried.
        self.from_ = table

        # Define a dict of tables to access in this query.
        self.tables = {}

        # Define a list of conditions to generate in the SELECT.
        self.conditions = []

    # internal workhouse methods

    # query methods

    def select(self, *tables: Table):
        """
        Selects some tables to query.

        :param tables: A list of DeclarativeMeta or aliased tables to query.
        :return: Ourself.
        """
        for table in tables:
            if not isinstance(table, Table):
                raise TypeError("Table must be instance of Table")

            self.tables[table.name] = table

        return self

    def where(self, *conditions):
        """
        Adds conditions to the query.

        :param conditions: A list of field operators to query.
        :return: Ourself.
        """
        for condition in conditions:
            self.conditions.append(condition)

        return self

    # sql methods
    def get_token(self) -> typing.Tuple[Select, dict]:
        """
        Gets the Select tokens for this query.
        """
        # get the fields
        fields = []

        # add the main table's fields
        for column in self.from_.columns:
            fields.append(
                Column('"{}"."{}"'.format(self.from_.name, column.name))
            )

        # add any joined tables fields
        for tbl_name, table in self.tables.items():
            for column in table.columns:
                fields.append(Column(
                    '"{}"."{}"'.format(table.name, column.name))
                )

        s = Select(
            subtokens=[
                # expand out the fields and tables as From queries
                *fields,
                From(self.from_.name)
            ]
        )

        # update subfields with WHERE query
        params = {}
        if self.conditions:
            where = Where()
            param_count = 0
            for op in self.conditions:
                if isinstance(op, _Operator):
                    ops = [op]
                elif isinstance(op, _CombinatorOperator):
                    ops = op.operators

                final = op.get_token()

                for nop in ops:
                    o = nop.get_token()

                    # update parameterized queries
                    if isinstance(o.value, str):
                        name = "param_{}".format(param_count)
                        params[name] = o.value
                        o.value = "{{{n}}}".format(n=name)
                        param_count += 1

                where.subtokens.append(final)

            s.subtokens.append(where)

        return s, params

    # return methods

    async def all(self) -> typing.Generator[typing.Mapping, None, None]:
        """
        Gets
        """
        r = await self.session.execute(self)

        async for result in r:
            yield result

    async def first(self):
        """
        Returns the first result that matches.
        """
        rset = await self.session.execute(self)
        next = await rset.get_next()

        return next
