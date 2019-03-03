# Copyright 2019 Google LLC

"""Convert textual binding file to Python (poorly).

This provides a way to create a Python binding file from an extisting textual
one. This is not a fully comprehensive conversion, but it does its best. While
it does not complete the job, it hopefully speeds up the process. If it does
not, please send a patch.
"""

from __future__ import print_function

import argparse
import os
import sys

(S_NAME,        # Name of binding
    S_DESC,     # Description of binding
    S_TAG,      # Tag indicating next section, followed by ':'
    S_END,      # End of file
    S_PROP,     # Property (required or optional)
    S_COMPAT,   # Compatible string
)= range(6)


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

    def GetLine(self):
        return self._infd.readline().strip()

    def GetPara(self):
        para = []
        while True:
            line = self.GetLine()
            if not line and para:
                break
            para.append(line)
        return '\n'.join(para)

    def Raise(self, msg):
        print('State %d: Error: %s' % (self._state, msg), file=sys.stderr)
        sys.exit(1)

    def Process(self, infd, outfd):
        self._infd = infd
        self._outfd = outfd

        self._state = S_NAME
        while self._state != S_END:
            if self._state == S_NAME:
                name = self.GetLine()
                self._state = S_DESC
            elif self._state == S_DESC:
                desc = self.GetPara()
                self._state = S_TAG
            elif self._state == S_TAG:
                tag = self.GetLine()
                if not tag:
                    break
                if tag[-1] != ':':
                    self.Raise("Expected ':' at end of tag line '%s'" % tag)
                tag = tag[:-1]
                if tag == 'Required properties':
                    self.required = True
                    self._state = S_PROP
                elif tag == 'Optional properties':
                    self.required = False
                    self._state = S_PROP
            elif self._state == S_PROP:
                line = self.GetLine()
                if line[0:2] != '- ':
                    self.Raise("Expected '- ' at start of prop line '%s'" %
                               line)
                pos = line.find(':')
                if pos == -1:
                    self.Raise("Expected ':' at end prop name '%s'" % line)
                prop = line[2:pos]
                if prop == 'compatible':
                    self._state = S_COMPAT
                else:
                    self.Raise("Unknown property name '%s'" % prop)
            elif self._state == S_COMPAT:
                pass


        print('# SPDX-License-Identifier: GPL-2.0+', file=outfd)
        print('#', file=outfd)
        print(file=outfd)
        print('# %s' % name, file=outfd)
        print(file=outfd)
        print('from kschema import NodeDesc', file=outfd)        
        print(file=outfd)
        print('schema = [', file=outfd)
        print("    NodeDesc('regulator-fixed', ['regulator-fixed'], False, [",
              file=outfd)
        print('        ],', file=outfd)
        print("        desc='%s')" % desc, file=outfd)
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
