#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0 or BSD-3-Clause
# Copyright 2023 Google LLC
# Written by Simon Glass <sjg@chromium.org>

"""Convert an ELF executable to a file suitable for use with the Universal
Payload Specification

usage: uni_build.py -a ARCH -d DESCRIPTION -o OUTFILE -O OS -m dll_file fv1 fv2

Splits up elf_file into segments, putting each segment in its image image in
a UPS file. Note that ULS is a Flat Image Tree (FIT) with a few extra fields.
See [1] for information about FIT.

Use the -t option to run tests.

https://github.com/u-boot/u-boot/blob/master/doc/uImage.FIT/source_file_format.txt

For example:

   pip install pylibfdt pyelftools
   ../dtc/uni_build.py \
        -m Build/UefiPayloadPkgX64/DEBUG_CLANGDWARF/X64/UefiPayloadPkg/UefiPayloadEntry/UniversalPayloadEntry/DEBUG/UniversalPayloadEntry.dll \
        ./Build/UefiPayloadPkgX64/DEBUG_CLANGDWARF/FV/DXEFV.Fv \
        ./Build/UefiPayloadPkgX64/DEBUG_CLANGDWARF/FV/BDSFV.Fv -o uefi.fit
"""

import argparse
import io
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile
import unittest

import elftools.elf.elffile
import libfdt


from libfdt import QUIET_NOTFOUND


def read_loadable_segments(data):
    """Read segments from an ELF file

    Args:
        data (bytes): Contents of file

    Returns:
        tuple:
            list of segments, each:
                int: Segment number (0 = first)
                int: Start address of segment in memory
                bytes: Contents of segment
            int: entry address for image
            int: bit size of ELF (32 or 64)

    Raises:
        ValueError: elftools is not available
    """
    with io.BytesIO(data) as inf:
        elf = elftools.elf.elffile.ELFFile(inf)
        elf_class = elf.header['e_ident']['EI_CLASS']
        bit_size = 64 if elf_class == 'ELFCLASS64' else 32
        entry = elf.header['e_entry']
        segments = []
        for i in range(elf.num_segments()):
            segment = elf.get_segment(i)
            if segment['p_type'] != 'PT_LOAD' or not segment['p_memsz']:
                continue
            start = segment['p_offset']
            rend = start + segment['p_filesz']
            segments.append((i, segment['p_paddr'], data[start:rend]))
    return segments, entry, bit_size


def get_props(fdt, node_offset):
    """Get all properties from a node

    Args:
        node_offset (int): Offset of node to scan

    Returns:
        dict:
            key: property name
            valueL property offset
    """
    props_dict = {}
    poffset = fdt.first_property_offset(node_offset, QUIET_NOTFOUND)
    while poffset >= 0:
        prop = fdt.get_property_by_offset(poffset)
        props_dict[prop.name] = poffset

        poffset = fdt.next_property_offset(poffset, QUIET_NOTFOUND)
    return props_dict


def fdt_from_list(str_list):
    """Convert a list into an FDT string list property

    Args:
        str_list (list of str): List of strings to convert

    Returns:
        bytes: String list in FDT format
    """
    return b'\0'.join([s.encode('utf-8') for s in str_list]) + b'\0'


def fdt_to_list(strval):
    """Convert an FDT list into a string list

    Args:
        strval (bytes): FDT property value to convert

    Returns:
        list of str: String list
    """
    return [s.decode() for s in strval[:-1].split(b'\0')]


def to_int(prop, expect_bit_size):
    """Convert an FDT property to an integer

    This expects the property to be either 4 or 8 bytes, depending on
    expect_bit_size

    Args:
        prop (Property): Property to convert
        expect_bit_size (int): 32 or 64, indicating the bit size
    """
    if expect_bit_size == 64:
        return prop.as_uint64()
    return prop.as_uint32()


def add_image(fsw, basename, props, bit_size, seq, start, data, entry_addr):
    """Add an image to a FIT

    The entry address is emitted if it seq == 0

    Args:
        fsw (FdtSw): Software writer object
        props (dict): Properties to use for writing. Must include
            description, arch, type. The others are optional
        bit_size (int): Bit size to use (32 or 64)
        seq (int): Image sequence number (0 for first)
        start (int): Start address of this segment
        data (bytes): Data for this segment
        entry_addr (int): Entry address to use
    """
    node_name = f'{basename}-{seq}'
    with fsw.add_node(node_name):

        # Since this is all one ELF file we don't need to repeat the properties
        if not seq:
            fsw.property_string('description', props['description'])
            fsw.property_string('arch', props['arch'])
            if props['build-type']:
                fsw.property_string('build-type', props['build-type'])
            fsw.property_u32('revision', props['revision'])
            if props['capabilities']:
                fsw.property('capabilities',
                             fdt_from_list(props['capabilities']))
            if props['os']:
                fsw.property_string('os', props['os'])
            if props['producer']:
                fsw.property_string('producer', props['producer'])

        fsw.property_string('type', 'firmware')
        fsw.property_string('compression', props.get('compression', 'none'))
        if bit_size == 64:
            fsw.property_u64('load', start)
            fsw.property_u64('size', len(data))
            if not seq and entry_addr is not None:
                fsw.property_u64('entry', entry_addr)
        else:
            fsw.property_u32('load', start)
            fsw.property_u32('size', len(data))
            if not seq and entry_addr is not None:
                fsw.property_u32('entry', entry_addr)
        fsw.property('data', bytes(data))
    return node_name


def build_it(main_fname, fv_fnames, props):
    """Build a universal payload

    Args:
        elf_fname (str): Filename of ELF file to convert
        props (dict): Contains properties to put into the FIT

    Returns:
        bytes: FIT payload in binary form
    """
    with open(main_fname, 'rb') as inf:
        segments, entry_addr, bit_size = read_loadable_segments(inf.read())
    timestamp = int(os.stat(main_fname).st_mtime)

    # Build a new tree with all nodes and properties starting from the
    # entry node
    fsw = libfdt.FdtSw()
    fsw.INC_SIZE = 65536
    fsw.finish_reservemap()
    with fsw.add_node(''):
        fsw.property_string('description', props['description'])
        fsw.property_u32('timestamp', timestamp)
        fsw.property_string('compatible', 'universal-osloader')
        fsw.property_u32('uol-version', 0x0100)
        firmware = None
        loadables = []
        with fsw.add_node('images'):
            # Specify explicit load addresses, putting the FVs on a 4KB boundary
            load = 0
            for seq, start, data in segments:
                node_name = add_image(fsw, 'tianocore', props, bit_size, seq,
                                      start, data, entry_addr)
                if not seq:
                    firmware = node_name
                else:
                    loadables.append(node_name)
                load = max(load, start + len(data))
            load = (load + 0x1000) & ~0xfff
            for seq, fv_fname in enumerate(fv_fnames):
                with open(fv_fname, 'rb') as inf:
                    data = inf.read()
                node_name = add_image(fsw, 'fv', props, bit_size, seq, load,
                                      data, None)
                loadables.append(node_name)
                load += len(data)
                load = (load + 0x1000) & ~0xfff

        with fsw.add_node('configurations'):
            with fsw.add_node('conf-1'):
                fsw.property_string('description', props['description'])
                fsw.property_string('firmware', firmware)
                if loadables:
                    fsw.property('loadables', fdt_from_list(loadables))

    fdt = fsw.as_fdt()
    return fdt.as_bytearray()


def parse_args(argv):
    """Parse arguments provided to the tool

    Args:
        argv (list of str): List of arguments, excluding the program name

    Returns:
        argparse object providing access to the arguments provded
    """
    epilog = 'Convert an ELF file to Universal Payload Format'
    parser = argparse.ArgumentParser(epilog=epilog)
    parser.add_argument('-a', '--arch', type=str, default='x86_64',
                        help='Set architecture')
    parser.add_argument('-b', '--build-type', type=str, help='Set build type')
    parser.add_argument('-c', '--capabilities', type=str, default='',
                        help='Set capabilities (comma-separated)')
    parser.add_argument('-C', '--compression', type=str, default='none',
                        help='Set compression')
    parser.add_argument('-d', '--description', type=str, default='UEFI',
                        help='Set description')
    parser.add_argument('-m', '--main', type=str,
                        help='Main DLL file to package')
    parser.add_argument('-o', '--outfile', type=str, help='Set output file')
    parser.add_argument('-O', '--os', type=str, default="uefi", help='Set Operating System')
    parser.add_argument('-p', '--producer', type=str, help='Set producer')
    parser.add_argument('-r', '--revision', type=int, default=1,
                        help='Set revision')
    parser.add_argument('-t', '--test', action='store_true', help='Run tests')
    parser.add_argument('fv', type=str, nargs='*', help='FV file to package')
    args = parser.parse_args(argv)
    if not args.test:
        if not all([args.main, args.arch, args.description, args.os,
                   args.outfile]):
            parser.error('Must provide arch, description, os and outfile')
    return args


class UniBuildTests(unittest.TestCase):
    """Test class for uni_build

    Properties:
        fdt: Device tree file used for testing
    """
    def setUp(self):
        self.tmpdir = pathlib.Path(tempfile.mkdtemp(prefix='uni_build.'))

        # Create an ELF file
        src = '''/* Sample ELF file to use for testing uni_build.py */

int mydata[0x10] = {1, 2, 3, 4};

int _start(void)
{
}
'''
        lds_src = '''
/* SPDX-License-Identifier: GPL-2.0+ */
/*
 * Copyright (c) 2011-2012 The Chromium OS Authors.
 * Use of this source code is governed by a BSD-style license that can be
 * found in the LICENSE file.
 */

ENTRY(_start)

SECTIONS
{
    . = 0x5000;

    .text  : { *(.text*); }
    . = ALIGN(4);
    .rodata : {
        *(SORT_BY_ALIGNMENT(SORT_BY_NAME(.rodata*)))
        KEEP(*(.rodata.efi.init));
    }

    /* create a data segment somewhere else */
    . += 0x1000;
    .data : { *(.data*) }

    . = ALIGN(4);
    .hash : { *(.hash*) }

    . = ALIGN(4);
    .got : { *(.got*) }

    . = ALIGN(4);

    . = ALIGN(4);
    .dynsym : { *(.dynsym*) }

    . = ALIGN(4);
    __rel_dyn_start = .;
    .rel.dyn : {
        *(.rel*)
    }
    __rel_dyn_end = .;
    . = ALIGN(4);
    _end = .;

    .bss __rel_dyn_start (OVERLAY) : {
        __bss_start = .;
        *(.bss*)
        *(COM*)
        . = ALIGN(4);
        __bss_end = .;
    }

    /DISCARD/ : { *(.dynstr*) }
    /DISCARD/ : { *(.dynamic*) }
    /DISCARD/ : { *(.plt*) }
    /DISCARD/ : { *(.interp*) }
    /DISCARD/ : { *(.gnu*) }
    /DISCARD/ : { *(.note.gnu.property) }
}
'''

        src_fname = self.tmpdir / 'test.c'
        with open(src_fname, 'w', encoding='utf-8') as outf:
            outf.write(src)

        lds_fname = self.tmpdir / 'test.lds'
        with open(lds_fname, 'w', encoding='utf-8') as outf:
            outf.write(lds_src)

        self.elf_fname = self.tmpdir / 'test'
        subprocess.call(['cc', '-o', self.elf_fname, src_fname, '-Wl,-T',
                         f'-Wl,{lds_fname}', '-ffreestanding', '-nostdlib',
                         '-nostartfiles', '-Wl,--build-id=none'])

    def tearDown(self):
        shutil.rmtree(self.tmpdir)

    def make_test_elf(self):
        """Make an ELF file useful for testing"""

    def test_elf(self):
        """Test packaging an ELF file"""
        expect_bit_size = 64
        props = {
            'description': 'Tianocore edk2-stable202208',
            'arch': 'x86_64',
            'revision': 0x12345678,   # 0xMMmmRRbb - major, minor, rev, build
            'build-type': 'release',
            'capabilities': ['smm-rebase', 'torque-propound'],
            'producer': 'My company',
            'os': 'tianocore',
            }

        u_loader = build_it(self.elf_fname, [], props)

        fdt = libfdt.Fdt(u_loader)
        outprops = get_props(fdt, 0)
        self.assertEqual({'compatible', 'description', 'timestamp',
                          'uol-version'}, outprops.keys())

        images = fdt.subnode_offset(0, 'images')
        outprops = get_props(fdt, images)
        self.assertFalse(outprops.keys())

        first = fdt.subnode_offset(images, 'tianocore-0')
        outprops = get_props(fdt, first)
        self.assertEqual(
            ['arch', 'build-type', 'capabilities', 'compression', 'data',
             'description', 'entry', 'load', 'os', 'producer',
             'revision', 'size', 'type'],
            sorted(outprops.keys()))

        # The text section should be first. Make sure the data is correct
        text_fname = self.tmpdir / 'text'
        subprocess.call(['objcopy', '-O', 'binary', '-j', '.text',
                        self.elf_fname, text_fname])
        with open(text_fname, 'rb') as inf:
            expected = inf.read()

        data = fdt.getprop(first, 'data')
        self.assertEqual(expected, data[:len(expected)])

        # Check the properties
        self.assertEqual(props['description'],
                         fdt.getprop(first, 'description').as_str())
        self.assertEqual(props['arch'],
                         fdt.getprop(first, 'arch').as_str())
        self.assertEqual(props['revision'],
                         fdt.getprop(first, 'revision').as_int32())
        self.assertEqual(props['build-type'],
                         fdt.getprop(first, 'build-type').as_str())
        self.assertEqual(props['capabilities'],
                         fdt_to_list(fdt.getprop(first, 'capabilities')))
        self.assertEqual(props['producer'],
                         fdt.getprop(first, 'producer').as_str())
        self.assertEqual(props['os'], fdt.getprop(first, 'os').as_str())

        self.assertEqual('firmware', fdt.getprop(first, 'type').as_str())
        self.assertEqual(0x5000, to_int(fdt.getprop(first, 'load'),
                                        expect_bit_size))
        self.assertEqual(len(data),
                         to_int(fdt.getprop(first, 'size'), expect_bit_size))
        self.assertEqual(0x5000,
                         to_int(fdt.getprop(first, 'entry'), expect_bit_size))

        # Now check the second node
        second = fdt.subnode_offset(images, 'tianocore-1')
        outprops = get_props(fdt, second)
        self.assertEqual(
            ['compression', 'data', 'load', 'size', 'type'],
            sorted(outprops.keys()))


def run_tests(prog_name):
    """Run tests for this tool

    Args:
        prog_name (str): Program name, i.e. argv[0]
    """
    sys.argv = [prog_name]
    unittest.main()


def main(argv):
    """Main program for the tool

    Args:
        argv (list of str): List of arguments
    """
    args = parse_args(argv[1:])
    if args.test:
        run_tests(argv[0])
    else:
        props = {
            'arch': args.arch,
            'build-type': args.build_type,
            'capabilities': '',
            'compression': args.compression,
            'description': args.description,
            'os': args.os,
            'producer': args.producer,
            'revision': int(args.revision),
            }
        if args.capabilities:
            props['capabilities'] = args.capabilities.split(',')
        u_loader = build_it(args.main, args.fv, props)
        with open(args.outfile, 'wb') as outf:
            outf.write(u_loader)


if __name__ == '__main__':
    main(sys.argv)
