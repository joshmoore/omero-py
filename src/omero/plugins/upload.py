#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
   upload plugin

   Plugin read by omero.cli.Cli during initialization. The method(s)
   defined here will be added to the Cli class for later use.

   Copyright 2007-2016 Glencoe Software, Inc. All rights reserved.
   Use is subject to license terms supplied in LICENSE.txt

"""

import sys
import re
import path

from omero.cli import BaseControl, CLI
from omero.model import FileAnnotationI

import omero
import omero.rtypes
import omero.util.originalfileutils


try:
    import hashlib
    hash_sha1 = hashlib.sha1
except:
    import sha
    hash_sha1 = sha.new

HELP = """Upload local files to the OMERO server"""
RE = re.compile("\s*upload\s*")


class UploadControl(BaseControl):

    def _complete(self, text, line, begidx, endidx):
        """
        Returns a file after "upload" and otherwise delegates to the
        BaseControl
        """
        m = RE.match(line)
        if m:
            return self._complete_file(RE.sub('', line))
        else:
            return BaseControl._complete(self, text, line, begidx, endidx)

    def _configure(self, parser):
        parser.add_argument(
            "--pytable", action="store_true",
            help="If set, the following files are interpreted as pytable"
            " files")
        parser.add_argument(
            "--annotate", action="store_true",
            help="If set, create FileAnnotations from the uploaded files")
        parser.add_argument("file", nargs="+")
        parser.set_defaults(func=self.upload)
        parser.add_login_arguments()

    def upload(self, args):
        client = self.ctx.conn(args)
        fileIds = []
        annIds = []
        for file in args.file:
            if not path.path(file).exists():
                self.ctx.die(500, "File: %s does not exist" % file)
        for file in args.file:
            is_importer, omero_format = \
                omero.util.originalfileutils.getFormat(file)
            if (is_importer == omero.util.originalfileutils.IMPORTER):
                self.ctx.dir(493, "This file should be imported using omero"
                             " import")
            else:
                obj = client.upload(file, type=omero_format)
                fileIds.append(obj.id.val)
                self.ctx.set("last.upload.id", obj.id.val)

        if args.annotate:
            c = self.ctx.conn(args)
            update = c.sf.getUpdateService()
            query = c.sf.getQueryService()

            for fId in fileIds:
                # obj new FileAnnotation file=OriginalFile:275
                ann = FileAnnotationI()
                # f = file get by id
                ann.file = query.find("OriginalFile", fId)
                ann = update.saveAndReturnObject(ann)
                annIds.append(ann.id.val)
            annIds = self._order_and_range_ids(annIds)
            self.ctx.out("FileAnnotation:%s" % annIds)
        else:
            fileIds = self._order_and_range_ids(fileIds)
            self.ctx.out("OriginalFile:%s" % fileIds)

try:
    register("upload", UploadControl, HELP)
except NameError:
    if __name__ == "__main__":
        cli = CLI()
        cli.register("upload", UploadControl, HELP)
        cli.invoke(sys.argv[1:])
