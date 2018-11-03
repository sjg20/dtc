# Copyright 2019 Google LLC
# Written by Simon Glass <sjg@chromium.org>

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

"""Convert textual binding file to Python (poorly).

This provides a way to create a Python schema file from an existing textual
binding file, i.e. a .txt file inside Documentation/devicetree/bindings. This
is not a fully comprehensive conversion by any means, just a proof of concept.

THERE ARE NO TESTS, few comments and the code is pretty rough. Read at own risk.
This is written to show that it is possible, not as an example of how to solve
this problem. Experiments with a few markdown parsers were unsuccessful, so I
ended up with this :-)

Run with -d to get a full exception trace.
"""

from __future__ import print_function

import argparse
from collections import namedtuple, OrderedDict
import os
import sys

# States that we can be in
(S_NAME,        # Name of binding
    S_DESC,     # Description of binding
    S_END,      # End of file
    S_PROP,     # Property (required or optional)
    S_OPTIONS,  # Property options
    S_OPTION,   # A single option of many
    S_EXAMPLE,  # Example(s) of how to use the binding
    S_NODES,    # List of sub-node
    S_NODE,     # Sub-node
)= range(9)

# Names for each state
STATE_NAME = {
    S_NAME: 'name',
    S_DESC: 'desc',
    S_PROP: 'prop',
    S_OPTIONS: 'options',
    S_OPTION: 'option',
    S_EXAMPLE: 'example',
    S_NODES: 'nodes',
    S_NODE: 'node',
}

# Items in the stack, so we can get back to a previous indent level
StackItem = namedtuple('StackItem', ['indent', 'state', 'node'])

# Maps property names to classes
PROP_NAME_TO_CLASS = {
    'reg': 'PropReg',
    'compatible': False,
    'interrupts': 'PropInterrupts',
    'clocks': 'PropClocks',
    '#address-cells': 'PropInt',
    '#size-cells': 'PropInt',
}

# Indent expected for child items in the .txt file
INDENT_DELTA = 8


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
    parser.add_argument('bindings', type=str, nargs='+',
                        help='Paths to the binding files to convert')
    parser.add_argument('-r', '--raise-on-error', action='store_true',
                        help='Causes the converter to raise on the first ' +
                        'error it finds. This is useful for debugging.')
    return parser.parse_args(argv)

def IndentStr(indent):
    """Get spaces for the given indent level

    Args:
        indent: Number of levels to indent

    Returns:
        A string containing the spaces to indent that much
    """
    return ' ' * 4 * indent

class Property:
    """Models a single property in the binding file"""
    def __init__(self, name, required, desc):
        self.name = name
        self.required = required
        self._desc = [desc]
        self._options = []

    def AddDesc(self, desc):
        self._desc.append(desc)

    def AddOption(self, option):
        self._options.append(option)

    def GetValue(self):
        val = []
        for opt in self._options:
            val.append(opt.value)
        return val

    def GetDesc(self):
        if self._desc and not self._desc[-1]:
            self._desc = self._desc[:-1]
        return self._desc

    def GetOptions(self):
        return self._options

    def RemoveFinalBlankLine(self):
        if self._desc and not self._desc[-1]:
            self._desc = self._desc[:-1]

class Node:
    """Models a node in the binding file, containing subnodes and properties"""
    def __init__(self, name, required, desc):
        self.name = name
        self.required = required
        self._desc = [desc] if desc is not None else []
        self._props = OrderedDict()
        self._prop_lines = []
        self._subnodes = OrderedDict()

    def AddProp(self, prop):
        self._props[prop.name] = prop

    def AddSubnode(self, subnode):
        self._subnodes[subnode.name] = subnode

    def GetProps(self):
        return self._props.values()

    def GetSubnodes(self):
        return self._subnodes.values()

    def GetDesc(self):
        if self._desc and not self._desc[-1]:
            self._desc = self._desc[:-1]
        return self._desc

    def GetProp(self, name):
        return self._props.get(name)

    def AddPropLine(self, line):
        self._prop_lines.append(line)

    def AddDesc(self, desc):
        self._desc.append(desc)

    def GetPropLines(self):
        return self._prop_lines

class Option:
    """Models an option in the binding file, a possible value for a property"""
    def __init__(self, value, desc):
        self._raw_value = value
        if isinstance(value, str) and value.startswith('"'):
            value = value[1:-1]
        else:
            try:
                value = int(value)
            except ValueError:
                value = None
        self.value = value
        self._desc = [desc]

    def AddDesc(self, desc):
        self._desc.append(desc)


class BindingConverter(object):
    """Converter for binding files

    Properties:
        _raise_on_error: True if the validator should raise on the first error
            (useful for debugging)
    """
    def __init__(self, raise_on_error):
        self._raise_on_error = raise_on_error
        self._infd = None
        self._line = None
        self._stack = []
        self._state = None
        self._binding_name = None
        self._used_types = set()

    def PeekLine(self):
        if self._line is None:
            self._line = self._infd.readline()
        rest = self._line.lstrip()
        indent = 0
        chars = len(self._line) - len(rest)
        for ch in self._line[:chars]:
            if ch == '\t':
                indent += 8
            else:
                indent += 1
        self._indent = indent
        return self._line.strip()

    def ConsumeLine(self):
        self._line = None

    def GetLine(self):
        line = self.PeekLine()
        self.ConsumeLine()
        return line

    def GetPara(self):
        para = []
        while True:
            line = self.GetLine()
            if not line and para:
                break
            para.append(line)
        return '\n'.join(para)

    def GetOption(self):
        opt = []
        line = self.GetLine()
        if line[0:2] != '* ':
            self.Raise("Expected '* ' at start of option line '%s'" % line)
        opt.append(line)
        indent = self._indent
        while True:
            line = self.PeekLine()
            if not line or self._indent <= indent:
                break
            opt.append(line)
            self.ConsumeLine()
        return '\n'.join(opt)

    def GetListItem(self, line, line_type):
        if line[0:2] != '- ':
            self.Raise("Expected '- ' at start of '%s' line '%s'" %
                       (line_type, line))
        pos = line.find(':')
        if pos == -1:
            self.Raise("Expected ':' at end %s name '%s'" % (line_type, line))
        return line[2:pos], line[pos + 2:]

    def Raise(self, msg):
        print('State %d/%s: Error: %s' % (self._state, STATE_NAME[self._state],
                                          msg), file=sys.stderr)
        sys.exit(1)

    def PushState(self, indent, node):
        self._stack.append(StackItem(indent, self._state, node))

    def PopState(self, line):
        if not self._stack:
            self.Raise("Stack underflow at '%s'" % line)
        item = self._stack.pop()
        return item.indent, item.state, item.node

    def Process(self, infd, node):
        """Process the input file and record the information in 'node'

        This function is basically an ad-hoc state machine which attempts to
        deal with the binding file, which does not seem to be in a particularly
        regular format.

        Args:
            infd: Input file (a .txt file from Documentation/devicetree/binding)
            node: Node object to put information into

        Raises:
            Valueerror if something goes wrong
        """
        self._infd = infd

        self._state = S_NAME
        name = ''
        base_indent = 0
        required = False  # Property is mandatory (else optional)
        range_start = None  # Not in a range of options
        pending_subnode = False
        for linenum, line in enumerate(infd.read().splitlines()):
            #print('State %d/%s: base_indent=%d, %s' %
                  #(self._state, STATE_NAME[self._state], base_indent, line))

            rest = line.lstrip()
            indent = 0
            chars = len(line) - len(rest)
            for ch in line[:chars]:
                if ch == '\t':
                    indent += 8
                else:
                    indent += 1

            if line:
                while indent < base_indent:
                    #print('pop indent=%d, base=%d' % (indent, base_indent))
                    base_indent, self._state, node = self.PopState(line)
                    #print('State %d/%s: %s' % (self._state,
                                               #STATE_NAME[self._state], line))
                    #print('indent=%d, new base=%d' % (indent, base_indent))

            tag = None

            if self._state in (S_DESC, S_NODE):
                if indent >= base_indent and line and line[-1] == ':':
                    tag = rest[:-1]

            if tag:
                self.PushState(base_indent, node)
                if tag == 'Required properties':
                    required = True
                    self._state = S_PROP
                elif tag == 'Optional properties':
                    required = False
                    self._state = S_PROP
                elif tag in ['Example', 'Examples']:
                    self._state = S_EXAMPLE
                elif tag == 'Required subnodes':
                    required = True
                    self._state = S_NODES
                else:
                    #self.Raise("Unknonwn tag in '%s'" % line)
                    #self._state = S_PROP
                    tag = None
                if tag:
                    base_indent = (indent + INDENT_DELTA) & 0xf8
                    continue
            elif 'child node' in rest:
                pending_subnode = True
                self._state = S_DESC
                subnode = Node(None, required, rest)
                node.AddSubnode(subnode)
                node = subnode
                continue

            if self._state == S_NAME:
                if indent:
                    self.Warn("Expected name to unindented (indent=%d, line='%s')"
                              % (indent, line))
                self._binding_name = line
                self._state = S_DESC
            elif self._state == S_DESC:
                if line:
                    node.AddDesc(rest)
            elif self._state == S_PROP:
                prop_name, desc = self.GetListItem(rest, 'prop')
                self.PushState(base_indent, node)
                self._state = S_OPTIONS
                range_start = None
                prop = Property(prop_name, required, desc)
                node.AddProp(prop)
                base_indent += 2
            elif self._state == S_OPTIONS:
                if rest[0:2] == '* ':
                    pos = rest.find(':')
                    if pos == -1:
                        self.Raise("Expected ':' at end of option name '%s'" %
                                   line)
                    opt_value = rest[2:pos]
                    if range_start is not None:
                        try:
                            range_end = int(opt_value)
                        except:
                            self.Raise("Expected int value (not '%s') for range" %
                                       opt_value)
                        for val in range(range_start + 1, range_end - 1):
                            opt = Option(val, '')
                            prop.AddOption(opt)

                    self.PushState(base_indent, node)
                    self._state = S_OPTION
                    opt = Option(opt_value, rest[pos:])
                    prop.AddOption(opt)
                    base_indent = indent + 2

                # Handle a range of integer values
                elif rest == '...':
                    try:
                        range_start = int(opt_value)
                    except:
                        self.Raise("Expected int value (not '%s') for range" %
                                   opt_value)
                else:
                    if pending_blank_line:
                        prop.AddDesc('')
                    prop.AddDesc(rest)
                pending_blank_line = False
            elif self._state == S_OPTION:
                if rest:
                    opt.AddDesc(rest)
                else:
                    pending_blank_line = True
            elif self._state == S_NODES:
                node_name, desc = self.GetListItem(rest, 'node')
                self.PushState(base_indent, node)
                self._state = S_NODE
                subnode = Node(node_name, required, desc)
                node.AddSubnode(subnode)
                node = subnode
                base_indent += 2
            elif self._state == S_NODE:
                node.AddDesc(rest)

    def GenerateNodeOutput(self, node, indent):
        """Generate the output for a node, its properties and subnodes

        Output is added by calling node.AddPropLine() for each line.

        Args:
            node: Node object to process
            indent: Starting indent level (0 for none, 1 for one level, etc.)
        """
        for prop in node.GetProps():
            need_prop_name_str = False
            pattern_str = ''
            class_name = PROP_NAME_TO_CLASS.get(prop.name)
            if class_name is False:
                continue
            #not class_name and
            if prop.GetOptions():
                value_list = []
                single_type = None  # See if all values are the same type
                for opt in prop.GetOptions():
                    value_list.append(str(opt.value))
                    this_type = type(opt.value)
                    if single_type is None:
                        single_type = this_type
                    elif single_type and single_type != this_type:
                        single_type = False
                if single_type == str:
                    class_name = 'PropStringList'
                    pattern_str = "str_pattern='%s'," % '|'.join(value_list)
                elif single_type == int:
                    class_name = 'PropIntList'
                    pattern_str = "valid_list='%s'," % '|'.join(value_list)
                if class_name:
                    need_prop_name_str = True
            elif not class_name:
                class_name = 'PropBool'
                need_prop_name_str = True
            if class_name == 'PropInt':
                need_prop_name_str = True
            if not class_name:
                class_name = 'PropDesc'
                need_prop_name_str = True
            prop_name_str = "'%s', " % prop.name if need_prop_name_str else ''
            self._used_types.add(class_name)
            req_str = 'required=True, ' if prop.required else ''
            prop.RemoveFinalBlankLine()
            node.AddPropLine("%s%s(%s%s%s" %
                             (IndentStr(indent), class_name, prop_name_str,
                              req_str, pattern_str))
            desc_lines = prop.GetDesc()
            if desc_lines:
                for num, line in enumerate(desc_lines):
                    desc = not num and 'desc=' or ''
                    desc = "%s%s'%s'" % (IndentStr(indent + 1), desc, line)
                    if num == len(desc_lines) - 1:
                        desc += '),'
                    node.AddPropLine(desc)
            else:
                node.AddPropLine("%sdesc='')," % (IndentStr(indent + 1)))
        for subnode in node.GetSubnodes():
            self.GenerateNodeOutput(subnode, indent)

    def OutputNode(self, outfd, node, indent):
        """Output a node to the output file

        Args:
            outfd: Output file
            node: Node object to output
            indent: Starting indent level (0 for none, 1 for one level, etc.)
        """
        compat = node.GetProp('compatible')
        if compat:
            compat_list = "', '".join(compat.GetValue())
            compat_str = "['%s']" % compat_list
        else:
            compat_str = 'None'
        desc = ''
        if node.GetDesc():
            desc = ("'\n%s'" % IndentStr(indent + 2)).join(node.GetDesc())
            desc = ", desc=\n%s'%s'" % (IndentStr(indent + 2), desc)
        print("%sNodeDesc('%s', %s, False%s, elements=[" %
              (IndentStr(indent), node.name, compat_str, desc), file=outfd)
        for prop_line in node.GetPropLines():
            print('%s%s' % (IndentStr(indent + 1), prop_line), file=outfd)
        for subnode in node.GetSubnodes():
            self.OutputNode(outfd, subnode, indent + 1)
        print('%s]),' % IndentStr(indent + 1), file=outfd)

    def Output(self, outfd, node):
        """Output a Python binding to the output file

        This generates a (hopefully valid) Python binding file based on the
        .txt binding file that was read.

        Args:
            outfd: Output file
            node: Node object to output
        """        print('# SPDX-License-Identifier: GPL-2.0+', file=outfd)
        print('#', file=outfd)
        print(file=outfd)
        print('# %s' % self._binding_name, file=outfd)
        print(file=outfd)
        if self._used_types:
            print('from kschema import %s' %
                  (', '.join(sorted(self._used_types))), file=outfd)
        print(file=outfd)
        print('schema = [', file=outfd)
        self.OutputNode(outfd, node, 1)
        print('%s]' % IndentStr(1), file=outfd)

    def Convert(self, fname):
        """Convert a .txt binding file into a .py schema file

        The output write is written to the same directory as the input file, but
        with a .py extension.

        Args:
            fname: Full path of file to convert
        """
        basename = os.path.split(fname)[1]
        root = os.path.splitext(fname)[0]

        outfname = root + '.py'
        leafname = os.path.splitext(basename)[0]
        self._used_types = set(['NodeDesc'])
        with open(fname) as infd:
            with open(outfname, 'w') as outfd:
                node = Node(leafname, False, None)
                self.Process(infd, node)
                self.GenerateNodeOutput(node, 0)
                self.Output(outfd, node)

def Main(argv=None):
    """Main program

    This contains the main logic of this program.

    Args:
        argv: Arguments to the program (excluding argv[0]); if None, uses
                sys.argv
    """
    if argv is None:
        argv = sys.argv[1:]
    args = ParseArgv(argv)
    converter = BindingConverter(args.raise_on_error)
    found_errors = False
    try:
        for fname in args.bindings:
            converter.Convert(fname)
    except Exception as e:
        if args.debug:
            raise
        print('Failed: %s' % e, file=sys.stderr)
        found_errors = True
    if found_errors:
        sys.exit(1)

if __name__ == '__main__':
    Main()
