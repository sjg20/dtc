# Copyright 2017 The Chromium OS Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

"""Schema elements.

This module provides schema elements that can be used to build up a schema for
validation of the master configuration
"""

from __future__ import print_function

import re

import fdt_util


def CheckPhandleTarget(val, target, target_compat):
    """Check that the target of a phandle matches a pattern

    Args:
        val: Validator (used for model list, etc.)
        target: Target node (Node object)
        target_compat: Match string. This is the compatible string that the
            target must point to.

    Returns:
        True if the target matches, False if not
    """
    compats = fdt_util.GetCompatibleList(target)
    if not compats:
        return False
    return target_compat in compats


class SchemaElement(object):
    """A schema element, either a property or a subnode

    Args:
        name: Name of schema eleent
        prop_type: String describing this property type
        required: True if this element is mandatory, False if optional
        cond_props: Properties which control whether this element is present.
             Dict:
                 key: name of controlling property
                 value: True if the property must be present, False if it must be absent
    """
    def __init__(self, name, prop_type, required=False, cond_props=None):
        self.name = name
        self.prop_type = prop_type
        self.required = required
        self.cond_props = cond_props
        self.parent = None

    def Validate(self, val, prop):
        """Validate the schema element against the given property.

        This method is overridden by subclasses. It should call val.Fail() if there
        is a problem during validation.

        Args:
            val: CrosConfigValidator object
            prop: Prop object of the property
        """
        pass


class PropDesc(SchemaElement):
    """A generic property schema element (base class for properties)"""
    def __init__(self, name, prop_type, required=False, cond_props=None):
        super(PropDesc, self).__init__(name, prop_type, required, cond_props)


class PropString(PropDesc):
    """A string-property

    Args:
        str_pattern: Regex to use to validate the string
    """
    def __init__(self, name, required=False, str_pattern='',
                             cond_props=None):
        super(PropString, self).__init__(name, 'string', required,
                                         cond_props)
        self.str_pattern = str_pattern

    def Validate(self, val, prop):
        """Check the string with a regex"""
        if not self.str_pattern:
            return
        pattern = '^' + self.str_pattern + '$'
        m = re.match(pattern, prop.value)
        if not m:
            val.Fail(prop.node.path, "'%s' value '%s' does not match pattern '%s'" %
                             (prop.name, prop.value, pattern))


class PropInt(PropDesc):
    """An integer property"""
    def __init__(self, name, required=False, int_range=None,
                             cond_props=None):
        super(PropInt, self).__init__(name, 'int', required, cond_props)
        self.int_range = int_range

    def Validate(self, val, prop):
        """Check that the value is an int"""
        try:
            int_val = fdt_util.fdt32_to_cpu(prop.value)
            if self.int_range is not None:
                # pylint: disable=unpacking-non-sequence
                min_val, max_val = self.int_range
                if int_val < min_val or int_val > max_val:
                    val.Fail(prop.node.path, "'%s' value '%s' is out of range [%g..%g]" %
                                     (prop.name, int_val, min_val, max_val))

        except ValueError:
            val.Fail(prop.node.path, "'%s' value '%s' is not an int" %
                             (prop.name, prop.value))


class PropIntList(PropDesc):
    """An int-list property schema element

    Note that the list may be empty in which case no validation is performed.

    Args:
        int_range: List: min and max value
    """
    def __init__(self, name, required=False, int_range=None,
                             cond_props=None):
        super(PropIntList, self).__init__(name, 'intlist', required,
                                                                            cond_props)
        self.int_range = int_range

    def Validate(self, val, prop):
        """Check each item of the list with a range"""
        if not self.int_range:
            return
        for int_val in prop.value:
            try:
                if self.int_range is not None:
                    # pylint: disable=unpacking-non-sequence
                    min_val, max_val = self.int_range
                    int_val = fdt_util.fdt32_to_cpu(int_val)
                    if int_val < min_val or int_val > max_val:
                        val.Fail(prop.node.path, "'%s' value '%s' is out of range [%g..%g]" %
                                        (prop.name, prop.value, min_val, max_val))

            except ValueError:
                val.Fail(prop.node.path, "'%s' value '%s' is not a float" %
                                (prop.name, prop.value))

class PropFloat(PropDesc):
    """A floating-point property"""
    def __init__(self, name, required=False, float_range=None,
                             cond_props=None):
        super(PropFloat, self).__init__(name, 'float', required, cond_props)
        self.float_range = float_range

    def Validate(self, val, prop):
        """Check that the value is a float"""
        try:
            float_val = float(prop.value)
            if self.float_range is not None:
                # pylint: disable=unpacking-non-sequence
                min_val, max_val = self.float_range
                if float_val < min_val or float_val > max_val:
                    val.Fail(prop.node.path, "'%s' value '%s' is out of range [%g..%g]" %
                                     (prop.name, prop.value, min_val, max_val))

        except ValueError:
            val.Fail(prop.node.path, "'%s' value '%s' is not a float" %
                             (prop.name, prop.value))


class PropBool(PropDesc):
    """A boolean property"""
    def __init__(self, name, cond_props=None):
        super(PropBool, self).__init__(name, 'bool', False, cond_props)


class PropFile(PropDesc):
    """A file property

    This represents a file to be installed on the filesystem.

    Properties:
        target_dir: Target directory in the filesystem for files from this
                property (e.g. '/etc/cras'). This is used to set the install directory
                and keep it consistent across ebuilds (which use cros_config_host) and
                init scripts (which use cros_config). The actual file written will be
                relative to this.
    """
    def __init__(self, name, required=False, str_pattern='',
                             cond_props=None, target_dir=None):
        super(PropFile, self).__init__(name, 'file', required, cond_props)
        self.str_pattern = str_pattern
        self.target_dir = target_dir

    def Validate(self, val, prop):
        """Check the filename with a regex"""
        if not self.str_pattern:
            return
        pattern = '^' + self.str_pattern + '$'
        m = re.match(pattern, prop.value)
        if not m:
            val.Fail(prop.node.path, "'%s' value '%s' does not match pattern '%s'" %
                             (prop.name, prop.value, pattern))


class PropStringList(PropDesc):
    """A string-list property schema element

    Note that the list may be empty in which case no validation is performed.

    Args:
        str_pattern: Regex to use to validate the string
    """
    def __init__(self, name, required=False, str_pattern='',
                             cond_props=None):
        super(PropStringList, self).__init__(name, 'stringlist', required,
                                                                                 cond_props)
        self.str_pattern = str_pattern

    def Validate(self, val, prop):
        """Check each item of the list with a regex"""
        if not self.str_pattern:
            return
        pattern = '^' + self.str_pattern + '$'
        for item in prop.value:
            m = re.match(pattern, item)
            if not m:
                val.Fail(prop.node.path, "'%s' value '%s' does not match pattern '%s'" %
                                 (prop.name, item, pattern))


class PropPhandleTarget(PropDesc):
    """A phandle-target property schema element

    A phandle target can be pointed to by another node using a phandle property.
    """
    def __init__(self, required=False, cond_props=None):
        super(PropPhandleTarget, self).__init__('phandle', 'phandle-target',
                                                required, cond_props)


class PropPhandle(PropDesc):
    """A phandle property schema element

    Phandle properties point to other nodes, and allow linking from one node to
    another.

    Properties:
        target_compat: String to use to validate the target of this phandle.
                It is the compatible string that it must point to. See
                CheckPhandleTarget for details.
    """
    def __init__(self, name, target_compat, required=False, cond_props=None):
        super(PropPhandle, self).__init__(name, 'phandle', required, cond_props)
        self.target_compat = target_compat

    def Validate(self, val, prop):
        """Check that this phandle points to the correct place"""
        phandle = prop.GetPhandle()
        target = prop.fdt.LookupPhandle(phandle)
        if not CheckPhandleTarget(val, target, self.target_compat):
            val.Fail(prop.node.path, "Phandle '%s' targets node '%s' which "
                     "does not have compatible string  '%s'" %
                     (prop.name, target.path, self.target_compat))


class PropCustom(PropDesc):
    """A custom property with its own validator

    Properties:
        validator: Function to call to validate this property
    """
    def __init__(self, name, validator, required=False, cond_props=None):
        super(PropCustom, self).__init__(name, 'custom', required,
                                                                         cond_props)
        self.validator = validator

    def Validate(self, val, prop):
        """Validator for this property

        This should be a static method in CrosConfigValidator.

        Args:
            val: CrosConfigValidator object
            prop: Prop object of the property
        """
        self.validator(val, prop)


class PropAny(PropDesc):
    """A placeholder for any property name

    Properties:
        validator: Function to call to validate this property
    """
    def __init__(self, validator=None):
        super(PropAny, self).__init__('ANY', 'any')
        self.validator = validator

    def Validate(self, val, prop):
        """Validator for this property

        This should be a static method in CrosConfigValidator.

        Args:
            val: CrosConfigValidator object
            prop: Prop object of the property
        """
        if self.validator:
            self.validator(val, prop)


class NodeDesc(SchemaElement):
    """A generic node schema element (base class for nodes)"""
    def __init__(self, name, compat, required=False, elements=None,
                             cond_props=None):
        super(NodeDesc, self).__init__(name, 'node', required,
                                       cond_props)
        self.compat = compat
        self.elements = [] if elements is None else elements
        if compat:
            self.elements.append(PropStringList('compatible', True,
                                                '|'.join(compat)))
        self.elements.append(PropInt('#address-cells'))
        self.elements.append(PropInt('#size-cells'))
        self.elements.append(PropInt('interrupt-parent'))
        for element in self.elements:
            element.parent = self

    def GetNodes(self):
        """Get a list of schema elements which are nodes

        Returns:
            List of objects, each of which has NodeDesc as a base class
        """
        return [n for n in self.elements if isinstance(n, NodeDesc)]


class NodeModel(NodeDesc):
    """A model (top-level node in DT)"""
    def __init__(self, name, compat, elements=None):
        super(NodeModel, self).__init__('MODEL', compat, elements=elements)
        self.name = name
        self.elements.append(PropString('model', True, name))
        self.elements.append(NodeAliases())
        self.elements.append(NodeCpus())


class NodeAliases(NodeDesc):
    """An /aliases node, containing references to other nodes"""
    def __init__(self):
        super(NodeAliases, self).__init__('ALIAS', None)
        self.name = 'aliases'
        self.elements.append(PropAny())


class NodeByPath(NodeDesc):
    """A nde which is specified by path rather than compatible string"""
    def __init__(self, path, elements):
        super(NodeByPath, self).__init__('PATH-%s' % path, None,
                                         elements=elements)
        self.path = path


class NodeCpus(NodeByPath):
    """A /cpus node, containing information about CPUs"""
    def __init__(self, elements=None):
        super(NodeCpus, self).__init__('/cpus', elements)
        self.name = 'cpus'


class NodeAny(NodeDesc):
    """A generic node schema element (base class for nodes)"""
    def __init__(self, name_pattern, elements):
        super(NodeAny, self).__init__('ANY', elements=elements)
        self.name_pattern = name_pattern

    def Validate(self, val, node):
        """Check the name with a regex"""
        if not self.name_pattern:
            return
        pattern = '^' + self.name_pattern + '$'
        m = re.match(pattern, node.name)
        if not m:
            val.Fail(node.path, "Node name '%s' does not match pattern '%s'" %
                             (node.name, pattern))
