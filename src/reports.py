#
# Copyright (C) 2012  Red Hat, Inc.
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU Lesser General Public License as published
# by the Free Software Foundation; either version 2.1 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU Lesser General Public License for more details.
#
# You should have received a copy of the GNU Lesser General Public License
# along with this program.  If not, see <http://www.gnu.org/licenses/>.
#
# Author: Fabian Deutsch <fabiand@fedoraproject.org>
#
# -*- coding: utf-8 -*-

from lxml import etree
import simplejson as json

import utils

def statusjson_to_report(txt):
    return transform_status_json("data/tools/report.rst.xsl", txt)

def transform_status_json(stylefile, txt):
    d = json.loads(txt)
    xml = utils.obj2xml("status", d)
    transform = etree.XSLT(etree.parse(stylefile))
    report = transform(xml)
    return report
