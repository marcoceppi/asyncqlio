"""
Classes to do with tables.
"""
import inspect
import logging
import typing
import sys

from cached_property import cached_property

from katagawa.orm import session as md_session
from katagawa.orm import operators as md_operators
from katagawa.exc import NoSuchColumnError
from katagawa.orm.types import ColumnType

PY36 = sys.version_info[0:2] >= (3, 6)
logger = logging.getLogger(__name__)


class Column(object):
    """
    Represents a column in a table in a database.

    .. code-block:: python
        class MyTable(Table):
            id = Column(Integer, primary_key=True)
            
    The ``id`` column will mirror the ID of records in the table when fetching, etc. and can be set 
    on a record when storing in a table.
    
    .. code-block:: python
        sess = db.get_session()
        user = await sess.select(User).where(User.id == 2).first()
        
        print(user.id)  # 2

    """

    def __init__(self, type_: typing.Union[ColumnType, typing.Type[ColumnType]], *,
                 primary_key: bool = False,
                 nullable: bool = True,
                 default: typing.Any = None,
                 autoincrement: bool = False,
                 index: bool = True,
                 unique: bool = True):
        """
        :param type_:
            The :class:`.ColumnType` that represents the type of this column.
         
        :param primary_key: 
            Is this column the table's Primary Key (the unique identifier that identifies each row)?
            
        :param nullable: 
            Can this column be NULL?
            
        :param default:
            The client-side default for this column. If no value is provided when inserting, this 
            value will automatically be added to the insert query.
         
        :param autoincrement: 
            Should this column auto-increment? This will create a serial sequence.
        
        :param index: 
            Should this column be indexed?
        
        :param unique: 
            Is this column unique?
        """
        #: The name of the column.
        #: This can be manually set, or automatically set when set on a table.
        self.name = None  # type: str

        #: The :class:`.Table` instance this Column is associated with.
        self.table = None

        #: The :class:`.ColumnType` that represents the type of this column.
        self.type = type_

        #: The default for this column.
        self.default = default

        #: If this Column is a primary key.
        self.primary_key = primary_key

        #: If this Column is nullable.
        self.nullable = nullable

        #: If this Column is to autoincrement.
        self.autoincrement = autoincrement

        #: If this Column is indexed.
        self.indexed = index

        #: If this Column is unique.
        self.unique = unique

    def __hash__(self):
        return super().__hash__()

    def __set_name__(self, owner, name):
        """
        Called to update the table and the name of this Column.
        
        :param owner: The :class:`.Table` this Column is on. 
        :param name: The str name of this table.
        """
        logger.debug("Column created with name {} on {}".format(name, owner))
        self.name = name
        self.table = owner

    def __eq__(self, other: typing.Any) -> 'md_operators.Eq':
        return md_operators.Eq(self, other)

    @cached_property
    def quoted_name(self) -> str:
        """
        Gets the full quoted name for this column.
         
        This returns the column name in "table"."column" format.
        """
        return r'"{}"."{}"'.format(self.table.__tablename__, self.name)


# OO-like objects
class PrimaryKey(object):
    """
    Represents the primary key of a table.
    
    A primary key can be on any 1 to N columns in a table.
    
    .. code-block:: python
        class Something(Table):
            first_id = Column(Integer)
            second_id = Column(Integer)
            
        pkey = PrimaryKey(Something.first_id, Something.second_id)
        Something.primary_key = pkey
        
    Alternatively, the primary key can be automatically calculated by passing ``primary_key=True`` 
    to columns in their constructor:
    
    .. code-block:: python
        class Something(Table):
            id = Column(Integer, primary_key=True)
            
        print(Something.primary_key)
    """

    def __init__(self, *cols: 'Column'):
        #: A list of :class:`.Column` that this primary key encompasses.
        self.columns = list(cols)  # type: typing.List[Column]

        #: The table this primary key is bound to.
        self.table = None

    def __repr__(self):
        return "<PrimaryKey table='{}' columns='{}'>".format(self.table, self.columns)


# table BOLLUCKS
class TableMetaRoot(type):
    """
    The "root" metaclass for the OO-style table shenanigans. This class **should never be used** 
    outside of inside the library. It is used for keeping track of sub-table instances, and so on.
    
    .. code-block:: python
        class TableMeta(type, metaclass=TableMetaRoot):
            def __init__(self, name, bases, dict):
                super().__init__(name, bases, dict)
                # register the new table 
                self.register_new_table(self)
    """

    def __init__(self, name, bases, class_dict):
        super().__init__(name, bases, class_dict)

        #: The registry of 'Table name' -> 'Table type'.
        self._tbl_registry = {}

    def register_new_table(self, tbl: 'TableMetaRoot'):
        self._tbl_registry[tbl.__name__] = tbl


class TableRow(object):
    """
    Represents a single row in a table.  
    :class:`.Table` objects cannot be instantiated (not without hacking at the object level), so as
    such they return TableRow objects when called.
    
    TableRow objects are representative of a single row in said table - the column names are the 
    keys, and the value in that row are the items.
     
    .. code-block:: python
        class User(Table):
            id = Column(Integer, primary_key=True)
            
        user = User(id=1)  # user is actually a TableRow bound to the User table
    """

    def __init__(self, tbl):
        """
        :param tbl: The table object to bind this row to.
        """
        self._table = tbl

        #: If this row existed before.
        #: If this is True, this row was fetched from the DB previously.
        #: Otherwise, it is a fresh row.
        self.__existed = False

        #: The session this row is attached to.
        self._session = None  # type: md_session.Session

        #: A mapping of Column -> Previous values for this row.
        #: Used in update generation.
        self._previous_values = {}

        #: A mapping of Column -> Current value for this row.
        self._values = {}

        # BECAUSE PYTHON
        self.__setattr__ = self._setattr__

    def __repr__(self):
        gen = ("{}={}".format(col.name, self._get_column_value(col)) for col in self._table.columns)
        return "<{} {}>".format(self._table.__name__, " ".join(gen))

    def __getattr__(self, item):
        col = next(filter(lambda col: col.name == item, self._table.columns), None)
        if col is None:
            raise NoSuchColumnError(item)

        return self._values[col]

    def _setattr__(self, key, value):
        col = next(filter(lambda col: col.name == key, self._table.columns), None)
        if col is None:
            return super().__setattr__(key, value)

        return self.update_column(col, value)

    def _get_column_value(self, column: 'Column'):
        """
        Gets the value from the specified column in this row.
        """
        if column.table != self._table:
            raise ValueError("Column table must match row table")

        try:
            return self._values[column]
        except KeyError:
            return column.default

    def update_column(self, column: 'Column', value: typing.Any):
        """
        Updates the value of a column in this row.
        """
        if column not in self._previous_values:
            if column in self._values:
                self._previous_values[column] = self._values[column]

        self._values[column] = value

        return self

    @property
    def primary_key(self) -> typing.Union[typing.Any, typing.Iterable[typing.Any]]:
        """
        Gets the primary key for this row.
          
        If this table only has one primary key column, this property will be a single value.  
        If this table has multiple columns in a primary key, this property will be a tuple. 
        """
        pk = self._table.primary_key  # type: PrimaryKey
        result = []

        for col in pk.columns:
            val = self._get_column_value(col)
            result.append(val)

        if len(result) == 1:
            return result[0]

        return tuple(result)


def table_base(name: str = "Table", bases=(object,)):
    """
    Gets a new base object to use for OO-style tables.  
    This object is the parent of all tables created in the object-oriented style; it provides some 
    key configuration to the relationship calculator and the Katagawa object itself.
    
    To use this object, you call this function to create the new object, and subclass it in your 
    table classes:
    
    .. code-block:: python
        Table = table_base()
        
        class User(Table):
            ...
            
    Binding the base object to the database object is essential for querying:
    
    .. code-block:: python
        # ensure the table is bound to that database
        db.bind_tables(Table)
        
        # now we can do queries
        sess = db.get_session()
        user = await sess.select(User).where(User.id == 2).first()
    
    Each Table object is associated with a database interface, which it uses for special querying
    inside the object, such as :meth:`.Table.get`.
    
    .. code-block:: python
        class User(Table):
            id = Column(Integer, primary_key=True)
            ...
        
        db.bind_tables(Table)    
        # later on, in some worker code
        user = await User.get(1)
    
    :param name: The name of the new class to produce. By default, it is ``Table``.
    :param bases: An iterable of classes for the Table object to inherit from.
    :return: A new Table class that can be used for OO tables.
    """

    # metaclass is defined inside a function because we need to add specific-state to it
    class TableMeta(type, metaclass=TableMetaRoot):
        def __new__(mcs, n, b, c, register: bool = True):
            return type.__new__(mcs, n, b, c)

        def __init__(self, tblname: str, tblbases: tuple, class_body: dict, register: bool = True):
            """
            Creates a new Table instance. 
            """
            # table metaclassery shit
            # calculate the new bases
            new_bases = tuple(list(tblbases) + list(bases))
            super().__init__(tblname, new_bases, class_body)

            if not PY36:
                # emulate __set_name__ for descriptors on python 3.5
                for name, value in class_body.items():
                    if hasattr(value, "__set_name__"):
                        value.__set_name__(self, name)

            if register is False:
                return
            logger.debug("Registered new table {}".format(tblname))
            TableMeta.register_new_table(self)

            # ================ #
            # TABLE ATTRIBUTES #
            # ================ #

            try:
                self.__tablename__
            except AttributeError:
                #: The name of this table.
                self.__tablename__ = tblname.lower()

            #: The primary key for this table.
            #: This should be a :class:`.PrimaryKey`.
            self._primary_key = self._calculate_primary_key()

            #: The :class:`.Katagawa` this table is bound to.
            self.__bind = None

        def __call__(self, *args, **kwargs):
            return self._get_table_row(**kwargs)

        @property
        def columns(self) -> 'typing.List[Column]':
            """
            :return: A list of :class:`.Column` this Table has. 
            """
            return list(self.iter_columns())

        def iter_columns(self) -> typing.Generator['Column', None, None]:
            """
            :return: A generator that yields :class:`.Column` objects for this table. 
            """
            for name, col in inspect.getmembers(self, predicate=lambda x: isinstance(x, Column)):
                yield col

        def _calculate_primary_key(self) -> PrimaryKey:
            """
            Calculates the current primary key for a table, given all the columns.
            
            If no columns are marked as a primary key, the key will not be generated.
            """
            pk_cols = []
            for col in self.iter_columns():
                if col.primary_key is True:
                    pk_cols.append(col)

            if pk_cols:
                pk = PrimaryKey(*pk_cols)
                pk.table = self
                logger.debug("Calculated new primary key {}".format(pk))
                return pk

            return None

        @property
        def primary_key(self) -> PrimaryKey:
            """
            :getter: The :class:`.PrimaryKey` for this table.
            :setter: A new :class:.PrimaryKey` for this table.
            
            .. note::
                A primary key will automatically be calculated from columns at define time, if any
                columns have ``primary_key`` set to True.
            """
            return self._primary_key

        @primary_key.setter
        def primary_key(self, key: PrimaryKey):
            key.table = self
            self._primary_key = key

        def _get_table_row(self, **kwargs) -> 'TableRow':
            """
            Gets a :class:`.TableRow` that represents this table.
            """
            col_map = {col.name: col for col in self.columns}
            row = TableRow(tbl=self)

            for name, val in kwargs.items():
                if name not in col_map:
                    raise NoSuchColumnError(name)

                row.update_column(col_map[name], val)

            return row

    class Table(metaclass=TableMeta, register=False):
        pass

    Table.__name__ = name
    return Table