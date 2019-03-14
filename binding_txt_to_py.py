# Copyright 2019 Google LLC

"""Convert textual binding file to Python (poorly).

This provides a way to create a Python binding file from an extisting textual
one. This is not a fully comprehensive conversion, but it does its best. While
it does not complete the job, it hopefully speeds up the process. If it does
not, please send a patch.
"""

from __future__ import print_function

import argparse
from collections import namedtuple, OrderedDict
import os
import sys

(S_NAME,        # Name of binding
    S_DESC,     # Description of binding
    S_TAG,      # Tag indicating next section, followed by ':'
    S_END,      # End of file
    S_PROP,     # Property (required or optional)
    S_COMPAT,   # Property definition
    S_OPTIONS,  # Property options
    S_OPTION,   # A single option of many
    S_EXAMPLE,  # Example(s) of how to use the binding
    S_NEXT,     # Determine self._state by the line contents
)= range(10)

STATE_NAME = {
    S_NAME: 'name',
    S_DESC: 'desc',
    S_TAG: 'tag',
    S_PROP: 'prop',
    S_COMPAT: 'compat',
    S_OPTIONS: 'options',
    S_OPTION: 'option',
    S_EXAMPLE: 'example',
    S_NEXT: 'next',
}

StackItem = namedtuple('StackItem', ['indent', 'state'])
#Property = namedtuple('Property', ['name', 'required', 'compatible'])


# Indent expected for child items
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


class Property:
    def __init__(self, name, required, desc):
        self.name = name
        self.required = required
        self.desc = [desc]
        self.options = []

    def GetValue(self):
        val = []
        for opt in self.options:
            val.append(opt.name)
        return val

class Option:
    def __init__(self, name, desc):
        if name.startswith('"'):
            name = name[1:-1]
        self.name = name
        self.desc = [desc]

    def AddDesc(self, desc):
        self.desc.append(desc)


class BindingConverter(object):
    """Converter for binding files

    Properties:
        _raise_on_error: True if the validator should raise on the first error
            (useful for debugging)
    """
    def __init__(self, raise_on_error):
        self._raise_on_error = raise_on_error
        self._infd = None
        self._outfd = None
        self._line = None
        self._stack = []
        self._state = None
        self._props = OrderedDict()

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

    def Raise(self, msg):
        print('State %d/%s: Error: %s' % (self._state, STATE_NAME[self._state],
                                          msg), file=sys.stderr)
        sys.exit(1)

    def PushState(self, indent):
        self._stack.append(StackItem(indent, self._state))

    def PopState(self, line):
        if not self._stack:
            self.Raise("Stack underflow at '%s'" % line)
        item = self._stack.pop()
        return item.indent, item.state

    def Process(self, infd, outfd):
        self._infd = infd
        self._outfd = outfd

        self._state = S_NAME
        name = ''
        desc = []
        base_indent = 0
        required = False  # Property is mandatory (else optional)
        for linenum, line in enumerate(infd.read().splitlines()):
            print('State %d/%s: %s' % (self._state, STATE_NAME[self._state],
                                       line))

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
                    print('pop indent=%d, base=%d' % (indent, base_indent))
                    base_indent, self._state = self.PopState(line)
                    print('State %d/%s: %s' % (self._state,
                                               STATE_NAME[self._state], line))
                    print('indent=%d, new base=%d' % (indent, base_indent))

            tag = None
            if not line:
                continue

            if not indent and line[-1] == ':':
                tag = line[:-1]
            elif rest[0:2] == '- ':
                pass

            if tag:
                self.PushState(base_indent)
                if tag == 'Required properties':
                    required = True
                    self._state = S_PROP
                elif tag == 'Optional properties':
                    required = False
                    self._state = S_PROP
                elif tag in ['Example', 'Examples']:
                    self._state = S_EXAMPLE
                else:
                    self.Raise("Unknonwn property in '%s'" % line)
                base_indent += INDENT_DELTA
                continue

            if self._state == S_NAME:
                if indent:
                    self.Warn("Expected name to unindented (indent=%d, line='%s')"
                              % (indent, line))
                name = self.GetLine()
                self._state = S_DESC
            elif self._state == S_DESC:
                # Ignore blank line at start
                if desc or line:
                    desc.append(line)
            elif self._state == S_PROP:
                if rest[0:2] != '- ':
                    self.Raise("Expected '- ' at start of prop line '%s'" %
                               line)
                pos = rest.find(':')
                if pos == -1:
                    self.Raise("Expected ':' at end prop name '%s'" % line)
                prop_name = rest[2:pos]

                self.PushState(base_indent)
                self._state = S_OPTIONS
                prop = Property(prop_name, required, rest[pos + 2:])
                self._props[prop_name] = prop
                base_indent += 2
            elif self._state == S_OPTIONS:
                if rest[0:2] == '* ':
                    pos = rest.find(':')
                    if pos == -1:
                        self.Raise("Expected ':' at end of option name '%s'" % line)
                    opt_name = rest[2:pos]

                    self.PushState(base_indent)
                    self._state = S_OPTION
                    opt = Option(opt_name, rest[pos:])
                    prop.options.append(opt)
                    print(prop.options)
                    base_indent = indent + 2
                else:
                    prop.desc.append(rest)
            elif self._state == S_OPTION:
                opt.AddDesc(rest)

        print('# SPDX-License-Identifier: GPL-2.0+', file=outfd)
        print('#', file=outfd)
        print(file=outfd)
        print('# %s' % name, file=outfd)
        print(file=outfd)
        print('from kschema import NodeDesc', file=outfd)        
        print(file=outfd)
        print('schema = [', file=outfd)
        compat_str = "', '".join(self._props['compatible'].GetValue())
        print("    NodeDesc('regulator-fixed', ['%s'], False, [" % compat_str,
              file=outfd)
        for prop in self._props.values():
            req_str = ', required=True' if prop.required else ''
            desc = "'\n                '".join(prop.desc)
            print("        PropDesc('%s'%s," % (prop.name, req_str, ),
                  file=outfd)
            print("            desc='%s')," % desc, file=outfd)
            #for opt in prop.options:
                #print(opt.name, opt.desc, file=outfd)
        print('        ],', file=outfd)
        #print("        desc='%s')" % '\n'.join(desc), file=outfd)
        print('    ]', file=outfd)

    def Convert(self, fname):
        leaf, ext = os.path.splitext(fname)
        outfname = leaf + '.py'
        with open(fname) as infd:
            with open(outfname, 'w') as outfd:
                self.Process(infd, outfd)

def Main(argv=None):
    """Main program

    This contains the main logic of this program.

    Args:
        argv: Arguments to the problem (excluding argv[0]); if None, uses sys.argv
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
