#!/usr/bin/env python

"""
   Library for integration tests

   Copyright 2008 Glencoe Software, Inc. All rights reserved.
   Use is subject to license terms supplied in LICENSE.txt

"""

import os
import Ice
import sys
import time
import weakref
import unittest
import tempfile
import traceback
import exceptions
import subprocess

import omero

from omero.util.temp_files import create_path
from omero.rtypes import rstring, rtime
from path import path


class Clients(object):

    def __init__(self):
        self.__clients = set()

    def  __del__(self):
        try:
            for client_ref in self.__clients:
                client = client_ref()
                if client:
                    client.__del__()
        finally:
            self.__clients = set()

    def add(self, client):
        self.__clients.add(weakref.ref(client))


class ITest(unittest.TestCase):

    def setUp(self):

        self.OmeroPy = self.omeropydir()

        self.__clients = Clients()

        p = Ice.createProperties(sys.argv)
        rootpass = p.getProperty("omero.rootpass")

        name = None
        pasw = None
        if rootpass:
            self.root = omero.client()
            self.__clients.add(self.root)
            self.root.setAgent("OMERO.py.root_test")
            self.root.createSession("root", rootpass)
            newuser = self.new_user()
            name = newuser.omeName.val
            pasw = "1"
        else:
            self.root = None

        self.client = omero.client()
        self.__clients.add(self.client)
        self.client.setAgent("OMERO.py.test")
        self.sf = self.client.createSession(name, pasw)

        self.update = self.sf.getUpdateService()
        self.query = self.sf.getQueryService()


    def omeropydir(self):
        count = 10
        searched = []
        p = path(".").abspath()
        while str(p.basename()) not in ("OmeroPy", ""): # "" means top of directory
            searched.append(p)
            p = p / ".." # Walk up, in case test runner entered a subdirectory
            p = p.abspath()
            count -= 1
            if not count:
                break
        if str(p.basename()) == "OmeroPy":
            return p
        else:
            self.fail("Could not find OmeroPy/; searched %s" % searched)

    def uuid(self):
        import omero_ext.uuid as _uuid # see ticket:3774
        return str(_uuid.uuid4())

    def login_args(self):
        p = self.client.ic.getProperties()
        host = p.getProperty("omero.host")
        port = p.getProperty("omero.port")
        key = self.sf.ice_getIdentity().name
        return ["-s", host, "-k", key, "-p", port]

    def root_login_args(self):
        p = self.root.ic.getProperties()
        host = p.getProperty("omero.host")
        port = p.getProperty("omero.port")
        key = self.root.sf.ice_getIdentity().name
        return ["-s", host, "-k", key, "-p", port]

    def tmpfile(self):
        return str(create_path())

    def new_group(self, experimenters = None, perms = None):
        admin = self.root.sf.getAdminService()
        gname = self.uuid()
        group = omero.model.ExperimenterGroupI()
        group.name = rstring(gname)
        if perms:
            group.details.permissions = omero.model.PermissionsI(perms)
        gid = admin.createGroup(group)
        group = admin.getGroup(gid)
        if experimenters:
            for exp in experimenters:
                user, name = self.user_and_name(exp)
                admin.addGroups(user, [group])
        return group

    def new_image(self, name = ""):
        img = omero.model.ImageI()
        img.name = rstring(name)
        img.acquisitionDate = rtime(0)
        return img

    def import_image(self, filename = None):
        if filename is None:
            filename = self.OmeroPy / ".." / ".." / ".." / "components" / "common" / "test" / "tinyTest.d3d.dv"

        server = self.client.getProperty("omero.host")
        port = self.client.getProperty("omero.port")
        key = self.client.getSessionId()

        # Search up until we find "OmeroPy"
        dist_dir = self.OmeroPy / ".." / ".." / ".." / "dist"
        args = ["python"]
        args.append(str(path(".") / "bin" / "omero"))
        args.extend(["-s", server, "-k", key, "-p", port, "import", filename])
        popen = subprocess.Popen(args, cwd=str(dist_dir), stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        out, err = popen.communicate()
        rc = popen.wait()
        if rc != 0:
            raise exceptions.Exception("import failed: [%r] %s\n%s" % (args, rc, err))
        pix_ids = []
        for x in out.split("\n"):
            if x and x.find("Created") < 0 and x.find("#") < 0:
                try:    # if the line has an image ID...
                    imageId = str(long(x.strip()))
                    pix_ids.append(imageId)
                except: pass
        return pix_ids
    
    
    def createTestImage(self, sizeX = 256, sizeY = 256, sizeZ = 5, sizeC = 3, sizeT = 1):
        """
        Creates a test image of the required dimensions, where each pixel value is set 
        to the value of x+y. 
        Returns the image (omero.model.ImageI)
        """
        from numpy import fromfunction, int16
        from omero.util import script_utils
        import random
        
        session = self.root.sf
        gateway = session.createGateway()
        renderingEngine = session.createRenderingEngine()
        queryService = session.getQueryService()
        pixelsService = session.getPixelsService()
        rawPixelStore = session.createRawPixelsStore()

        def f1(x,y):
            return y
        def f2(x,y):
            return (x+y)/2
        def f3(x,y):
            return x

        pType = "int16"
        # look up the PixelsType object from DB
        pixelsType = queryService.findByQuery("from PixelsType as p where p.value='%s'" % pType, None) # omero::model::PixelsType
        if pixelsType == None and pType.startswith("float"):    # e.g. float32
            pixelsType = queryService.findByQuery("from PixelsType as p where p.value='%s'" % "float", None) # omero::model::PixelsType
        if pixelsType == None:
            print "Unknown pixels type for: " % pType
            return

        # code below here is very similar to combineImages.py
        # create an image in OMERO and populate the planes with numpy 2D arrays
        channelList = range(sizeC)
        iId = pixelsService.createImage(sizeX, sizeY, sizeZ, sizeT, channelList, pixelsType, "testImage", "description")
        image = gateway.getImage(iId.getValue())

        pixelsId = image.getPrimaryPixels().getId().getValue()
        rawPixelStore.setPixelsId(pixelsId, True)

        colourMap = {0: (0,0,255,255), 1:(0,255,0,255), 2:(255,0,0,255), 3:(255,0,255,255)}
        fList = [f1, f2, f3]
        for theC in range(sizeC):
            minValue = 0
            maxValue = 0
            f = fList[theC % len(fList)]
            for theZ in range(sizeZ):
                for theT in range(sizeT):
                    plane2D = fromfunction(f,(sizeY,sizeX),dtype=int16)
                    print plane2D
                    script_utils.uploadPlane(rawPixelStore, plane2D, theZ, theC, theT)
                    minValue = min(minValue, plane2D.min())
                    maxValue = max(maxValue, plane2D.max())
            pixelsService.setChannelGlobalMinMax(pixelsId, theC, float(minValue), float(maxValue))
            rgba = None
            if theC in colourMap:
                rgba = colourMap[theC]
            script_utils.resetRenderingSettings(renderingEngine, pixelsId, theC, minValue, maxValue, rgba)

        return image

    def index(self, *objs):
        if objs:
            for obj in objs:
                self.root.sf.getUpdateService().indexObject(obj, {"omero.group":"-1"})

    def new_user(self, group = None, perms = None, admin = False):

        if not self.root:
            raise exceptions.Exception("No root client. Cannot create user")

        admin = self.root.getSession().getAdminService()
        name = self.uuid()

        # Create group if necessary
        if not group:
            g = self.new_group(perms = perms)
            group = g.name.val
        else:
            g, group = self.group_and_name(group)

        # Create user
        e = omero.model.ExperimenterI()
        e.omeName = rstring(name)
        e.firstName = rstring(name)
        e.lastName = rstring(name)
        uid = admin.createUser(e, group)
        e = admin.lookupExperimenter(name)
        if admin:
            admin.setGroupOwner(g, e)
        return admin.getExperimenter(uid)

    def new_client(self, group = None, user = None, perms = None, admin = False):
        """
        Like new_user() but returns an active client.
        """
        if user is None:
            user = self.new_user(group, perms, admin)
        props = self.root.getPropertyMap()
        props["omero.user"] = user.omeName.val
        props["omero.pass"] = user.omeName.val
        client = omero.client(props)
        self.__clients.add(client)
        client.setAgent("OMERO.py.new_client_test")
        client.createSession()
        return client

    def new_client_and_user(self, group = None, perms = None, admin = False):
        user = self.new_user(group)
        client = self.new_client(group, user, perms, admin)
        return client, user

    def timeit(self, func, *args, **kwargs):
        start = time.time()
        rv = func(*args, **kwargs)
        stop = time.time()
        elapsed = stop - start
        return elapsed, rv

    def group_and_name(self, group):
        admin = self.root.sf.getAdminService()
        if isinstance(group, omero.model.ExperimenterGroup):
            if group.isLoaded():
                name = group.name.val
                group = admin.lookupGroup(name)
            else:
                group = admin.getGroup(group.id.val)
                name = group.name.val
        elif isinstance(group, (str, unicode)):
            name = group
            group = admin.lookupGroup(name)
        else:
            self.fail("Unknown type: %s=%s" % (type(group), group))

        return group, name

    def user_and_name(self, user):
        admin = self.root.sf.getAdminService()
        if isinstance(user, omero.model.Experimenter):
            if user.isLoaded():
                name = user.name.val
                user = admin.lookupExperimenter(name)
            else:
                user = admin.getExperimenter(user.id.val)
                name = user.omeName.val
        elif isinstance(user, (str, unicode)):
            name = user
            user = admin.lookupExperimenter(name)
        else:
            self.fail("Unknown type: %s=%s" % (type(user), user))

        return user, name

    def tearDown(self):
        failure = False
        self.__clients.__del__()