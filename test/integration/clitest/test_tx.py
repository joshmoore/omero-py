#!/usr/bin/env python
# -*- coding: utf-8 -*-

#
# Copyright (C) 2014 University of Dundee & Open Microscopy Environment.
# All rights reserved.
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 2 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License along
# with this program; if not, write to the Free Software Foundation, Inc.,
# 51 Franklin Street, Fifth Floor, Boston, MA 02110-1301 USA.


import pytest

from test.integration.clitest.cli import CLITest
from omero.plugins.tx import TxControl
from omero.util.temp_files import create_path
from omero.cli import NonZeroReturnCode


class TestTx(CLITest):

    def setup_method(self, method):
        super(TestTx, self).setup_method(method)
        self.cli.register("tx", TxControl, "TEST")
        self.args += ["tx"]
        self.setup_mock()

    def teardown_method(self, method):
        self.teardown_mock()
        super(TestTx, self).teardown_method(method)

    def go(self):
        self.cli.invoke(self.args, strict=True)
        return self.cli.get("tx.out")

    def create_script(self):
        path = create_path()
        for x in ("Screen", "Plate", "Project", "Dataset"):
            path.write_text("new %s name=test\n" % x, append=True)
            path.write_text("new %s name=test description=foo\n" % x,
                            append=True)
        return path

    def test_create_from_file(self):
        path = self.create_script()
        self.args.append("--file=%s" % path)
        self.cli.invoke(self.args, strict=True)
        rv = self.cli.get("tx.out")
        assert 8 == len(rv)
        path.remove()

    def test_create_from_args(self):
        self.args.append("new")
        self.args.append("Dataset")
        self.args.append("name=foo")
        rv = self.go()
        assert 1 == len(rv)
        assert rv[0].startswith("Dataset")

    def test_linkage(self):
        path = create_path()
        path.write_text(
            """
            new Project name=foo
            new Dataset name=bar
            new ProjectDatasetLink parent@=0 child@=1
            """)
        self.args.append("--file=%s" % path)
        rv = self.go()
        assert 3 == len(rv)
        assert rv[0].startswith("Project")
        assert rv[1].startswith("Dataset")
        assert rv[2].startswith("ProjectDatasetLink")
        path.remove()

    @pytest.mark.parametrize(
        "input", (
            ("new", "Image"),
            ("new", "Image", "name=foo"),
            ("new", "ProjectDatasetLink", "parent=Project:1"),
        ))
    def test_required(self, input):
        self.args.extend(list(input))
        with pytest.raises(NonZeroReturnCode):
            self.go()
