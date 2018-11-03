#!/usr/bin/env python2
# Copyright 2019 Google LLC
# Written by Simon Glass <sjg@chromium.org>
#
# This program is free software; you can redistribute it and/or
# modify it under the terms of the GNU General Public License as
# published by the Free Software Foundation; either version 2 of the
# License, or (at your option) any later version.
#
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the GNU
#  General Public License for more details.
#
#  You should have received a copy of the GNU General Public License
#  along with this program; if not, write to the Free Software
#  Foundation, Inc., 59 Temple Place, Suite 330, Boston, MA  02111-1307, USA

"""Validates a device-tree file

This enforces various rules defined by the schema. Some of these
are fairly simple (the valid properties and subnodes for each node, the
allowable values for properties) and some are more complex (where phandles
are allowed to point).

The schema is defined by Python objects containing variable SchemaElement
subclasses. Each subclass defines how the device tree property is validated.
For strings this is via a regex. Phandles properties are validated by the
target they are expected to point to.

Schema elements can be optional or required. Optional elements will not cause
a failure if the node does not include them.

The presence or absense of a particular schema element can also be controlled
by a 'cond_props' option. This lists elements that must (or must not)
be present in the node for this element to be present. This provides some
flexibility where the schema for a node has two options, for example, where
the presence of one element conflicts with the presence of others.

Usage:
    The validator can be run like this (set PYTHONPATH to the directory with
    libfdt.py):

    KERNEL=/path/to/kernel
    PYTHONPATH=pylibfdt python validate_dts.py -k \
            $KERNEL/arch/arm/boot/dts/zynq-zybo.dts -d

    The output format for each input file is the name of the file followed by a
    list of validation problems. If there are no problems, the filename is not
    shown.

    Unit tests have been removed from this proof-of-concept version.


Theory of operation (in brief):
    This first compiles the .dts source, then reads it in, then validates it
    against the schema. The schema is obtained from the kernel source tree by
    scanning for .py files containing schema for particular compatible strings.
"""

from __future__ import print_function

import argparse
import copy
import itertools
import os
import re
import sys

# importlib was introduced in Python 2.7 but there was a report of it not
# working in 2.7.12, so we work around this:
# http://lists.denx.de/pipermail/u-boot/2016-October/269729.html
try:
    import importlib
    have_importlib = True
except:
    have_importlib = False

import fdt, fdt_util
from kschema import NodeAny, NodeDesc, NodeModel, NodeByPath
from kschema import PropCustom, PropDesc, PropString, PropStringList
from kschema import PropPhandleTarget, PropPhandle, CheckPhandleTarget
from kschema import PropAny, PropBool, PropFile, PropFloat, PropIntList
from kschema import SchemaElement, PropInt

def ParseArgv(argv):
    """Parse the available arguments.

    Invalid arguments or -h cause this function to print a message and exit.

    Args:
        argv: List of string arguments (excluding program name / argv[0])

    Returns:
        argparse.Namespace object containing the attributes.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('-d', '--debug', action='store_true',
                        help='Run in debug mode (full exception traceback)')
    parser.add_argument('-k', '--kernel', action='store_true',
                        help='Search kernel bindings when compiling')
    parser.add_argument('-p', '--partial', action='store_true',
            help='Validate a list of partial files (.dtsi) individually')
    parser.add_argument('-r', '--raise-on-error', action='store_true',
                        help='Causes the validator to raise on the first ' +
                        'error it finds. This is useful for debugging.')
    parser.add_argument('config', type=str, nargs='+',
                        help='Paths to the config files (.dtb) to validated')
    return parser.parse_args(argv)


class CrosConfigValidator(object):
    """Validator for the master configuration

    Properties:
        _errors: List of validation errors detected (each a string)
        _fdt: fdt.Fdt object containing device tree to validate
        _raise_on_error: True if the validator should raise on the first error
            (useful for debugging)
        _schema: Schema used for validation, a dict:
            key: Compatible string
            value: NodeDesc object containing schema for that compatible string
        _kernel: True if we are performing validation for the kernel. This
            tries to automatically add the dt-bindings search path
        _schema_by_path: Schema for each node path, used when the nodes does
            not have a compatible string, but still needs schema. Dict:
                key: node Path
                value: List of NodeDesc objects
        _settings: Global settings for validation, dict:
            key: setting (e.g. '#arch')
            value: value for that setting (e.g. 'armv8')
        _imported_elments: Partial schema imported from a file that much be
            merged into the full schema. This allows a schema file to
    """
    def __init__(self, schema, raise_on_error, kernel, settings):
        self._errors = []
        self._fdt = None
        self._raise_on_error = raise_on_error
        self._schema = schema
        self._kernel = kernel
        self._schema_by_path = {}
        self._settings = settings or {}
        self._imported_elments = []

    def Fail(self, location, msg):
        """Record a validation failure

        Args:
            location: fdt.Node object where the error occurred
            msg: Message to record for this failure
        """
        self._errors.append('%s: %s' % (location, msg))
        if self._raise_on_error:
            raise ValueError(self._errors[-1])

    def _CheckCondition(self, name, value, node_target, schema_target):
        """Check whether a required schema condition is true

        This looks at the value of a setting to see if it matches what is
        required for this schema element.

        Args:
            name: Name of setting ('#setting'), or path to the target node
                which needs to be checked ('../...')
            value: Required value for the setting
            node_target: Node that is being checked
            schema_target: NodeDesc for the schema element for this node

        Returns:
            True if the condition is met
            False if the condition is not met
        """
        if name.startswith('#'):
            if name not in self._settings:
                self.Fail(node_target.path, "Setting '%s' does not exist" %
                          name)
                return False
            actual = self._settings[name]
            if value.startswith('!'):
                if value[1:] == actual:
                    return False
            elif value != actual:
                return False
            return True

        while name.startswith('../'):
            schema_target = schema_target.parent
            node_target = node_target.parent
            name = name[3:]
        actual = node_target.props.get(name)
        if actual is not None:
            if actual.value != value:
                return False
        return True

    def ElementPresent(self, schema, parent_node):
        """Check whether a schema element should be present

        This handles the cond_props feature. The list of names of sibling
        nodes/properties that are actually present is checked to see if any of them
        conflict with the conditional properties for this node. If there is a
        conflict, then this element is considered to be absent.

        Args:
            schema: Schema element to check
            parent_node: Parent fdt.Node containing this schema element (or None if
                    this is not known)

        Returns:
            True if this element is present, False if absent
        """
        if schema.cond_props and parent_node:
            for rel_name, value in schema.cond_props.items():
                if not self._CheckCondition(rel_name, value, parent_node,
                                            schema.parent):
                    return False
        elif schema.cond_props:
            print ('if schema.cond_props')
        return True

    def GetElement(self, schema, name, node, expected=None):
        """Get an element from the schema by name

        Args:
            schema: Schema element to check
            name: Name of element to find (string)
            node: Node contaElementPresentining the property (or for nodes, the parent node
                    containing the subnode) we are looking up. None if none available
            expected: The SchemaElement object that is expected. This can be NodeDesc
                    if a node is expected, PropDesc if a property is expected, or None
                    if either is fine.

        Returns:
            Tuple:
                Schema for the node, or None if none found
                True if the node should have schema, False if it can be ignored
                        (because it is internal to the device-tree format)
        """
        for element in schema.elements:
            if not self.ElementPresent(element, node):
                continue
            if element.NameMatches(name):
                return element, True
            #elif '@' in name and element.name == name.split('@')[0]:
                #return element, True
            elif ((expected is None or expected == NodeDesc) and
                        isinstance(element, NodeAny)):
                return element, True
            elif ((expected is None or expected == PropDesc) and
                        isinstance(element, PropAny)):
                return element, True
        if expected == PropDesc:
            if name == 'linux,phandle':
                return None, False
        return None, True

    def GetElementByPath(self, path):
        """Find a schema element given its full path

        Args:
            path: Full path to look up (e.g. '/chromeos/models/MODEL/thermal/dptf-dv')

        Returns:
            SchemaElement object for that path

        Raises:
            AttributeError if not found
        """
        parts = path.split('/')[1:]
        schema = self._schema
        for part in parts:
            element, _ = self.GetElement(schema, part, None)
            schema = element
        return schema

    def _ValidateSchema(self, node, schema):
        """Simple validation of properties.

        This only handles simple mistakes like getting the name wrong. It
        cannot handle relationships between different properties.

        Args:
            node: fdt.Node where the property appears
            schema: NodeDesc containing schema for this node
        """
        schema.Validate(self, node)
        schema_props = [e.name for e in schema.elements
                                        if isinstance(e, PropDesc) and
                                        self.ElementPresent(e, node)]

        # Validate each property and check that there are no extra properties not
        # mentioned in the schema.
        for prop_name in node.props.keys():
            if prop_name == 'linux,phandle':    # Ignore this (use 'phandle' instead)
                continue
            element, _ = self.GetElement(schema, prop_name, node, PropDesc)
            if not element or not isinstance(element, PropDesc):
                if prop_name == 'phandle':
                    self.Fail(node.path, 'phandle target not valid for this node')
                else:
                    self.Fail(node.path, "Unexpected property '%s', valid list is (%s)" %
                                        (prop_name, ', '.join(schema_props)))
                continue
            element.Validate(self, node.props[prop_name])

        # Check that there are no required properties which we don't have
        for element in schema.elements:
            if (not isinstance(element, PropDesc) or
                    not self.ElementPresent(element, node)):
                continue
            if element.required and element.name not in node.props.keys():
                self.Fail(node.path, "Required property '%s' missing" % element.name)

        # Check that any required subnodes are present
        subnode_names = [n.name for n in node.subnodes.values()]
        for element in schema.elements:
            if (not isinstance(element, NodeDesc) or not element.required
                    or not self.ElementPresent(element, node)):
                continue
            if element.name not in subnode_names:
                msg = "Missing subnode '%s'" % element.name
                if subnode_names:
                    msg += ' in %s' % ', '.join(subnode_names)
                self.Fail(node.path, msg)

    def GetSchema(self, node, parent_schema):
        """Obtain the schema for a subnode

        This finds the schema for a subnode, by scanning for a matching element.

        Args:
            node: fdt.Node whose schema we are searching for
            parent_schema: Schema for the parent node, which contains that schema

        Returns:
            Schema for the node, or None if none found
        """
        schema, needed = self.GetElement(parent_schema, node.name, node.parent,
                                         NodeDesc)
        if not schema and needed:
            elements = [e.name for e in parent_schema.GetNodes()
                        if self.ElementPresent(e, node.parent)]
            self.Fail(os.path.dirname(node.path),
                                "Unexpected subnode '%s', valid list is (%s)" %
                                (node.name, ', '.join(elements)))
        return schema

    def _ImportSchemaFile(self, dirpath, module_name, priority):
        """Import a schema file from the kernel

        Args:
            dirpath: Path to directory containing the schema file
            module_name: Name of module to load (module.py)
            priority: Numbered priority to load (0 for none, 1 for highest)

        Returns:
            True if everthing went OK
            None if the module has no schema
            False if the module should have had schema but no schema was found
        """
        old_path = sys.path
        sys.path.insert(0, dirpath)
        try:
            if have_importlib:
                module = importlib.import_module(module_name)
            else:
                module = __import__(module_name)
        except ImportError as e:
            raise
            raise ValueError("Bad schema module '%s', error '%s'" %
                             (os.path.join(dirpath, module_name), e))
        finally:
            sys.path = old_path
        if getattr(module, 'no_schema', None):
            return None
        attr_name = 'schema%d' % priority if priority else 'schema'
        schema = getattr(module, attr_name, None)
        if not schema:
            return False
        for element in schema:
            bad = False
            # Most elements have a list of compatible strings for which they
            # provide the schema.
            if element.compat:
                for compat in element.compat:
                    self._schema[compat] = element

            # Some elements have no compatible string, but relate to a
            # particular path in the DT.
            elif hasattr(element, 'path'):
                self._schema_by_path[element.path] = element

            # Or perhaps we have an additional piece of schema which needs to
            # be merged with an existing element.
            elif priority:
                bad = True
                for orig in self._imported_elments:
                    if (orig.name == element.name and
                            isinstance(element, type(orig))):
                        #print("found '%s' for '%s'" % (orig.name, element.name))
                        for elem in element.elements:
                            orig.elements.append(elem)
                        bad = False
                        element = None  # Don't record this additive element
                        break

            # Or maybe there has just been some mistake
            if bad:
                self.Fail("Module '%s', var '%s', element '%s'" %
                          (module_name, attr_name, element.name),
                          'Node must have compatible string or path')
            if element:
                self._imported_elments.append(element)

        return True

    def _GetSchemaFiles(self, schema_path):
        """Find all schema files in a given path

        Args:
            schema_path: Path to schema, e.g. 'Documentation/devicetree/binding'

        Returns:
            List of schema files, each:
                List containing:
                    Directory path
                    Base name of module (with the '.py')
        """
        file_list = []
        for (dirpath, dirnames, fnames) in os.walk(schema_path):
            for fname in fnames:
                base, ext = os.path.splitext(fname)
                if ext == '.py' and not base.startswith('_'):
                    #print("Importing '%s/%s'" % (dirpath, fname))
                    file_list.append([dirpath, base])
        return file_list

    def _LoadSchema(self, schema_path):
        """Locate and load all the schema files

        This looks in the given path for .py schema files and loads them.

        Args:
            schema_path: Root path to look for Python files
        """
        remaining_list = self._GetSchemaFiles(schema_path)
        priority = 0
        while remaining_list:
            leftover = []
            for dirpath, base in remaining_list:
                loaded = self._ImportSchemaFile(dirpath, base, priority)
                if loaded == False:
                    leftover.append([dirpath, base])
            remaining_list = leftover
            priority += 1
            if priority > 9:
                self.Fail(schema_path,
                          'Cannot locate schema in files: %s' %
                          ', '.join([fname for fname, name in remaining_list]))
                break

    def _ValidateTree(self, node, parent_schema):
        """Validate a node and all its subnodes recursively

        Args:
            node: name of fdt.Node to search for
            parent_schema: Schema for the parent node
        """
        schema = None
        base_path = node.path.split('@')[0]

        # Normal case: compatible string specifies the schema
        compats = []
        if 'compatible' in node.props:
            compats = fdt_util.GetCompatibleList(node)
            for compat in compats:
                if compat in self._schema:
                    schema = self._schema[compat]

        # Schema for some nodes is specified by their path (e.g. /cpu)
        elif base_path in self._schema_by_path:
            schema = self._schema_by_path[base_path]
            #print(self._schema_by_path)

        # Schema may be in a child element of this schema
        elif isinstance(parent_schema, SchemaElement):
            schema = self.GetSchema(node, parent_schema)
            if isinstance(schema, NodeByPath):
                self.Fail(node.path, 'No schema found for this path %s' % schema)
                return

        if schema is None:
            print('No schema for: %s' % (', '.join(compats)))
            #return

        if schema:
            self._ValidateSchema(node, schema)
            for subnode in node.subnodes.values():
                self._ValidateTree(subnode, schema or parent_schema)

    # This is not actually used - it's just an example of the more complex
    # validation possible with this validator. Here we check for duplicate
    # values as well as a range, plus we look to make sure the phandle target
    # points to a suitable node.
    @staticmethod
    def ValidateSkuMap(val, prop):
        it = iter(prop.value)
        sku_set = set()
        for sku, phandle in itertools.izip(it, it):
            sku_id = fdt_util.fdt32_to_cpu(sku)
            # Allow a SKU ID of -1 as a valid match.
            if sku_id > 0xffff and sku_id != 0xffffffff:
                val.Fail(prop.node.path, 'sku_id %d out of range' % sku_id)
            if sku_id in sku_set:
                val.Fail(prop.node.path, 'Duplicate sku_id %d' % sku_id)
            sku_set.add(sku_id)
            phandle_val = fdt_util.fdt32_to_cpu(phandle)
            target = prop.fdt.LookupPhandle(phandle_val)
            if (not CheckPhandleTarget(val, target, '/chromeos/models/MODEL') and
                not CheckPhandleTarget(val, target,
                               '/chromeos/models/MODEL/submodels/SUBMODEL')):
                val.Fail(prop.node.path,
                     "Phandle '%s' sku-id %d must target a model or submodel'" %
                     (prop.name, sku_id))

    def Prepare(self, _fdt):
        """Get ready to valid a DT file"""
        self._fdt = _fdt

    def Start(self, fnames, partial=False):
        """Start validating a DT file

        Args:
            fnames: List of filenames containing the configuration to validate.
                    Supports compiled .dtb files and source .dts files, If
                    partial is False then there can be only one filename in the
                    list.
            partial: True to process a list of partial config files (.dtsi)
        """
        tmpfile = None
        self._errors = []
        try:
            if partial:
                dtb, tmpfile = fdt_util.CompileAll(fnames)
            else:
                search_paths = [os.path.join(os.getcwd(), 'include')]
                if self._kernel:
                    # Add kernel bindings dir if found
                    pathname = os.path.dirname(fnames[0])
                    dirs = []
                    for items in range(4):
                        pathname, dirname = os.path.split(pathname)
                        dirs.insert(0, dirname)
                    if dirs[-2:] == ['boot', 'dts']:
                        search_paths.append(os.path.join(pathname, 'include'))

                dtb, tmpfile = fdt_util.EnsureCompiled(fnames[0], search_paths)
                schema_path = os.path.join(pathname, 'Documentation',
                        'devicetree', 'bindings')
                self._LoadSchema(schema_path)
            self.Prepare(fdt.FdtScan(dtb))

            self._ValidateTree(self._fdt.GetRoot(), self._schema)
        finally:
            if tmpfile:
                os.unlink(tmpfile.name)
        return self._errors


"""This is the schema. It is a hierarchical set of nodes and properties, just
like the device tree. If an object subclasses NodeDesc then it is a node,
possibly with properties and subnodes.

In this way it is possible to describe the schema in a fairly natural,
hierarchical way.

# Note: The schema starts off empty and is read from .py files in the kernel.
This seems like a better approach than trying to have the schema all in one
file.
"""
SCHEMA = {}


def ShowErrors(fname, errors):
    """Show validation errors

    Args:
        fname: Filename containng the errors
        errors: List of errors, each a string
    """
    print('%s:' % fname, file=sys.stderr)
    for error in errors:
        print(error, file=sys.stderr)
    print(file=sys.stderr)


def Main(argv=None):
    """Main program for validator

    This validates each of the provided files and prints the errors for each, if
    any.

    Args:
        argv: Arguments to the problem (excluding argv[0]); if None, uses
                 sys.argv
    """
    if argv is None:
        argv = sys.argv[1:]
    args = ParseArgv(argv)
    settings = {'#arch': 'armv7'}
    validator = CrosConfigValidator(SCHEMA, args.raise_on_error, args.kernel,
                                    settings)
    found_errors = False
    try:
        # If we are given partial files (.dtsi) then we compile them all into one
        # .dtb and validate that.
        if args.partial:
            errors = validator.Start(args.config, partial=True)
            fname = args.config[0]
            if errors:
                ShowErrors(fname, errors)
                found_errors = True

        # Otherwise process each file individually
        else:
            for fname in args.config:
                errors = validator.Start([fname])
                if errors:
                    found_errors = True
                    if errors:
                        ShowErrors(fname, errors)
                        found_errors = True
    except ValueError as e:
        if args.debug:
            raise
        print('Failed: %s' % e, file=sys.stderr)
        found_errors = True
    if found_errors:
        sys.exit(1)


if __name__ == "__main__":
    Main()
