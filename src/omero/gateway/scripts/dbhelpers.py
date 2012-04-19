import sys
sys.path.append('.')

import omero.gateway
import omero.model
from omero.rtypes import *
import os
import subprocess
import re
import time
import urllib2

from types import StringTypes
from path import path

BASEPATH = os.path.dirname(os.path.abspath(__file__))
TESTIMG_URL = 'http://users.openmicroscopy.org.uk/~cneves-x/'

if not omero.gateway.BlitzGateway.ICE_CONFIG:
    try:
        import settings
        omero.gateway.BlitzGateway.ICE_CONFIG = os.path.join(settings.OMERO_HOME, 'etc', 'ice.config')
    except ImportError:
        pass
    except AttributeError:
        pass

#Gateway = omero.gateway.BlitzGateway

def refreshConfig ():
    bg = omero.gateway.BlitzGateway()
    try:
        ru = bg.c.ic.getProperties().getProperty('omero.rootuser')
        rp = bg.c.ic.getProperties().getProperty('omero.rootpass')
    finally:
        bg.seppuku()

    if ru:
        ROOT.name = ru
    if rp:
        ROOT.passwd = rp

def loginAsRoot ():
    refreshConfig()
    return login(ROOT)

def loginAsPublic ():
    return login(settings.PUBLIC_USER, settings.PUBLIC_PASSWORD)

def login (alias, pw=None, groupname=None):
    if isinstance(alias, UserEntry):
        return alias.login(groupname=groupname)
    elif pw is None:
        return USERS[alias].login(groupname=groupname)
    else:
        return UserEntry(alias, pw).login(groupname=groupname)

#def addGroupToUser (client, groupname):
#    a = client.getAdminService()
#    if not 'system' in [x.name.val for x in a.containedGroups(client._userid)]:
#        admin = loginAsRoot()
#        a = admin.getAdminService()
#    else:
#        admin = client
#    try:
#        g = a.lookupGroup(groupname)
#    except:
#        g = omero.model.ExperimenterGroupI()
#        g.setName(omero.gateway.omero_type(groupname))
#        a.createGroup(g)
#        g = a.lookupGroup(groupname)
#    a.addGroups(a.getExperimenter(client._userid), (g,))

#def setGroupForSession (client, groupname):
#    ssuid = client._sessionUuid
#    ss = client.getSessionService()
#    sess = ss.getSession(ssuid)
#    if sess.getDetails().getGroup().getName().val == groupname:
#        # Already correct
#        return
#    a = client.getAdminService()
#    if not groupname in [x.name.val for x in a.containedGroups(client._userid)]:
#        UserEntry.addGroupToUser(client, groupname)
#    g = a.lookupGroup(groupname)
#    sess.getDetails().setGroup(g)
#    ss.updateSession(sess)


class BadGroupPermissionsException(Exception):
    pass


class UserEntry (object):
    def __init__ (self, name, passwd, firstname='', middlename='', lastname='', email='',
                  groupname=None, groupperms=None, groupowner=False, admin=False):
        """
        If no groupperms are passed, then check_group_perms will do nothing.
        The default perms for newly created groups is defined in _getOrCreateGroup
        """
        self.name = name
        self.passwd = passwd
        self.firstname = firstname
        self.middlename = middlename
        self.lastname = lastname
        self.email = email
        self.admin = admin
        self.groupname = groupname
        self.groupperms = groupperms
        self.groupowner = groupowner

    def fullname (self):
        return '%s %s' % (self.firstname, self.lastname)

    def login (self, groupname=None):
        if groupname == None:
            groupname = self.groupname
        client = omero.gateway.BlitzGateway(self.name, self.passwd, group=groupname, try_super=self.admin)
        if not client.connect():
            print "Can not connect" 
            return None

        a = client.getAdminService()
        if groupname is not None:
            if client.getEventContext().groupName != groupname:
                try:
                    g = a.lookupGroup(groupname)
                    client.setGroupForSession(g.getId().val)
                except:
                    pass

        # Reset group name and evaluate
        self.groupname = a.getEventContext().groupName
        if self.groupname != "system":
            UserEntry.check_group_perms(client, self.groupname, self.groupperms)

        return client

    @staticmethod
    def check_group_perms(client, groupname, groupperms):
        """
        If expected permissions have been set, then this will
        enforce equality. If groupperms are None, then
        nothing will be checked.
        """
        if groupperms is not None:
            a = client.getAdminService()
            g = a.lookupGroup(groupname)
            p = g.getDetails().getPermissions()
            if str(p) != groupperms:
                raise BadGroupPermissionsException( \
                        "%s group has wrong permissions! Expected: %s Found: %s" % \
                        (groupname, groupperms, p))

    @staticmethod
    def _getOrCreateGroup (client, groupname, groupperms=None):

        # Default on class is None
        if groupperms is None:
            groupperms = "rwr---"

        a = client.getAdminService()
        try:
            g = a.lookupGroup(groupname)
        except:
            g = omero.model.ExperimenterGroupI()
            g.setName(omero.gateway.omero_type(groupname))
            p = omero.model.PermissionsI(groupperms)
            g.details.setPermissions(p)
            a.createGroup(g)
            g = a.lookupGroup(groupname)
        UserEntry.check_group_perms(client, groupname, groupperms)
        return g

    def create (self, client, password):
        a = client.getAdminService()
        try:
            a.lookupExperimenter(self.name)
            #print "Already exists: %s" % self.name
            return False
        except:
            #print "Creating: %s" % self.name
            pass
        if self.groupname is None:
            self.groupname = self.name + '_group'
        g = UserEntry._getOrCreateGroup(client, self.groupname, self.groupperms)
        u = omero.model.ExperimenterI()
        u.setOmeName(omero.gateway.omero_type(self.name))
        u.setFirstName(omero.gateway.omero_type(self.firstname))
        u.setMiddleName(omero.gateway.omero_type(self.middlename))
        u.setLastName(omero.gateway.omero_type(self.lastname))
        u.setEmail(omero.gateway.omero_type(self.email))
        a.createUser(u, g.getName().val)
        u = a.lookupExperimenter(self.name)
        if self.admin:
            a.addGroups(u,(a.lookupGroup("system"),))
        client.c.sf.setSecurityPassword(password) # See #3202
        a.changeUserPassword(u.getOmeName().val, omero.gateway.omero_type(self.passwd))
        if self.groupowner:
            a.setGroupOwner(g, u)
        return True

    def changePassword (self, client, password, rootpass):
        a = client.getAdminService()
        client.c.sf.setSecurityPassword(rootpass) # See #3202
        a.changeUserPassword(self.name, omero.gateway.omero_type(password))

    @staticmethod
    def addGroupToUser (client, groupname, groupperms='rw----'):
        a = client.getAdminService()
        admin_gateway = None
        try:
            if not 'system' in [x.name.val for x in a.containedGroups(client._userid)]:
                admin_gateway = loginAsRoot()
                a = admin_gateway.getAdminService()
            else:
                admin = client
            g = UserEntry._getOrCreateGroup(client, groupname, groupperms)
            a.addGroups(a.getExperimenter(client._userid), (g,))
        finally:
            # Always clean up the results of login
            if admin_gateway:
                admin_gateway.seppuku()

    @staticmethod
    def setGroupForSession (client, groupname):
        a = client.getAdminService()
        if not groupname in [x.name.val for x in a.containedGroups(client._userid)]:
            UserEntry.addGroupToUser(client, groupname)
        g = a.lookupGroup(groupname)
        client.setGroupForSession(g.getId().val)


class ObjectEntry (object):
    pass

class ProjectEntry (ObjectEntry):
    def __init__ (self, name, owner, create_group=False, group_perms=False):
        self.name = name
        self.owner = owner
        self.create_group = create_group
        self.group_perms = group_perms

    def get (self, client=None, fromCreate=False):
        if client is None:
            client = USERS[self.owner].login()
        for p in client.listProjects():
            if p.getName() == self.name:
                p.__loadedHotSwap__()
                return p
        return None

    def create (self, client=None):
        if client is None:
            client = USERS[self.owner].login()
        p = self.get(client)
        if p is not None:
            return p
        p = omero.model.ProjectI(loaded=True)
        p.setName(omero.gateway.omero_type(self.name))
        p.setDescription(omero.gateway.omero_type(self.name))
        if self.create_group:
            if isinstance(self.create_group, StringTypes):
                groupname = self.create_group
            else:
                raise ValueError('group must be string')
                groupname = 'project_test'

            s = loginAsRoot()
            try:
                UserEntry.addGroupToUser (s, groupname)
            finally:
                s.seppuku()

            UserEntry.setGroupForSession(client, groupname)
        p = omero.gateway.ProjectWrapper(client, client.getUpdateService().saveAndReturnObject(p))
        return self.get(client, True)

class DatasetEntry (ObjectEntry):
    def __init__ (self, name, project, description=None, callback=None):
        self.name = name
        self.project = project
        self.description = description
        self.callback = callback

    def get (self, client, forceproj=None):
        if forceproj is None:
            if isinstance(self.project, StringTypes):
                project = PROJECTS[self.project].get(client)
            elif isinstance(self.project, ProjectEntry):
                project = self.project.get(client)
            else:
                project = self.project
        else:
            project = forceproj
        for d in project.listChildren():
            if d.getName() == self.name and ((self.description is None and d.getDescription() == '') or (self.description is not None and omero.gateway.omero_type(d.getDescription()) == omero.gateway.omero_type(self.description))):
                d.__loadedHotSwap__()
                return d
        return None

    def create (self):
        if isinstance(self.project, StringTypes):
            project = PROJECTS[self.project]
            user = USERS[project.owner]
            client = user.login()
            project = project.get(client)
        else:
            project = self.project
            client = project._conn
        d = self.get(client, project)
        if d is not None and ((self.description is None and d.getDescription() == '') or (self.description is not None and omero.gateway.omero_type(d.getDescription()) == omero.gateway.omero_type(self.description))):
            return d
        d = omero.model.DatasetI(loaded=True)
        d.setName(omero.gateway.omero_type(self.name))
        if self.description is not None:
            d.setDescription(omero.gateway.omero_type(self.description))
        project.linkDataset(d)
        project.save()
        rv = self.get(client, project)
        if self.callback:
            self.callback(rv)
        return rv

class ImageEntry (ObjectEntry):
    def __init__ (self, name, filename, dataset, callback=None):
        self.name = name
        self.filename = filename # If False will create image without pixels
        if self.name is None and filename:
            self.name = os.path.basename(filename)
        self.dataset = dataset
        self.callback = callback

    def get (self, client, forceds=None):
        if forceds is None:
            dataset = DATASETS[self.dataset].get(client)
        else:
            dataset = forceds
        for i in dataset.listChildren():
            if i.getName() == self.name:
                return i
        return None

    def create (self):
        if isinstance(self.dataset, StringTypes):
            dataset = DATASETS[self.dataset]
            project = PROJECTS[dataset.project]
            client = USERS[project.owner].login()
            dataset = dataset.get(client)
        else:
            dataset = self.dataset
            client = dataset._conn
        i = self.get(client, dataset)
        if i is not None:
            #print ".. -> image already exists: %s" % self.name
            return i
        #print ".. -> create new image: %s" % self.name
        sys.stderr.write('I')
        if self.filename is False:
            UserEntry.setGroupForSession(client, dataset.getDetails().getGroup().getName())
            self._createWithoutPixels(client, dataset)
            return self.get(client, dataset)
        fpath = os.path.join(BASEPATH, self.filename)
        if not os.path.exists(fpath):
            if not os.path.exists(os.path.dirname(fpath)):
                os.makedirs(os.path.dirname(fpath))
            # First try to download the image
            try:
                #print "Trying to get test image from " + TESTIMG_URL + self.filename
                sys.stderr.write('<')
                f = urllib2.urlopen(TESTIMG_URL + self.filename)
                open(fpath, 'wb').write(f.read())
            except urllib2.HTTPError:
                raise IOError('No such file %s' % fpath)
        host = dataset._conn.c.ic.getProperties().getProperty('omero.host') or 'localhost'
        port = dataset._conn.c.ic.getProperties().getProperty('omero.port') or '4063'

        exe = path(".") / ".." / "bin" /"omero" # Running from dist
        if not exe.exists():
            exe = path(".") / ".." / ".."/ ".." / "dist" / "bin" / "omero" # Running from OmeroPy
            if not exe.exists():
                print "\n\nNo omero found! Add OMERO_HOME/bin to your PATH variable (See #5176)\n\n"
                exe = "omero"

        newconn = dataset._conn.clone()
        newconn.connect()
        try:
            UserEntry.setGroupForSession(newconn, dataset.getDetails().getGroup().getName())
            session = newconn._sessionUuid
            #print session
            exe += ' -s %s -k %s -p %s import -d %i -n' % (host, session, port, dataset.getId())
            exe = exe.split() + [self.name, fpath]
            print ' '.join(exe)
            try:
                p = subprocess.Popen(exe,  shell=False, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            except OSError:
                print "!!Please make sure the 'omero' executable is in PATH"
                return None
            #print ' '.join(exe)
            pid = p.communicate()#[0].strip() #re.search('Saving pixels id: (\d*)', p.communicate()[0]).group(1)
            #print pid
            try:
                img = omero.gateway.ImageWrapper(dataset._conn, dataset._conn.getQueryService().find('Pixels', long(pid[0].split('\n')[0].strip())).image)
            except ValueError:
                print pid
                raise
            #print "imgid = %i" % img.getId()
            img.setName(self.name)
            #img._obj.objectiveSettings = None
            img.save()
            if self.callback:
                self.callback(img)
            return img
        finally:
            newconn.seppuku() # Always cleanup the return from clone/connect

    def _createWithoutPixels (self, client, dataset):
        img = omero.model.ImageI()
        img.setName(omero.gateway.omero_type(self.name))
        img.setAcquisitionDate(rtime(0))
        if not dataset.imageLinksLoaded:
            print ".!."
            dataset._obj._imageLinksSeq = []
            dataset._obj._imageLinksLoaded = True;
        dataset.linkImage(img)
        dataset.save()

def getProject (client, alias):
    return PROJECTS[alias].get(client)

def assertCommentAnnotation (object, ns, value):
    ann = object.getAnnotation(ns)
    if ann is None or ann.getValue() != value:
        ann = CommentAnnotationWrapper()
        ann.setNs(ns)
        ann.setValue(value)
        object.linkAnnotation(ann)
    return ann

def getDataset (client, alias, forceproj=None):
    return DATASETS[alias].get(client, forceproj)

def getImage (client, alias, forceds=None):
    return IMAGES[alias].get(client, forceds)

def bootstrap ():
    # Create users
    client = loginAsRoot()
    for k, u in USERS.items():
        if not u.create(client, ROOT.passwd):
            u.changePassword(client, u.passwd, ROOT.passwd)
    for k, p in PROJECTS.items():
        p = p.create()
        p._conn.seppuku()
        #print p.get(client).getDetails().getPermissions().isUserWrite()
    for k, d in DATASETS.items():
        d = d.create()
        d._conn.seppuku()
    for k, i in IMAGES.items():
        i = i.create()
        i._conn.seppuku()
    client.seppuku()

def cleanup ():
    clients = []
    try:
        for k, p in PROJECTS.items():
            sys.stderr.write('*')
            p = p.get()
            if p is not None:
                client = p._conn
                clients.append(client)
                update = client.getUpdateService()
                delete = client.getDeleteService()
                for d in p.listChildren():
                    delete.deleteImagesByDataset(d.getId(), True)
                    update.deleteObject(d._obj)
                nss = list(set([x.ns for x in p.listAnnotations()]))
                for ns in nss:
                    p.removeAnnotations(ns)
                #print ".. -> removing project %s" % p.getName()
                update.deleteObject(p._obj)
        # What about users?
        admin = client.getAdminService()
        for k, u in USERS.items():
            u.changePassword(client, None)
    finally:
        for client in clients:
            client.seppuku()

ROOT=UserEntry('root','ome',admin=True)

USERS = {
    #'alias': UserEntry entry,
    }

PROJECTS = {
    #'alias': ProjectEntry entry,
    }

DATASETS = {
    #'alias': DatasetEntry entry,
    }

IMAGES = {
    #'alias': ImageEntry entry,
}

