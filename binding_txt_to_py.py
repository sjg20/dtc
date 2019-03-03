# Copyright 2019 Google LLC

"""Convert textual binding file to Python (poorly).

This provides a way to create a Python binding file from an extisting textual
one. This is not a fully comprehensive conversion, but it does its best. While
it does not complete the job, it hopefully speeds up the process. If it does
not, please send a patch.
"""

from __future__ import print_function

import argparse
import sys

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

    def Convert(self, fname):
        pass


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
    except:
        if args.debug:
            raise
        print('Failed: %s' % e, file=sys.stderr)
        found_errors = True
    if found_errors:
        sys.exit(1)

if __name__ == '__main__':
    Main()
