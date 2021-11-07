// SPDX-License-Identifier: GPL-2.0-or-later
/*
 * Tool to selection regions of a devicetree blob
 *
 * Copyright 2021 Google LLC
 * Written by Simon Glass <sjg@chromium.org>
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
	OUT_BIN,		/* Fragment of .dtb, for hashing */
};

/* Holds information which controls our output and options */
struct display_info {
	enum output_t output;	/* Output format */
	int flags;		/* Flags (FDT_REG_...) */
	int types_inc;		/* Mask of types that we include (FDT_IS...) */
	int types_exc;		/* Mask of types that we exclude (FDT_IS...) */
	struct value_node *value_head;	/* List of values to match */
	const char *output_fname;	/* Output filename */
	FILE *fout;		/* File to write dts/dtb output */
};

static void report_error(const char *where, int err)
{
	fprintf(stderr, "Error at '%s': %s\n", where, fdt_strerror(err));
}

/**
 * value_add() - Add a new value to our list of things to grep for
 *
 * @disp:	Display structure, holding info about our options
 * @headp:	Pointer to header pointer of list
 * @type:	Type of this value (FDT_IS_...)
 * @include:	1 if we want to include matches, 0 to exclude
 * @str:	String value to match
 * @return 0 on success, -1 if out of memory
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
		fprintf(stderr, "Cannot use both include and exclude for '%s'\n",
			str);
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
static void display_fdt_by_regions(struct display_info *disp, const void *blob,
				   struct fdt_region region[], int count)
{
	struct fdt_region *reg = region, *reg_end = region + count;
	int base = fdt_off_dt_struct(blob);
	int nextoffset;
	int tag, depth, shift;
	FILE *f = disp->fout;
	int in_region;
	int file_ofs;

	depth = nextoffset = 0;
	shift = 4;	/* 4 spaces per indent */
	do {
		const struct fdt_property *prop;
		const char *name;
		int offset;
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
		show = in_region;

		if (!show) {
			/* Do this here to avoid 'if (show)' in every 'case' */
			if (tag == FDT_BEGIN_NODE)
				depth++;
			else if (tag == FDT_END_NODE)
				depth--;
			continue;
		}

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
		fprintf(f, "\n");
	} while (1);
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
 *   FDT_REG_SUPERNODES: ensures that subnodes are preceded by their
 *		parents. Without this option, fragments of subnode data may be
 *		output without the supernodes above them, which is useful for
 *		hashing but cannot produce a valid FDT.
 *
 * @disp:	Display structure, holding info about our options
 * @blob:	FDT blob to display
 * @region:	List of regions to display
 * @count:	Number of regions
 * @out:	Output destination
 * @return number of bytes dumped
 */
static int dump_fdt_regions(struct display_info *disp, const void *blob,
		struct fdt_region region[], int count, char *out)
{
	unsigned int ptr;
	int i;

	/* Output all the nodes */
	ptr = 0;
	for (i = 0; i < count; i++) {
		struct fdt_region *reg = &region[i];

		memcpy(out + ptr, (const char *)blob + reg->offset, reg->size);
		ptr += reg->size;
	}

	return ptr;
}

/**
 * check_type_include() - Check whether to include this type
 *
 * See fdt_region_func for its purpose
 *
 * @disp:	Display structure, holding info about our options
 * @type: Type of this part, FDT_IS_...
 * @data: Pointer to data (full path of node / property name)
 * @size: Size of data (length of node path / property including \0 terminator
 * @return 0 to exclude, 1 to include, -1 if no information is available
 */
static int check_type_include(struct display_info *disp, int type,
			      const char *data, int size)
{
	struct value_node *val;
	int match, none_match = FDT_IS_ANY;

	/* If none of our conditions mention this type, we know nothing */
	debug("type=%x, data=%s\n", type, data ? data : "(null)");
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
		return 1;
	}

	debug("   - no match, types_inc=%x, types_exc=%x, none_match=%x\n",
	      disp->types_inc, disp->types_exc, none_match);

	return 0;
}

/**
 * h_include() - Include handler function for fdt_find_regions()
 *
 * This function decides whether to include or exclude a node, property or
 * compatible string. The function is called by fdt_find_regions().
 *
 * The algorithm is documented in check_type_include(). See fdt_region_func for
 * parameters
 *
 * @priv: Pointer to our struct display_info
 */
static int h_include(void *priv, const void *fdt, int offset, int type,
		     const char *data, int size)
{
	struct display_info *disp = priv;
	int inc;

	inc = check_type_include(disp, type, data, size);

	debug("   - returning %d\n", inc);

	return inc;
}

/**
 * fdt_find_regions() - Find regiions in a devicetree
 *
 * Calls fdt_first_region() and then fdt_next_region() repeatedly until all
 * regions are found.
 *
 * This mirrors fdt_first_region() except that @region is an array of regions
 * of size @max_regions
 *
 * @return number of regions found. If this is more than @max_regions then
 * only @max_regions regions are returned. The @region array should be expanded
 * and this function called again.
 */
static int fdt_find_regions(const void *fdt,
		int (*include_func)(void *priv, const void *fdt, int offset,
				 int type, const char *data, int size),
		struct display_info *disp, struct fdt_region *region,
		int max_regions, char *path, int path_len, int flags)
{
	struct fdt_region_state state;
	int count;
	int ret;

	count = 0;
	ret = fdt_first_region(fdt, include_func, disp, &region[count++], path,
			       path_len, disp->flags, &state);
	while (!ret) {
		ret = fdt_next_region(fdt, include_func, disp,
				      count < max_regions ? &region[count] :
				      NULL, &state);
		if (!ret)
			count++;
	}

	/* If there are no more regions, stop */
	if (ret != -FDT_ERR_NOTFOUND)
		return ret;

	return count;
}

/**
 * Run the main fdtgrep operation, given a filename and valid arguments
 *
 * @disp		Display information / options
 * @filename	Filename of blob file
 * @return 0 if ok, -ve on error
 */
static int do_fdtgrep(struct display_info *disp, const char *filename)
{
	struct fdt_region *region;
	int max_regions;
	int count = 100;
	char path[1024];
	char *blob;
	int i, ret;

	blob = utilfdt_read(filename, NULL);
	if (!blob)
		return -1;
	ret = fdt_check_header(blob);
	if (ret) {
		fprintf(stderr, "Error: %s\n", fdt_strerror(ret));
		return ret;
	}

	/* Allow old files, but they are untested */
	if (fdt_version(blob) < 17 && disp->value_head) {
		fprintf(stderr, "Warning: fdtgrep does not fully support version %d files\n",
			fdt_version(blob));
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

	/* Output either source .dts or binary .dtb */
	if (disp->output == OUT_DTS) {
		display_fdt_by_regions(disp, blob, region, count);
	} else {
		void *fdt;
		/* Allow reserved memory section to expand slightly */
		unsigned int size = fdt_totalsize(blob) + 16;

		fdt = malloc(size);
		if (!fdt) {
			fprintf(stderr, "Out_of_memory\n");
			ret = -1;
			goto err;
		}
		size = dump_fdt_regions(disp, blob, region, count, fdt);

		if (size != fwrite(fdt, 1, size, disp->fout)) {
			fprintf(stderr, "Write failure, %d bytes\n", size);
			free(fdt);
			ret = 1;
			goto err;
		}
		free(fdt);
	}
err:
	free(blob);
	free(region);

	return ret;
}

static const char usage_synopsis[] =
	"fdtgrep - extract portions from device tree binary\n"
	"\n"
	"Usage:\n"
	"	fdtgrep <options> <dt file>|-\n\n"
	"Output formats are:\n"
	"\tdts - device tree soure text\n"
	"\tbin - device tree fragment (may not be a valid .dtb)";

static const char usage_short_opts[] =
		"hn:N:o:O:p:P:"
		USAGE_COMMON_SHORT_OPTS;
static const struct option usage_long_opts[] = {
	{"include-node",	a_argument, NULL, 'n'},
	{"exclude-node",	a_argument, NULL, 'N'},
	{"include-prop",	a_argument, NULL, 'p'},
	{"exclude-prop",	a_argument, NULL, 'P'},
	{"out",			a_argument, NULL, 'o'},
	{"out-format",		a_argument, NULL, 'O'},
	USAGE_COMMON_LONG_OPTS,
};
static const char * const usage_opts_help[] = {
	"Node to include in grep",
	"Node to exclude in grep",
	"Property to include in grep",
	"Property to exclude in grep",
	"-o <output file>",
	"-O <output format>",
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
			if (!strcmp(optarg, "dts"))
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
		}

		if (type && value_add(disp, &disp->value_head, type, inc,
					optarg))
			usage("Cannot add value");
	}
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
