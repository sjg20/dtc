/*
 * Copyright (c) 2013, Google Inc.
 *
 * Perform a grep of an FDT either displaying the source subset or producing
 * a new .dtb subset which can be used as required.
 *
 * This program is free software; you can redistribute it and/or
 * modify it under the terms of the GNU General Public License as
 * published by the Free Software Foundation; either version 2 of
 * the License, or (at your option) any later version.
 *
 * This program is distributed in the hope that it will be useful,
 * but WITHOUT ANY WARRANTY; without even the implied warranty of
 * MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
 * GNU General Public License for more details.
 *
 * You should have received a copy of the GNU General Public License
 * along with this program; if not, write to the Free Software
 * Foundation, Inc., 59 Temple Place, Suite 330, Boston,
 * MA 02111-1307 USA
 */

#include <assert.h>
#include <ctype.h>
#include <getopt.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

#include <libfdt.h>
#include <libfdt_internal.h>
#include "util.h"

/* Define DEBUG to get some debugging output on stderr */
#ifdef DEBUG
#define debug(a, b...) fprintf(stderr, a, ## b)
#else
#define debug(a, b...)
#endif

/* A linked list of values we are grepping for */
struct value_node {
	int type;		/* Types this value matches (FDT_IS... mask) */
	int include;		/* 1 to include matches, 0 to exclude */
	const char *string;	/* String to match */
	struct value_node *next;	/* Pointer to next node, or NULL */
};

/* Output formats we support */
enum output_t {
	OUT_DTS,		/* Device tree source */
	OUT_DTB,		/* Valid device tree binary */
	OUT_BIN,		/* Fragment of .dtb, for hashing */
};

/* Holds information which controls our output and options */
struct display_info {
	enum output_t output;	/* Output format */
	int all;		/* Display all properties/nodes */
	int colour;		/* Display output in ANSI colour */
	int region_list;	/* Output a region list */
	int flags;		/* Flags (FDT_REG_...) */
	int list_strings;	/* List strings in string table */
	int show_offset;	/* Show offset */
	int show_addr;		/* Show address */
	int header;		/* Output an FDT header */
	int diff;		/* Show +/- diff markers */
	int show_dts_version;	/* Put '/dts-v1/;' on the first line */
	int types_inc;		/* Mask of types that we include (FDT_IS...) */
	int types_exc;		/* Mask of types that we exclude (FDT_IS...) */
	int invert;		/* Invert polarity of match */
	struct value_node *value_head;	/* List of values to match */
	const char *output_fname;	/* Output filename */
	FILE *fout;		/* File to write dts/dtb output */
};

static void report_error(const char *where, int err)
{
	fprintf(stderr, "Error at '%s': %s\n", where, fdt_strerror(err));
}

/* Supported ANSI colours */
enum {
	COL_BLACK,
	COL_RED,
	COL_GREEN,
	COL_YELLOW,
	COL_BLUE,
	COL_MAGENTA,
	COL_CYAN,
	COL_WHITE,

	COL_NONE = -1,
};

/**
 * print_ansi_colour() - Print out the ANSI sequence for a colour
 *
 * @fout:	Output file
 * @col:	Colour to output (COL_...), or COL_NONE to reset colour
 */
static void print_ansi_colour(FILE *fout, int col)
{
	if (col == COL_NONE)
		fprintf(fout, "\033[0m");
	else
		fprintf(fout, "\033[1;%dm", col + 30);
}


/**
 * value_add() - Add a new value to our list of things to grep for
 *
 * @disp:	Display structure, holding info about our options
 * @headp:	Pointer to header pointer of list
 * @type:	Type of this value (FDT_IS_...)
 * @include:	1 if we want to include matches, 0 to exclude
 * @str:	String value to match
 */
static int value_add(struct display_info *disp, struct value_node **headp,
		     int type, int include, const char *str)
{
	struct value_node *node;

	/*
	 * Keep track of which types we are excluding/including. We don't
	 * allow both including and excluding things, because it doesn't make
	 * sense. 'Including' means that everything not mentioned is
	 * excluded. 'Excluding' means that everything not mentioned is
	 * included. So using the two together would be meaningless.
	 */
	if (include)
		disp->types_inc |= type;
	else
		disp->types_exc |= type;
	if (disp->types_inc & disp->types_exc & type) {
		fprintf(stderr, "Cannot use both include and exclude"
			" for '%s'\n", str);
		return -1;
	}

	str = strdup(str);
	node = malloc(sizeof(*node));
	if (!str || !node) {
		fprintf(stderr, "Out of memory\n");
		return -1;
	}
	node->next = *headp;
	node->type = type;
	node->include = include;
	node->string = str;
	*headp = node;

	return 0;
}

/**
 * display_fdt_by_regions() - Display regions of an FDT source
 *
 * This dumps an FDT as source, but only certain regions of it. This is the
 * final stage of the grep - we have a list of regions we want to display,
 * and this function displays them.
 *
 * @disp:	Display structure, holding info about our options
 * @blob:	FDT blob to display
 * @region:	List of regions to display
 * @count:	Number of regions
 */
static int display_fdt_by_regions(struct display_info *disp, const void *blob,
		struct fdt_region region[], int count)
{
	struct fdt_region *reg = region, *reg_end = region + count;
	uint32_t off_mem_rsvmap = fdt_off_mem_rsvmap(blob);
	int base = fdt_off_dt_struct(blob);
	int version = fdt_version(blob);
	int offset, nextoffset;
	int tag, depth, shift;
	FILE *f = disp->fout;
	uint64_t addr, size;
	int in_region;
	int file_ofs;
	int i;

	if (disp->show_dts_version)
		fprintf(f, "/dts-v1/;\n");

	if (disp->header) {
		fprintf(f, "// magic:\t\t0x%x\n", fdt_magic(blob));
		fprintf(f, "// totalsize:\t\t0x%x (%d)\n", fdt_totalsize(blob),
		       fdt_totalsize(blob));
		fprintf(f, "// off_dt_struct:\t0x%x\n",
			fdt_off_dt_struct(blob));
		fprintf(f, "// off_dt_strings:\t0x%x\n",
			fdt_off_dt_strings(blob));
		fprintf(f, "// off_mem_rsvmap:\t0x%x\n", off_mem_rsvmap);
		fprintf(f, "// version:\t\t%d\n", version);
		fprintf(f, "// last_comp_version:\t%d\n",
		       fdt_last_comp_version(blob));
		if (version >= 2) {
			fprintf(f, "// boot_cpuid_phys:\t0x%x\n",
			       fdt_boot_cpuid_phys(blob));
		}
		if (version >= 3) {
			fprintf(f, "// size_dt_strings:\t0x%x\n",
			       fdt_size_dt_strings(blob));
		}
		if (version >= 17) {
			fprintf(f, "// size_dt_struct:\t0x%x\n",
			       fdt_size_dt_struct(blob));
		}
		fprintf(f, "\n");
	}

	if (disp->flags & FDT_REG_ADD_MEM_RSVMAP) {
		const struct fdt_reserve_entry *p_rsvmap;

		p_rsvmap = (const struct fdt_reserve_entry *)
				((const char *)blob + off_mem_rsvmap);
		for (i = 0; ; i++) {
			addr = fdt64_to_cpu(p_rsvmap[i].address);
			size = fdt64_to_cpu(p_rsvmap[i].size);
			if (addr == 0 && size == 0)
				break;

			fprintf(f, "/memreserve/ %llx %llx;\n",
			(unsigned long long)addr, (unsigned long long)size);
		}
	}

	depth = nextoffset = 0;
	shift = 4;	/* 4 spaces per indent */
	do {
		const struct fdt_property *prop;
		const char *name;
		int show;
		int len;

		offset = nextoffset;

		/*
		 * Work out the file offset of this offset, and decide
		 * whether it is in the region list or not
		 */
		file_ofs = base + offset;
		if (reg < reg_end && file_ofs >= reg->offset + reg->size)
			reg++;
		in_region = reg < reg_end && file_ofs >= reg->offset &&
				file_ofs < reg->offset + reg->size;
		tag = fdt_next_tag(blob, offset, &nextoffset);

		if (tag == FDT_END)
			break;
		show = in_region || disp->all;
		if (show && disp->diff)
			fprintf(f, "%c", in_region ? '+' : '-');

		if (!show) {
			/* Do this here to avoid 'if (show)' in every 'case' */
			if (tag == FDT_BEGIN_NODE)
				depth++;
			else if (tag == FDT_END_NODE)
				depth--;
			continue;
		}
		if (tag != FDT_END) {
			if (disp->show_addr)
				fprintf(f, "%4x: ", file_ofs);
			if (disp->show_offset)
				fprintf(f, "%4x: ", file_ofs - base);
		}

		/* Green means included, red means excluded */
		if (disp->colour)
			print_ansi_colour(f, in_region ? COL_GREEN : COL_RED);

		switch (tag) {
		case FDT_PROP:
			prop = fdt_get_property_by_offset(blob, offset, NULL);
			name = fdt_string(blob, fdt32_to_cpu(prop->nameoff));
			fprintf(f, "%*s%s", depth * shift, "", name);
			utilfdt_print_data(prop->data,
					   fdt32_to_cpu(prop->len));
			fprintf(f, ";");
			break;

		case FDT_NOP:
			fprintf(f, "%*s// [NOP]", depth * shift, "");
			break;

		case FDT_BEGIN_NODE:
			name = fdt_get_name(blob, offset, &len);
			fprintf(f, "%*s%s {", depth++ * shift, "",
			       *name ? name : "/");
			break;

		case FDT_END_NODE:
			fprintf(f, "%*s};", --depth * shift, "");
			break;
		}

		/* Reset colour back to normal before end of line */
		if (disp->colour)
			print_ansi_colour(f, COL_NONE);
		fprintf(f, "\n");
	} while (1);

	/* Print a list of strings if requested */
	if (disp->list_strings) {
		const char *str;
		int offset;
		int str_base = fdt_off_dt_strings(blob);

		for (offset = 0; offset < fdt_size_dt_strings(blob);
				offset += strlen(str) + 1) {
			str = fdt_string(blob, offset);
			int len = strlen(str) + 1;
			int show;

			/* Only print strings that are in the region */
			file_ofs = str_base + offset;
			in_region = reg < reg_end &&
					file_ofs >= reg->offset &&
					file_ofs + len < reg->offset +
						reg->size;
			show = in_region || disp->all;
			if (show && disp->diff)
				printf("%c", in_region ? '+' : '-');
			if (disp->show_addr)
				printf("%4x: ", file_ofs);
			if (disp->show_offset)
				printf("%4x: ", offset);
			printf("%s\n", str);
		}
	}

	return 0;
}

/**
 * dump_fdt_regions() - Dump regions of an FDT as binary data
 *
 * This dumps an FDT as binary, but only certain regions of it. This is the
 * final stage of the grep - we have a list of regions we want to dump,
 * and this function dumps them.
 *
 * The output of this function may or may not be a valid FDT. To ensure it
 * is, these disp->flags must be set:
 *
 *   FDT_REG_SUPERNODES: ensures that subnodes are preceeded by their
 *		parents. Without this option, fragments of subnode data may be
 *		output without the supernodes above them. This is useful for
 *		hashing but cannot produce a valid FDT.
 *   FDT_REG_ADD_STRING_TAB: Adds a string table to the end of the FDT.
 *		Without this none of the properties will have names
 *   FDT_REG_ADD_MEM_RSVMAP: Adds a mem_rsvmap table - an FDT is invalid
 *		without this.
 *
 * @disp:	Display structure, holding info about our options
 * @blob:	FDT blob to display
 * @region:	List of regions to display
 * @count:	Number of regions
 */
static int dump_fdt_regions(struct display_info *disp, const void *blob,
		struct fdt_region region[], int count)
{
	struct fdt_header hdr, *fdt = &hdr;
	int size, struct_start;
	FILE *f = disp->fout;
	int i;

	/* Set up a basic header (even if we don't actually write it) */
	memset(fdt, '\0', sizeof(*fdt));
	fdt_set_magic(fdt, FDT_MAGIC);
	struct_start = FDT_ALIGN(sizeof(struct fdt_header),
					sizeof(struct fdt_reserve_entry));
	fdt_set_off_mem_rsvmap(fdt, struct_start);
	fdt_set_version(fdt, FDT_LAST_SUPPORTED_VERSION);
	fdt_set_last_comp_version(fdt, FDT_FIRST_SUPPORTED_VERSION);

	/*
	 * Calculate the total size of the regions we are writing out. The
	 * first will be the mem_rsvmap if the FDT_REG_ADD_MEM_RSVMAP flag
	 * is set. The last will be the string table if FDT_REG_ADD_STRING_TAB
	 * is set.
	 */
	for (i = size = 0; i < count; i++)
		size += region[i].size;

	/* Bring in the mem_rsvmap section from the old file if requested */
	if (count > 0 && (disp->flags & FDT_REG_ADD_MEM_RSVMAP)) {
		struct_start += region[0].size;
		size -= region[0].size;
	}
	fdt_set_off_dt_struct(fdt, struct_start);

	/* Update the header to have the correct offsets/sizes */
	if (count >= 2 && (disp->flags & FDT_REG_ADD_STRING_TAB)) {
		int str_size;

		str_size = region[count - 1].size;
		fdt_set_size_dt_struct(fdt, size - str_size);
		fdt_set_off_dt_strings(fdt, struct_start + size - str_size);
		fdt_set_size_dt_strings(fdt, str_size);
		fdt_set_totalsize(fdt, struct_start + size);
	}

	/* Write the header if required */
	if (disp->header) {
		if (sizeof(hdr) != fwrite(fdt, 1, sizeof(hdr), f))
			return -1;
		for (i = sizeof(hdr); i < fdt_off_mem_rsvmap(&hdr); i++)
			fputc('\0', f);
	}

	/* Output all the nodes including any mem_rsvmap/string table */
	for (i = size = 0; i < count; i++) {
		struct fdt_region *reg = &region[i];

		if (reg->size != fwrite((const char *)blob + reg->offset, 1,
				reg->size, f))
			return -1;
		size += reg->size;
	}

	return 0;
}

/**
 * show_region_list() - Print out a list of regions
 *
 * The list includes the region offset (absolute offset from start of FDT
 * blob in bytes) and size
 *
 * @reg:	List of regions to print
 * @count:	Number of regions
 */
static void show_region_list(struct fdt_region *reg, int count)
{
	int i;

	printf("Regions: %d\n", count);
	for (i = 0; i < count; i++, reg++) {
		printf("%d:  %-10x  %-10x\n", i, reg->offset,
		       reg->offset + reg->size);
	}
}

static int check_type_include(void *priv, int type, const char *data, int size)
{
	struct display_info *disp = priv;
	struct value_node *val;
	int match, none_match = FDT_IS_ANY;

	/* If none of our conditions mention this type, we know nothing */
	debug("type=%x, data=%s\n", type, data);
	if (!((disp->types_inc | disp->types_exc) & type)) {
		debug("   - not in any condition\n");
		return -1;
	}

	/*
	 * Go through the list of conditions. For inclusive conditions, we
	 * return 1 at the first match. For exclusive conditions, we must
	 * check that there are no matches.
	 */
	for (val = disp->value_head; val; val = val->next) {
		if (!(type & val->type))
			continue;
		match = fdt_stringlist_contains(data, size, val->string);
		debug("      - val->type=%x, str='%s', match=%d\n",
		      val->type, val->string, match);
		if (match && val->include) {
			debug("   - match inc %s\n", val->string);
			return 1;
		}
		if (match)
			none_match &= ~val->type;
	}

	/*
	 * If this is an exclusive condition, and nothing matches, then we
	 * should return 1.
	 */
	if ((type & disp->types_exc) && (none_match & type)) {
		debug("   - match exc\n");
		/*
		 * Allow FDT_IS_COMPAT to make the final decision in the
		 * case where there is no specific type
		 */
		if (type == FDT_IS_NODE && disp->types_exc == FDT_IS_ANY) {
			debug("   - supressed exc node\n");
			return -1;
		}
		return 1;
	}

	/*
	 * Allow FDT_IS_COMPAT to make the final decision in the
	 * case where there is no specific type (inclusive)
	 */
	if (type == FDT_IS_NODE && disp->types_inc == FDT_IS_ANY)
		return -1;

	debug("   - no match, types_inc=%x, types_exc=%x, none_match=%x\n",
	      disp->types_inc, disp->types_exc, none_match);

	return 0;
}

/**
 * h_include() - Include handler function for fdt_find_regions()
 *
 * This function decides whether to include or exclude a node, property or
 * compatible string. The function is defined by fdt_find_regions().
 *
 * The algorithm is documented in the code - disp->invert is 0 for normal
 * operation, and 1 to invert the sense of all matches.
 *
 * See
 */
static int h_include(void *priv, const void *fdt, int offset, int type,
		     const char *data, int size)
{
	struct display_info *disp = priv;
	int inc, len;

	inc = check_type_include(priv, type, data, size);

	/*
	 * If the node name does not tell us anything, check the
	 * compatible string
	 */
	if (inc == -1 && type == FDT_IS_NODE) {
		debug("   - checking compatible2\n");
		data = fdt_getprop(fdt, offset, "compatible", &len);
		inc = check_type_include(priv, FDT_IS_COMPAT, data, len);
	}

	switch (inc) {
	case 1:
		inc = !disp->invert;
		break;
	case 0:
		inc = disp->invert;
		break;
	}
	debug("   - returning %d\n", inc);

	return inc;
}

static int fdt_find_regions(const void *fdt,
		int (*h_include)(void *priv, const void *fdt, int offset,
				 int type, const char *data, int size),
		struct display_info *disp, struct fdt_region *region,
		int max_regions, char *path, int path_len, int flags)
{
	struct fdt_region_state state;
	int count;
	int ret;

	count = 0;
	ret = fdt_first_region(fdt, h_include, disp,
			&region[count++], path, path_len,
			disp->flags, &state);
	while (ret == 0) {
		ret = fdt_next_region(fdt, h_include, disp,
				&region[count], path, path_len,
				disp->flags, &state);
		if (!ret)
			count++;
	}

	if (ret != -FDT_ERR_NOTFOUND)
		return ret;

	return count;
}

/**
 * Run the main fdtgrep operation, given a filename and valid arguments
 *
 * @param disp		Display information / options
 * @param filename	Filename of blob file
 * @param return 0 if ok, -ve on error
 */
static int do_fdtgrep(struct display_info *disp, const char *filename)
{
	struct fdt_region *region;
	int max_regions;
	int count = 100;
	char path[1024];
	char *blob;
	int i, ret;

	blob = utilfdt_read(filename);
	if (!blob)
		return -1;
	ret = fdt_check_header(blob);
	if (ret) {
		fprintf(stderr, "Error: %s\n", fdt_strerror(ret));
		return ret;
	}

	/* Allow old files, but they are untested */
	if (fdt_version(blob) < 17 && disp->value_head) {
		fprintf(stderr, "Warning: fdtgrep does not fully support"
			" version %d files\n", fdt_version(blob));
	}

	/*
	 * We do two passes, since we don't know how many regions we need.
	 * The first pass will count the regions, but if it is too many,
	 * we do another pass to actually record them.
	 */
	for (i = 0; i < 2; i++) {
		region = malloc(count * sizeof(struct fdt_region));
		if (!region) {
			fprintf(stderr, "Out of memory for %d regions\n",
				count);
			return -1;
		}
		max_regions = count;
		count = fdt_find_regions(blob,
				h_include, disp,
				region, max_regions, path, sizeof(path),
				disp->flags);
		if (count < 0) {
			report_error("fdt_find_regions", count);
			return -1;
		}
		if (count <= max_regions)
			break;
		free(region);
	}

	/* Optionally print a list of regions */
	if (disp->region_list)
		show_region_list(region, count);

	/* Output either source .dts or binary .dtb */
	if (disp->output == OUT_DTS) {
		ret = display_fdt_by_regions(disp, blob, region, count);
	} else {
		ret = dump_fdt_regions(disp, blob, region, count);
		if (ret)
			fprintf(stderr, "Write failure\n");
	}

	free(blob);
	free(region);

	return ret;
}

static const char usage_synopsis[] =
	"fdtgrep - extract portions from device tree\n"
	"\n"
	"Usage:\n"
	"	fdtgrep <options> <dt file>|-\n\n"
	"Output formats are:\n"
	"\tdts - device tree soure text\n"
	"\tdtb - device tree blob (sets -Hmt automatically)\n"
	"\tbin - device tree fragment (may not be a valid .dtb)";

static const char usage_short_opts[] = "haAc:C:defg:G:HIlLmn:N:o:O:p:P:sStv"
			USAGE_COMMON_SHORT_OPTS;
static struct option const usage_long_opts[] = {
	{"show-address",	no_argument, NULL, 'a'},
	{"colour",		no_argument, NULL, 'A'},
	{"include-compat",	a_argument, NULL, 'c'},
	{"exclude-compat",	a_argument, NULL, 'C'},
	{"diff",		no_argument, NULL, 'd'},
	{"enter-node",		no_argument, NULL, 'e'},
	{"show-offset",		no_argument, NULL, 'f'},
	{"include-match",	a_argument, NULL, 'g'},
	{"exclude-match",	a_argument, NULL, 'G'},
	{"show-header",		no_argument, NULL, 'H'},
	{"show-version",	no_argument, NULL, 'I'},
	{"list-regions",	no_argument, NULL, 'l'},
	{"list-strings",	no_argument, NULL, 'L'},
	{"include-mem",		no_argument, NULL, 'm'},
	{"include-node",	a_argument, NULL, 'n'},
	{"exclude-node",	a_argument, NULL, 'N'},
	{"include-prop",	a_argument, NULL, 'p'},
	{"exclude-prop",	a_argument, NULL, 'P'},
	{"show-subnodes",	no_argument, NULL, 's'},
	{"skip-supernodes",	no_argument, NULL, 'S'},
	{"show-stringtab",	no_argument, NULL, 't'},
	{"out",			a_argument, NULL, 'o'},
	{"out-format",		a_argument, NULL, 'O'},
	{"invert-match",	no_argument, NULL, 'v'},
	USAGE_COMMON_LONG_OPTS,
};
static const char * const usage_opts_help[] = {
	"Display address",
	"Show all nodes/tags, colour those that match",
	"Compatible nodes to include in grep",
	"Compatible nodes to exclude in grep",
	"Diff: Mark matching nodes with +, others with -",
	"Enter direct subnode names of matching nodes",
	"Display offset",
	"Node/property/compatible string to include in grep",
	"Node/property/compatible string to exclude in grep",
	"Output a header",
	"Put \"/dts-v1/;\" on first line of dts output",
	"Output a region list",
	"List strings in string table",
	"Include mem_rsvmap section in binary output",
	"Node to include in grep",
	"Node to exclude in grep",
	"Property to include in grep",
	"Property to exclude in grep",
	"Show all subnodes matching nodes",
	"Don't include supernodes of matching nodes",
	"Include string table in binary output",
	"-o <output file>",
	"-O <output format>",
	"Invert the sense of matching (select non-matching lines)",
	USAGE_COMMON_OPTS_HELP
};

static void scan_args(struct display_info *disp, int argc, char *argv[])
{
	int opt;

	while ((opt = util_getopt_long()) != EOF) {
		int type = 0;
		int inc = 1;

		switch (opt) {
		case_USAGE_COMMON_FLAGS
		case 'a':
			disp->show_addr = 1;
			break;
		case 'A':
			disp->all = 1;
			break;
		case 'C':
			inc = 0;
			/* no break */
		case 'c':
			type = FDT_IS_COMPAT;
			break;
		case 'd':
			disp->diff = 1;
			break;
		case 'e':
			disp->flags |= FDT_REG_DIRECT_SUBNODES;
			break;
		case 'f':
			disp->show_offset = 1;
			break;
		case 'G':
			inc = 0;
			/* no break */
		case 'g':
			type = FDT_IS_ANY;
			break;
		case 'H':
			disp->header = 1;
			break;
		case 'l':
			disp->region_list = 1;
			break;
		case 'L':
			disp->list_strings = 1;
			break;
		case 'm':
			disp->flags |= FDT_REG_ADD_MEM_RSVMAP;
			break;
		case 'N':
			inc = 0;
			/* no break */
		case 'n':
			type = FDT_IS_NODE;
			break;
		case 'o':
			disp->output_fname = optarg;
			break;
		case 'O':
			if (!strcmp(optarg, "dtb"))
				disp->output = OUT_DTB;
			else if (!strcmp(optarg, "dts"))
				disp->output = OUT_DTS;
			else if (!strcmp(optarg, "bin"))
				disp->output = OUT_BIN;
			else
				usage("Unknown output format");
			break;
		case 'P':
			inc = 0;
			/* no break */
		case 'p':
			type = FDT_IS_PROP;
			break;
		case 's':
			disp->flags |= FDT_REG_ALL_SUBNODES;
			break;
		case 'S':
			disp->flags &= ~FDT_REG_SUPERNODES;
			break;
		case 't':
			disp->flags |= FDT_REG_ADD_STRING_TAB;
			break;
		case 'v':
			disp->invert = 1;
			break;
		case 'I':
			disp->show_dts_version = 1;
			break;
		}

		if (type && value_add(disp, &disp->value_head, type, inc,
					optarg))
			usage("Cannot add value");
	}

	if (disp->invert && disp->types_exc)
		usage("-v has no meaning when used with 'exclude' conditions");
}

int main(int argc, char *argv[])
{
	char *filename = NULL;
	struct display_info disp;
	int ret;

	/* set defaults */
	memset(&disp, '\0', sizeof(disp));
	disp.flags = FDT_REG_SUPERNODES;	/* Default flags */

	scan_args(&disp, argc, argv);

	/* Show matched lines in colour if we can */
	disp.colour = disp.all && isatty(0);

	/* Any additional arguments can match anything, just like -g */
	while (optind < argc - 1) {
		if (value_add(&disp, &disp.value_head, FDT_IS_ANY, 1,
				argv[optind++]))
			usage("Cannot add value");
	}

	if (optind < argc)
		filename = argv[optind++];
	if (!filename)
		usage("Missing filename");

	/* If a valid .dtb is required, set flags to ensure we get one */
	if (disp.output == OUT_DTB) {
		disp.header = 1;
		disp.flags |= FDT_REG_ADD_MEM_RSVMAP | FDT_REG_ADD_STRING_TAB;
	}

	if (disp.output_fname) {
		disp.fout = fopen(disp.output_fname, "w");
		if (!disp.fout)
			usage("Cannot open output file");
	} else {
		disp.fout = stdout;
	}

	/* Run the grep and output the results */
	ret = do_fdtgrep(&disp, filename);
	if (disp.output_fname)
		fclose(disp.fout);
	if (ret)
		return 1;

	return 0;
}
