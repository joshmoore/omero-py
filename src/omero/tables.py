#!/usr/bin/env python
#
# OMERO Tables Interface
# Copyright 2009 Glencoe Software, Inc.  All Rights Reserved.
# Use is subject to license terms supplied in LICENSE.txt
#

import os
import Ice
import time
import numpy
import signal
import logging
import threading
import traceback
import subprocess
import exceptions
import portalocker # Third-party

from path import path


import omero # Do we need both??
import omero.clients

# For ease of use
from omero.columns import *
from omero.rtypes import *
from omero.util.decorators import remoted, locked, perf
from omero_ext.functional import wraps


sys = __import__("sys") # Python sys
tables = __import__("tables") # Pytables

def slen(rv):
    """
    Returns the length of the argument or None
    if the argument is None
    """
    if rv is None:
        return None
    return len(rv)

def stamped(func, update = False):
    """
    Decorator which takes the first argument after "self" and compares
    that to the last modification time. If the stamp is older, then the
    method call will throw an omero.OptimisticLockException. Otherwise,
    execution will complete normally. If update is True, then the
    last modification time will be updated after the method call if it
    is successful.

    Note: stamped implies locked

    """
    def check_and_update_stamp(*args, **kwargs):
        self = args[0]
        stamp = args[1]
        if stamp < self._stamp:
            raise omero.OptimisticLockException(None, None, "Resource modified by another thread")

        try:
            return func(*args, **kwargs)
        finally:
            if update:
                self._stamp = time.time()
    checked_and_update_stamp = wraps(func)(check_and_update_stamp)
    return locked(check_and_update_stamp)


class HdfList(object):
    """
    Since two calls to tables.openFile() return non-equal files
    with equal fileno's, portalocker cannot be used to prevent
    the creation of two HdfStorage instances from the same
    Python process.
    """

    def __init__(self):
        self._lock = threading.RLock()
        self.__filenos = {}
        self.__paths = {}

    @locked
    def addOrThrow(self, hdfpath, hdffile, hdfstorage, action):
        fileno = hdffile.fileno()
        if fileno in self.__filenos.keys():
            raise omero.LockTimeout(None, None, "File already opened by process: %s" % hdfpath, 0)
        else:
            self.__filenos[fileno] = hdfstorage
            self.__paths[hdfpath] = hdfstorage
            action()

    @locked
    def getOrCreate(self, hdfpath):
        try:
            return self.__paths[hdfpath]
        except KeyError:
            return HdfStorage(hdfpath) # Adds itself.

    @locked
    def remove(self, hdfpath, hdffile):
        del self.__filenos[hdffile.fileno()]
        del self.__paths[hdfpath]

# Global object for maintaining files
HDFLIST = HdfList()

class HdfStorage(object):
    """
    Provides HDF-storage for measurement results. At most a single
    instance will be available for any given physical HDF5 file.
    """


    def __init__(self, file_path):

        """
        file_path should be the path to a file in a valid directory where
        this HDF instance can be stored (Not None or Empty). Once this
        method is finished, self.__hdf_file is guaranteed to be a PyTables HDF
        file, but not necessarily initialized.
        """

        if file_path is None or str(file_path) == "":
            raise omero.ValidationException(None, None, "Invalid file_path")

        self.logger = logging.getLogger("omero.tables.HdfStorage")
        self.__hdf_path = path(file_path)
        self.__hdf_file = self.__openfile("a")
        self.__tables = []

        self._lock = threading.RLock()
        self._stamp = time.time()

        # These are what we'd like to have
        self.__mea = None
        self.__ome = None

        # Now we try to lock the file, if this fails, we rollback
        # any previous initialization (opening the file)
        try:
            fileno = self.__hdf_file.fileno()
            HDFLIST.addOrThrow(self.__hdf_path, self.__hdf_file, self,\
                lambda: portalocker.lock(self.__hdf_file, portalocker.LOCK_NB|portalocker.LOCK_EX))
        except portalocker.LockException, le:
            self.cleanup()
            raise omero.LockTimeout(None, None, "Cannot acquire exclusive lock on: %s" % self.__hdf_path, 0)

        try:
            self.__ome = self.__hdf_file.root.OME
            self.__mea = self.__ome.Measurements
            self.__types = self.__ome.ColumnTypes[:]
            self.__descriptions = self.__ome.ColumnDescriptions[:]
            self.__initialized = True
        except tables.NoSuchNodeError:
            self.__initialized = False

    #
    # Non-locked methods
    #

    def __openfile(self, mode):
        try:
            return tables.openFile(self.__hdf_path, mode=mode, title="OMERO HDF Measurement Storege", rootUEP="/")
        except IOError, io:
            msg = "HDFStorage initialized with bad path: %s" % self.__hdf_path
            self.logger.error(msg)
            raise omero.ValidationException(None, None, msg)

    def __initcheck(self):
        if not self.__initialized:
            raise omero.ApiUsageException(None, None, "Not yet initialized")

    #
    # Locked methods
    #

    @locked
    def initialize(self, cols, metadata = {}):
        """

        """

        if self.__initialized:
            raise omero.ValidationException(None, None, "Already initialized.")

        self.__definition = columns2definition(cols)
        self.__ome = self.__hdf_file.createGroup("/", "OME")
        self.__mea = self.__hdf_file.createTable(self.__ome, "Measurements", self.__definition)

        self.__types = [ x.ice_staticId() for x in cols ]
        self.__descriptions = [ (x.description != None) and x.description or "" for x in cols ]
        self.__hdf_file.createArray(self.__ome, "ColumnTypes", self.__types)
        self.__hdf_file.createArray(self.__ome, "ColumnDescriptions", self.__descriptions)

        self.__mea.attrs.version = "v1"
        self.__mea.attrs.initialized = time.time()
        if metadata:
            for k, v in metadata.items():
                self.__mea.attrs[k] = v
                # See attrs._f_list("user") to retrieve these.

        self.__mea.flush()
        self.__hdf_file.flush()
        self.__initialized = True

    @locked
    def incr(self, table):
        sz = len(self.__tables)
        self.logger.info("Size: %s - Attaching %s to %s" % (sz, table, self.__hdf_path))
        if table in self.__tables:
            self.logger.warn("Already added")
            raise omero.ApiUsageException(None, Non, "Already added")
        self.__tables.append(table)
        return sz + 1

    @locked
    def decr(self, table):
        sz = len(self.__tables)
        self.logger.info("Size: %s - Detaching %s from %s", sz, table, self.__hdf_path)
        if not (table in self.__tables):
            self.logger.warn("Unknown table")
            raise omero.ApiUsageException(None, None, "Unknown table")
        self.__tables.remove(table)
        if sz <= 1:
            self.cleanup()
        return sz - 1

    @locked
    def uptodate(self, stamp):
        return self._stamp <= stamp

    @locked
    def rows(self):
        self.__initcheck()
        return self.__mea.nrows

    @locked
    def cols(self, size, current):
        self.__initcheck()
        ic = current.adapter.getCommunicator()
        types = self.__types
        names = self.__mea.colnames
        cols = []
        for i in range(len(types)):
            t = types[i]
            n = names[i]
            try:
                col = ic.findObjectFactory(t).create(t)
                col.name = n
                col.size(size)
                cols.append(col)
            except:
                msg = traceback.format_exc()
                raise omero.ValidationException(None, msg, "BAD COLUMN TYPE: %s for %s" % (t,n))
        return cols

    @locked
    def meta(self):
        self.__initcheck()
        metadata = {}
        attr = self.__mea.attrs
        keys = list(self.__mea.attrs._v_attrnamesuser)
        for key in keys:
            val = attr[key]
            if type(val) == numpy.float64:
                val = rfloat(val)
            elif type(val) == numpy.int32:
                val = rint(val)
            elif type(val) == numpy.string_:
                val = rstring(val)
            else:
                raise omero.ValidationException("BAD TYPE: %s" % type(val))
            metadata[key] = val

    @locked
    def append(self, cols):
        # Optimize!
        arrays = []
        names = []
        for col in cols:
            names.append(col.name)
            arrays.append(col.array())
        data = numpy.rec.fromarrays(arrays, names=names)
        self.__mea.append(data)
        self.__mea.flush()

    #
    # Stamped methods
    #

    @stamped
    def getWhereList(self, stamp, condition, variables, unused, start, stop, step):
        self.__initcheck()
        return self.__mea.getWhereList(condition, variables, None, start, stop, step).tolist()

    def _data(self, cols, rowNumbers):
        data = omero.grid.Data()
        data.columns = cols
        data.rowNumbers = rowNumbers
        data.lastModification = long(self._stamp*1000) # Convert to millis since epoch
        return data

    @stamped
    def readCoordinates(self, stamp, rowNumbers, current):
        self.__initcheck()
        rows = self.__mea.readCoordinates(rowNumbers)
        cols = self.cols(None, current)
        for col in cols:
            col.values = rows[col.name].tolist()
        return self._data(cols, rowNumbers)

    @stamped
    def slice(self, stamp, colNumbers, rowNumbers, current):
        self.__initcheck()
        if rowNumbers is None or len(rowNumbers) == 0:
            rows = self.__mea.read()
        else:
            rows = self.__mea.readCoordinates(rowNumbers)
        cols = self.cols(None, current)
        rv   = []
        for i in range(len(cols)):
            if colNumbers is None or len(colNumbers) == 0 or i in colNumbers:
                col = cols[i]
                col.values = rows[col.name].tolist()
                rv.append(col)
        return self._data(rv, rowNumbers)

    #
    # Lifecycle methods
    #

    def check(self):
        return True

    @locked
    def cleanup(self):
        self.logger.info("Cleaning storage: %s", self.__hdf_path)
        if self.__mea:
            self.__mea.flush()
            self.__mea = None
        if self.__ome:
            self.__ome = None
        if self.__hdf_file:
            HDFLIST.remove(self.__hdf_path, self.__hdf_file)
        hdffile = self.__hdf_file
        self.__hdf_file = None
        hdffile.close() # Resources freed

# End class HdfStorage


class TableI(omero.grid.Table, omero.util.SimpleServant):
    """
    Spreadsheet implementation based on pytables.
    """

    def __init__(self, ctx, file_obj, storage, uuid = "unknown"):
        self.uuid = uuid
        self.file_obj = file_obj
        self.stamp = time.time()
        self.storage = storage
        omero.util.SimpleServant.__init__(self, ctx)
        self.storage.incr(self)

    def check(self):
        """
        Called periodically to check the resource is alive. Returns
        False if this resource can be cleaned up. (Resources API)
        """
        self.logger.debug("Checking %s" % self)
        return False

    def cleanup(self):
        """
        Decrements the counter on the held storage to allow it to
        be cleaned up.
        """
        if self.storage:
            try:
                self.storage.decr(self)
            finally:
                self.storage = None

    def __str__(self):
        return "Table-%s" % self.uuid

    @remoted
    @perf
    def close(self, current = None):
        try:
            self.cleanup()
            self.logger.info("Closed %s", self)
        except:
            self.logger.warn("Closed %s with errors", self)

    # TABLES READ API ============================

    @remoted
    @perf
    def getOriginalFile(self, current = None):
        self.logger.info("%s.getOriginalFile() => %s", self, self.file_obj)
        return self.file_obj

    @remoted
    @perf
    def getHeaders(self, current = None):
        rv = self.storage.cols(None, current)
        self.logger.info("%s.getHeaders() => size=%s", self, slen(rv))
        return rv

    @remoted
    @perf
    def getMetadata(self, current = None):
        rv = self.storage.meta()
        self.logger.info("%s.getMetadata() => size=%s", self, slen(rv))
        return rv

    @remoted
    @perf
    def getNumberOfRows(self, current = None):
        rv = self.storage.rows()
        self.logger.info("%s.getNumberOfRows() => %s", self, rv)
        return long(rv)

    @remoted
    @perf
    def getWhereList(self, condition, variables, start, stop, step, current = None):
        if stop == 0:
            stop = None
        if step == 0:
            step = None
        rv = self.storage.getWhereList(self.stamp, condition, variables, None, start, stop, step)
        self.logger.info("%s.getWhereList(%s, %s, %s, %s, %s) => size=%s", self, condition, variables, start, stop, step, slen(rv))
        return rv

    @remoted
    @perf
    def readCoordinates(self, rowNumbers, current = None):
        self.logger.info("%s.readCoordinates(size=%s)", self, slen(rowNumbers))
        self.storage.readCoordinates(self.stamp, rowNumbers, current)

    @remoted
    @perf
    def slice(self, colNumbers, rowNumbers, current = None):
        self.logger.info("%s.slice(size=%s, size=%s)", self, slen(colNumbers), slen(rowNumbers))
        return self.storage.slice(self.stamp, colNumbers, rowNumbers, current)

    # TABLES WRITE API ===========================

    @remoted
    @perf
    def initialize(self, cols, current = None):
        self.storage.initialize(cols)
        if cols:
            self.logger.info("Initialized %s with %s cols", self, slen(cols))

    @remoted
    @perf
    def addColumn(self, col, current = None):
        raise omero.ApiUsageException(None, None, "NYI")

    @remoted
    @perf
    def addData(self, cols, current = None):
        self.storage.append(cols)
        if cols and cols[0].values:
            self.logger.info("Added %s rows of data to %s", slen(cols[0].values), self)


class TablesI(omero.grid.Tables, omero.util.Servant):
    """
    Implementation of the omero.grid.Tables API. Provides
    spreadsheet like functionality across the OMERO.grid.
    This servant serves as a session-less, user-less
    resource for obtaining omero.grid.Table proxies.

    The first major step in initialization is getting
    a session. This will block until the Blitz server
    is reachable.
    """

    def __init__(self,\
        ctx,\
        table_cast = omero.grid.TablePrx.uncheckedCast,\
        internal_repo_cast = omero.grid.InternalRepositoryPrx.checkedCast):

        omero.util.Servant.__init__(self, ctx, needs_session = True)

        # Storing these methods, mainly to allow overriding via
        # test methods. Static methods are evil.
        self._table_cast = table_cast
        self._internal_repo_cast = internal_repo_cast

        self.__stores = []
        self._get_dir()
        self._get_uuid()
        self._get_repo()

    def _get_dir(self):
        """
        Second step in initialization is to find the .omero/repository
        directory. If this is not created, then a required server has
        not started, and so this instance will not start.
        """
        wait = int(self.communicator.getProperties().getPropertyWithDefault("omero.repo.wait", "1"))
        self.repo_dir = self.communicator.getProperties().getProperty("omero.repo.dir")

        if not self.repo_dir:
            # Implies this is the legacy directory. Obtain from server
            self.repo_dir = self.ctx.getSession().getConfigService().getConfigValue("omero.data.dir")

        self.repo_cfg = path(self.repo_dir) / ".omero" / "repository"
        start = time.time()
        while not self.repo_cfg.exists() and wait < (time.time() - start):
            self.logger.info("%s doesn't exist; waiting 5 seconds..." % self.repo_cfg)
            time.sleep(5)
            count -= 1
        if not self.repo_cfg.exists():
            msg = "No repository found: %s" % self.repo_cfg
            self.logger.error(msg)
            raise omero.ResourceError(None, None, msg)

    def _get_uuid(self):
        """
        Third step in initialization is to find the database uuid
        for this grid instance. Multiple OMERO.grids could be watching
        the same directory.
        """
        cfg = self.ctx.getSession().getConfigService()
        self.db_uuid = cfg.getDatabaseUuid()
        self.instance = self.repo_cfg / self.db_uuid

    def _get_repo(self):
        """
        Fourth step in initialization is to find the repository object
        for the UUID found in .omero/repository/<db_uuid>, and then
        create a proxy for the InternalRepository attached to that.
        """

        # Get and parse the uuid from the RandomAccessFile format from FileMaker
        self.repo_uuid = (self.instance / "repo_uuid").lines()[0].strip()
        if len(self.repo_uuid) != 38:
            raise omero.ResourceError("Poorly formed UUID: %s" % self.repo_uuid)
        self.repo_uuid = self.repo_uuid[2:]

        # Using the repo_uuid, find our OriginalFile object
        self.repo_obj = self.ctx.getSession().getQueryService().findByQuery("select f from OriginalFile f where sha1 = :uuid",
            omero.sys.ParametersI().add("uuid", rstring(self.repo_uuid)))
        self.repo_mgr = self.communicator.stringToProxy("InternalRepository-%s" % self.repo_uuid)
        self.repo_mgr = self._internal_repo_cast(self.repo_mgr)
        self.repo_svc = self.repo_mgr.getProxy()

    @remoted
    def getRepository(self, current = None):
        """
        Returns the Repository object for this Tables server.
        """
        return self.repo_svc

    @remoted
    @perf
    def getTable(self, file_obj, current = None):
        """
        Create and/or register a table servant.
        """

        # Will throw an exception if not allowed.
        self.logger.info("getTable: %s", (file_obj and file_obj.id and file_obj.id.val))
        file_path = self.repo_mgr.getFilePath(file_obj)
        p = path(file_path).dirname()
        if not p.exists():
            p.makedirs()

        storage = HDFLIST.getOrCreate(file_path)
        id = Ice.Identity()
        id.name = Ice.generateUUID()
        table = TableI(self.ctx, file_obj, storage, uuid = id.name)
        self.resources.add(table)

        prx = current.adapter.add(table, id)
        return self._table_cast(prx)
