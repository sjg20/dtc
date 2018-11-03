# Copyright 2019 Google LLC
# Written by Simon Glass <sjg@chromium.org>
#
# Taken from U-Boot v2017.07 (tools/dtoc)
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

"""Utility functions for fdt."""

from __future__ import print_function

import os
import struct
import subprocess
import sys
import tempfile


def fdt32_to_cpu(val):
    """Convert a device tree cell to an integer

    Args:
        val: Value to convert (4-character string representing the cell value)

    Returns:
        A native-endian integer value
    """
    if sys.version_info > (3, 0):
        if isinstance(val, bytes):
            val = val.decode('utf-8')
        val = val.encode('raw_unicode_escape')
    return struct.unpack('>I', val)[0]

def RunCommand(args):
    """Run a command with arguments

    Args:
        args: Command (args[0]) and arguments (args[1:])
    """
    process = subprocess.Popen(args, stdout=subprocess.PIPE,
                               stderr=subprocess.PIPE)
    process.communicate()

def CompileDts(dts_input, search_paths):
    """Compiles a single .dts file

    This runs the file through the C preprocessor and then compiles it to .dtb
    format.

    Args:
        dts_input: Input filename
        search_paths: Paths to search for header files

    Returns:
        Tuple:
            Filename of resulting .dtb file
            tempfile containing the .dtb file
    """
    dtc_input = tempfile.NamedTemporaryFile(suffix='.dts', delete=False)
    root, _ = os.path.splitext(dts_input)
    args = ['-E', '-P', '-x', 'assembler-with-cpp', '-D__ASSEMBLY__']
    args += ['-Ulinux']
    for path in search_paths or []:
            args.extend(['-I', path])
    args += ['-o', dtc_input.name, dts_input]
    RunCommand(['cc'] + args)

    dtb_output = tempfile.NamedTemporaryFile(suffix='.dtb', delete=False)
    args = ['-I', 'dts', '-o', dtb_output.name, '-O', 'dtb']
    args.append(dtc_input.name)
    RunCommand(['dtc'] + args)
    return dtb_output.name, dtb_output


def EnsureCompiled(fname, search_paths=None):
    """Compile an fdt .dts source file into a .dtb binary blob if needed.

    Args:
        fname: Filename (if .dts it will be compiled). It not it will be
            left alone
        search_paths: Paths to search for header files

    Returns:
        Tuple:
            Filename of resulting .dtb file
            tempfile object to unlink after the caller is finished
    """
    out = None
    _, ext = os.path.splitext(fname)
    if ext == '.dtb':
        return fname, None
    else:
        dts_input = fname
    result = CompileDts(dts_input, search_paths)
    if out:
        os.unlink(out.name)
    return result


def CompileAll(fnames):
    """Compile a selection of .dtsi files

    This inserts the Chrome OS header and then includes the files one by one to
    ensure that error messages quote the correct file/line number.

    Args:
        fnames: List of .dtsi files to compile
    """
    out = tempfile.NamedTemporaryFile(suffix='.dts', delete=False)
    out.write('/dts-v1/;\n')
    out.write('/ { chromeos { family: family { }; models: models { };')
    out.write('schema { target-dirs { }; }; }; };\n')
    for fname in fnames:
        out.write('/include/ "%s"\n' % fname)
    out.close()
    dts_input = out.name
    result = CompileDts(dts_input)
    if out:
        os.unlink(out.name)
    return result


def GetCompatibleList(node):
    """Gets the list of compatible strings for a node

    Args:
        node: Node object to check

    Returns:
        List containing each string in the node's 'compatible' property
    """
    compats = node.props.get('compatible')
    if compats is None:
        return None
    if isinstance(compats.value, list):
        compats = [c for c in compats.value]
    else:
        compats = [compats.value]
    return compats
