// SPDX-License-Identifier: (GPL-2.0-or-later OR BSD-2-Clause)
/*
 * Selection of regions of a devicetree blob
 *
 * Copyright 2021 Google LLC
 * Written by Simon Glass <sjg@chromium.org>
 */

#include "libfdt_env.h"

#include <fdt.h>
#include <libfdt.h>

#include "libfdt_internal.h"

/**
 * fdt_add_region() - Add a new region to our list
 *
 * The region is added if there is space, but in any case we increment the
 * count. If permitted, and the new region overlaps the last one, we merge
 * them.
 *
 * @info: State information
 * @offset: Start offset of region
 * @size: Size of region
 */
static int fdt_add_region(struct fdt_region_state *info, int offset, int size)
{
	struct fdt_region *reg;

	reg = info->region ? &info->region[info->count - 1] : NULL;
	if (info->can_merge && info->count &&
			info->count <= info->max_regions &&
			reg && offset <= reg->offset + reg->size) {
		reg->size = offset + size - reg->offset;
	} else if (info->count++ < info->max_regions) {
		if (reg) {
			reg++;
			reg->offset = offset;
			reg->size = size;
		}
	} else {
		return -1;
	}

	return 0;
}

/**
 * fdt_include_supernodes() - Include supernodes required by this node
 *
 * When we decided to include a node or property which is not at the top
 * level, this function forces the inclusion of higher level nodes. For
 * example, given this tree:
 *
 * / {
 *     testing {
 *     }
 * }
 *
 * If we decide to include testing then we need the root node to have a valid
 * tree. This function adds those regions.
 *
 * @info: State information
 * @depth: Current stack depth
 */
static int fdt_include_supernodes(struct fdt_region_state *info, int depth)
{
	int base = fdt_off_dt_struct(info->fdt);
	int start, stop_at;
	int i;

	/*
	 * Work down the stack looking for supernodes that we didn't include.
	 * The algortihm here is actually pretty simple, since we know that
	 * no previous subnode had to include these nodes, or if it did, we
	 * marked them as included (on the stack) already.
	 */
	for (i = 0; i <= depth; i++) {
		if (!info->stack[i].included) {
			start = info->stack[i].offset;

			/* Add the FDT_BEGIN_NODE tag of this supernode */
			fdt_next_tag(info->fdt, start, &stop_at);
			if (fdt_add_region(info, base + start,
					stop_at - start))
				return -1;

			/* Remember that this supernode is now included */
			info->stack[i].included = 1;
			info->can_merge = 1;
		}

		/* Force (later) generation of the FDT_END_NODE tag */
		if (!info->stack[i].want)
			info->stack[i].want = WANT_NODES_ONLY;
	}

	return 0;
}

int fdt_first_region(const void *fdt, fdt_region_func h_include, void *priv,
		     struct fdt_region *region, char *path, int path_len,
		     int flags, struct fdt_region_state *info)
{
	struct fdt_region_ptrs *p = &info->ptrs;

	/* Set up our state */
	info->fdt = fdt;
	info->can_merge = 1;
	info->max_regions = 1;
	info->start = -1;
	info->path = path;
	info->path_len = path_len;
	info->flags = flags;
	p->want = WANT_NOTHING;
	p->end = path;
	*p->end = '\0';
	p->nextoffset = 0;
	p->depth = -1;
	p->done = FDT_DONE_NOTHING;

	return fdt_next_region(fdt, h_include, priv, region, info);
}

/*
 * Theory of operation
 *
 * Note: in this description 'included' means that a node (or other part of
 * the tree) should be included in the region list, i.e. it will have a region
 * which covers its part of the tree.
 *
 * This function maintains some state from the last time it is called. It
 * checks the next part of the tree that it is supposed to look at
 * (p.nextoffset) to see if that should be included or not. When it finds
 * something to include, it sets info->start to its offset. This marks the
 * start of the region we want to include.
 *
 * Once info->start is set to the start (i.e. not -1), we continue scanning
 * until we find something that we don't want included. This will be the end
 * of a region. At this point we can close off the region and add it to the
 * list. So we do so, and reset info->start to -1.
 *
 * One complication here is that we want to merge regions. So when we come to
 * add another region later, we may in fact merge it with the previous one if
 * one ends where the other starts.
 *
 * The function fdt_add_region() will return -1 if it fails to add the region,
 * because we already have a region ready to be returned, and the new one
 * cannot be merged in with it. In this case, we must return the region we
 * found, and wait for another call to this function. When it comes, we will
 * repeat the processing of the tag and again try to add a region. This time it
 * will succeed.
 *
 * The current state of the pointers (stack, offset, etc.) is maintained in
 * a ptrs member. At the start of every loop iteration we make a copy of it.
 * The copy is then updated as the tag is processed. Only if we get to the end
 * of the loop iteration (and successfully call fdt_add_region() if we need
 * to) can we commit the changes we have made to these pointers. For example,
 * if we see an FDT_END_NODE tag we will decrement the depth value. But if we
 * need to add a region for this tag (let's say because the previous tag is
 * included and this FDT_END_NODE tag is not included) then we will only commit
 * the result if we were able to add the region. That allows us to retry again
 * next time.
 *
 * We keep track of a variable called 'want' which tells us what we want to
 * include when there is no specific information provided by the h_include()
 * function for a particular property. This basically handles the inclusion of
 * properties which are pulled in by virtue of the node they are in. So if you
 * include a node, its properties are also included. In this case 'want' will
 * be WANT_NODES_AND_PROPS.
 *
 * Using 'want' we work out 'include', which tells us whether this current tag
 * should be included or not. As you can imagine, if the value of 'include'
 * changes, that means we are on a boundary between nodes to include and nodes
 * to exclude. At this point we either close off a previous region and add it
 * to the list, or mark the start of a new region.
 *
 * Apart from the nodes, we have the FDT_END tag, mem_rsvmap and the string
 * list. The last two are not handled at present. The FDT_END tag is dealt with
 * as a whole (i.e. we create a region for it if is to be included).
 */
int fdt_next_region(const void *fdt, fdt_region_func h_include, void *priv,
		    struct fdt_region *region, struct fdt_region_state *info)
{
	int base = fdt_off_dt_struct(fdt);
	int last_node = 0;
	const char *str;

	info->region = region;
	info->count = 0;

	/*
	 * Work through the tags one by one, deciding whether each needs to
	 * be included or not. We set the variable 'include' to indicate our
	 * decision. 'want' is used to track what we want to include absent
	 * further information from the h_include() function - it allows us to
	 * pick up all the properties (and/or subnode tags) of a node that we
	 * have been asked to include.
	 */
	while (info->ptrs.done < FDT_DONE_STRUCT) {
		const struct fdt_property *prop;
		struct fdt_region_ptrs p;
		const char *name;
		int include = 0;
		int stop_at = 0;
		uint32_t tag;
		int offset;
		int val;
		int len;

		/*
		 * Make a copy of our pointers. If we make it to the end of
		 * this block then we will commit them back to info->ptrs.
		 * Otherwise we can try again from the same starting state
		 * next time we are called.
		 */
		p = info->ptrs;

		/*
		 * Find the tag, and the offset of the next one. If we need to
		 * stop including tags, then by default we stop *after*
		 * including the current tag
		 */
		offset = p.nextoffset;
		tag = fdt_next_tag(fdt, offset, &p.nextoffset);
		stop_at = p.nextoffset;

		switch (tag) {
		case FDT_PROP:
			stop_at = offset;
			prop = fdt_get_property_by_offset(fdt, offset, NULL);
			str = fdt_string(fdt, fdt32_to_cpu(prop->nameoff));
			val = h_include(priv, fdt, last_node, FDT_IS_PROP, str,
					    strlen(str) + 1);
			if (val == -1) {
				include = p.want >= WANT_NODES_AND_PROPS;
			} else {
				include = val;
				/*
				 * Make sure we include the } for this block.
				 * It might be more correct to have this done
				 * by the call to fdt_include_supernodes() in
				 * the case where it adds the node we are
				 * currently in, but this is equivalent.
				 */
				if ((info->flags & FDT_REG_SUPERNODES) && val &&
						!p.want)
					p.want = WANT_NODES_ONLY;
			}

			/* Value grepping is not yet supported */
			break;

		case FDT_NOP:
			include = p.want >= WANT_NODES_AND_PROPS;
			stop_at = offset;
			break;

		case FDT_BEGIN_NODE:
			last_node = offset;
			p.depth++;
			if (p.depth == FDT_MAX_DEPTH)
				return -FDT_ERR_TOODEEP;
			name = fdt_get_name(fdt, offset, &len);
			if (p.end - info->path + 2 + len >= info->path_len)
				return -FDT_ERR_NOSPACE;

			/* Build the full path of this node */
			if (p.end != info->path + 1)
				*p.end++ = '/';
			strcpy(p.end, name);
			p.end += len;
			info->stack[p.depth].want = p.want;
			info->stack[p.depth].offset = offset;

			/*
			 * We are not intending to include this node unless
			 * it matches, so make sure we stop *before* its tag.
			 */
			stop_at = offset;
			p.want = WANT_NOTHING;
			val = h_include(priv, fdt, offset, FDT_IS_NODE,
					info->path, p.end - info->path + 1);

			/* Include this if requested */
			if (val)
				p.want = WANT_NODES_AND_PROPS;

			/* If not requested, decay our 'p.want' value */
			else if (p.want)
				p.want--;

			/* Not including this tag, so stop now */
			else
				stop_at = offset;

			/*
			 * Decide whether to include this tag, and update our
			 * stack with the state for this node
			 */
			include = p.want;
			info->stack[p.depth].included = include;
			break;

		case FDT_END_NODE:
			include = p.want;
			if (p.depth < 0)
				return -FDT_ERR_BADSTRUCTURE;

			/*
			 * If we don't want this node, stop right away, unless
			 * we are including subnodes
			 */
			if (!p.want)
				stop_at = offset;
			p.want = info->stack[p.depth].want;
			p.depth--;
			while (p.end > info->path && *--p.end != '/')
				;
			*p.end = '\0';
			break;

		case FDT_END:
			/* We always include the end tag */
			include = 1;
			p.done = FDT_DONE_STRUCT;
			break;
		}

		/* If this tag is to be included, mark it as region start */
		if (include && info->start == -1) {
			/* Include any supernodes required by this one */
			if (info->flags & FDT_REG_SUPERNODES) {
				if (fdt_include_supernodes(info, p.depth))
					return 0;
			}
			info->start = offset;
		}

		/*
		 * If this tag is not to be included, finish up the current
		 * region.
		 */
		if (!include && info->start != -1) {
			if (fdt_add_region(info, base + info->start,
				       stop_at - info->start))
				return 0;
			info->start = -1;
			info->can_merge = 1;
		}

		/* If we have made it this far, we can commit our pointers */
		info->ptrs = p;
	}

	/* Add a region for the END tag and a separate one for string table */
	if (info->ptrs.done < FDT_DONE_END) {
		if (info->ptrs.nextoffset != (int)fdt_size_dt_struct(fdt))
			return -FDT_ERR_BADSTRUCTURE;

		if (fdt_add_region(info, base + info->start,
			       info->ptrs.nextoffset - info->start))
			return 0;
		info->ptrs.done++;
	}

	return info->count > 0 ? 0 : -FDT_ERR_NOTFOUND;
}
