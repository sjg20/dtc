#! /bin/sh

. ./tests.sh

if [ -z "$CC" ]; then
    CC=gcc
fi

export QUIET_TEST=1
STOP_ON_FAIL=0

export VALGRIND=
VGCODE=126

tot_tests=0
tot_pass=0
tot_fail=0
tot_config=0
tot_vg=0
tot_strange=0

base_run_test() {
    tot_tests=$((tot_tests + 1))
    if VALGRIND="$VALGRIND" "$@"; then
	tot_pass=$((tot_pass + 1))
    else
	ret="$?"
	if [ "$STOP_ON_FAIL" -eq 1 ]; then
	    exit 1
	fi
	if [ "$ret" -eq 1 ]; then
	    tot_config=$((tot_config + 1))
	elif [ "$ret" -eq 2 ]; then
	    tot_fail=$((tot_fail + 1))
	elif [ "$ret" -eq $VGCODE ]; then
	    tot_vg=$((tot_vg + 1))
	else
	    tot_strange=$((tot_strange + 1))
	fi
    fi
}

shorten_echo () {
    limit=32
    echo -n "$1"
    shift
    for x; do
	if [ ${#x} -le $limit ]; then
	    echo -n " $x"
	else
	    short=$(echo "$x" | head -c$limit)
	    echo -n " \"$short\"...<${#x} bytes>"
	fi
    done
}

run_test () {
    echo -n "$@:	"
    if [ -n "$VALGRIND" -a -f $1.supp ]; then
	VGSUPP="--suppressions=$1.supp"
    fi
    base_run_test $VALGRIND $VGSUPP "./$@"
}

run_sh_test () {
    echo -n "$@:	"
    base_run_test sh "$@"
}

wrap_test () {
    (
	if verbose_run "$@"; then
	    PASS
	else
	    ret="$?"
	    if [ "$ret" -gt 127 ]; then
		signame=$(kill -l $((ret - 128)))
		FAIL "Killed by SIG$signame"
	    else
		FAIL "Returned error code $ret"
	    fi
	fi
    )
}

run_wrap_test () {
    shorten_echo "$@:	"
    base_run_test wrap_test "$@"
}

wrap_error () {
    (
	if verbose_run "$@"; then
	    FAIL "Expected non-zero return code"
	else
	    ret="$?"
	    if [ "$ret" -gt 127 ]; then
		signame=$(kill -l $((ret - 128)))
		FAIL "Killed by SIG$signame"
	    else
		PASS
	    fi
	fi
    )
}

run_wrap_error_test () {
    shorten_echo "$@"
    echo -n " {!= 0}:	"
    base_run_test wrap_error "$@"
}

run_dtc_test () {
    echo -n "dtc $@:	"
    base_run_test wrap_test $VALGRIND $DTC "$@"
}

asm_to_so () {
    $CC -shared -o $1.test.so data.S $1.test.s
}

asm_to_so_test () {
    run_wrap_test asm_to_so "$@"
}

run_fdtget_test () {
    expect="$1"
    shift
    echo -n "fdtget-runtest.sh "$expect" $@:	"
    base_run_test sh fdtget-runtest.sh "$expect" "$@"
}

run_fdtput_test () {
    expect="$1"
    shift
    shorten_echo fdtput-runtest.sh "$expect" "$@"
    echo -n ":	"
    base_run_test sh fdtput-runtest.sh "$expect" "$@"
}

run_fdtdump_test() {
    file="$1"
    shorten_echo fdtdump-runtest.sh "$file"
    echo -n ":	"
    base_run_test sh fdtdump-runtest.sh "$file"
}

tree1_tests () {
    TREE=$1

    # Read-only tests
    run_test get_mem_rsv $TREE
    run_test root_node $TREE
    run_test find_property $TREE
    run_test subnode_offset $TREE
    run_test path_offset $TREE
    run_test get_name $TREE
    run_test getprop $TREE
    run_test get_phandle $TREE
    run_test get_path $TREE
    run_test supernode_atdepth_offset $TREE
    run_test parent_offset $TREE
    run_test node_offset_by_prop_value $TREE
    run_test node_offset_by_phandle $TREE
    run_test node_check_compatible $TREE
    run_test node_offset_by_compatible $TREE
    run_test notfound $TREE

    # Write-in-place tests
    run_test setprop_inplace $TREE
    run_test nop_property $TREE
    run_test nop_node $TREE
}

tree1_tests_rw () {
    TREE=$1

    # Read-write tests
    run_test set_name $TREE
    run_test setprop $TREE
    run_test del_property $TREE
    run_test del_node $TREE
}

check_tests () {
    tree="$1"
    shift
    run_sh_test dtc-checkfails.sh "$@" -- -I dts -O dtb $tree
    run_dtc_test -I dts -O dtb -o $tree.test.dtb -f $tree
    run_sh_test dtc-checkfails.sh "$@" -- -I dtb -O dtb $tree.test.dtb
}

ALL_LAYOUTS="mts mst tms tsm smt stm"

libfdt_tests () {
    tree1_tests test_tree1.dtb

    run_dtc_test -I dts -O dtb -o addresses.test.dtb addresses.dts
    run_test addr_size_cells addresses.test.dtb

    # Sequential write tests
    run_test sw_tree1
    tree1_tests sw_tree1.test.dtb
    tree1_tests unfinished_tree1.test.dtb
    run_test dtbs_equal_ordered test_tree1.dtb sw_tree1.test.dtb

    # Resizing tests
    for mode in resize realloc; do
	run_test sw_tree1 $mode
	tree1_tests sw_tree1.test.dtb
	tree1_tests unfinished_tree1.test.dtb
	run_test dtbs_equal_ordered test_tree1.dtb sw_tree1.test.dtb
    done

    # fdt_move tests
    for tree in test_tree1.dtb sw_tree1.test.dtb unfinished_tree1.test.dtb; do
	rm -f moved.$tree shunted.$tree deshunted.$tree
	run_test move_and_save $tree
	run_test dtbs_equal_ordered $tree moved.$tree
	run_test dtbs_equal_ordered $tree shunted.$tree
	run_test dtbs_equal_ordered $tree deshunted.$tree
    done

    # v16 and alternate layout tests
    for tree in test_tree1.dtb; do
	for version in 17 16; do
	    for layout in $ALL_LAYOUTS; do
		run_test mangle-layout $tree $version $layout
		tree1_tests v$version.$layout.$tree
		run_test dtbs_equal_ordered $tree v$version.$layout.$tree
	    done
	done
    done

    # Read-write tests
    for basetree in test_tree1.dtb; do
	for version in 17 16; do
	    for layout in $ALL_LAYOUTS; do
		tree=v$version.$layout.$basetree
		rm -f opened.$tree repacked.$tree
		run_test open_pack $tree
		tree1_tests opened.$tree
		tree1_tests repacked.$tree

		tree1_tests_rw $tree
		tree1_tests_rw opened.$tree
		tree1_tests_rw repacked.$tree
	    done
	done
    done
    run_test rw_tree1
    tree1_tests rw_tree1.test.dtb
    tree1_tests_rw rw_tree1.test.dtb
    run_test appendprop1
    run_test appendprop2 appendprop1.test.dtb
    run_dtc_test -I dts -O dtb -o appendprop.test.dtb appendprop.dts
    run_test dtbs_equal_ordered appendprop2.test.dtb appendprop.test.dtb

    for basetree in test_tree1.dtb sw_tree1.test.dtb rw_tree1.test.dtb; do
	run_test nopulate $basetree
	run_test dtbs_equal_ordered $basetree noppy.$basetree
	tree1_tests noppy.$basetree
	tree1_tests_rw noppy.$basetree
    done

    run_dtc_test -I dts -O dtb -o subnode_iterate.dtb subnode_iterate.dts
    run_test subnode_iterate subnode_iterate.dtb

    # Tests for behaviour on various sorts of corrupted trees
    run_test truncated_property

    # Specific bug tests
    run_test add_subnode_with_nops
    run_dtc_test -I dts -O dts -o sourceoutput.test.dts sourceoutput.dts
    run_dtc_test -I dts -O dtb -o sourceoutput.test.dtb sourceoutput.dts
    run_dtc_test -I dts -O dtb -o sourceoutput.test.dts.test.dtb sourceoutput.test.dts
    run_test dtbs_equal_ordered sourceoutput.test.dtb sourceoutput.test.dts.test.dtb

    run_dtc_test -I dts -O dtb -o embedded_nul.test.dtb embedded_nul.dts
    run_dtc_test -I dts -O dtb -o embedded_nul_equiv.test.dtb embedded_nul_equiv.dts
    run_test dtbs_equal_ordered embedded_nul.test.dtb embedded_nul_equiv.test.dtb

    run_dtc_test -I dts -O dtb bad-size-cells.dts

    # Tests for fdt_find_regions()
    for flags in $(seq 0 15); do
	run_test region_tree ${flags}
    done
}

dtc_tests () {
    run_dtc_test -I dts -O dtb -o dtc_tree1.test.dtb test_tree1.dts
    tree1_tests dtc_tree1.test.dtb
    tree1_tests_rw dtc_tree1.test.dtb
    run_test dtbs_equal_ordered dtc_tree1.test.dtb test_tree1.dtb

    run_dtc_test -I dts -O dtb -o dtc_escapes.test.dtb propname_escapes.dts
    run_test propname_escapes dtc_escapes.test.dtb

    run_dtc_test -I dts -O dtb -o line_directives.test.dtb line_directives.dts

    run_dtc_test -I dts -O dtb -o dtc_escapes.test.dtb escapes.dts
    run_test string_escapes dtc_escapes.test.dtb

    run_dtc_test -I dts -O dtb -o dtc_char_literal.test.dtb char_literal.dts
    run_test char_literal dtc_char_literal.test.dtb

    run_dtc_test -I dts -O dtb -o dtc_sized_cells.test.dtb sized_cells.dts
    run_test sized_cells dtc_sized_cells.test.dtb

    run_dtc_test -I dts -O dtb -o dtc_extra-terminating-null.test.dtb extra-terminating-null.dts
    run_test extra-terminating-null dtc_extra-terminating-null.test.dtb

    run_dtc_test -I dts -O dtb -o dtc_references.test.dtb references.dts
    run_test references dtc_references.test.dtb

    run_dtc_test -I dts -O dtb -o dtc_path-references.test.dtb path-references.dts
    run_test path-references dtc_path-references.test.dtb

    run_test phandle_format dtc_references.test.dtb both
    for f in legacy epapr both; do
	run_dtc_test -I dts -O dtb -H $f -o dtc_references.test.$f.dtb references.dts
	run_test phandle_format dtc_references.test.$f.dtb $f
    done

    run_dtc_test -I dts -O dtb -o multilabel.test.dtb multilabel.dts
    run_test references multilabel.test.dtb

    run_dtc_test -I dts -O dtb -o label_repeated.test.dtb label_repeated.dts

    run_dtc_test -I dts -O dtb -o dtc_comments.test.dtb comments.dts
    run_dtc_test -I dts -O dtb -o dtc_comments-cmp.test.dtb comments-cmp.dts
    run_test dtbs_equal_ordered dtc_comments.test.dtb dtc_comments-cmp.test.dtb

    # Check aliases support in fdt_path_offset
    run_dtc_test -I dts -O dtb -o aliases.dtb aliases.dts
    run_test get_alias aliases.dtb
    run_test path_offset_aliases aliases.dtb

    # Check /include/ directive
    run_dtc_test -I dts -O dtb -o includes.test.dtb include0.dts
    run_test dtbs_equal_ordered includes.test.dtb test_tree1.dtb

    # Check /incbin/ directive
    run_dtc_test -I dts -O dtb -o incbin.test.dtb incbin.dts
    run_test incbin incbin.test.dtb

    # Check boot_cpuid_phys handling
    run_dtc_test -I dts -O dtb -o boot_cpuid.test.dtb boot-cpuid.dts
    run_test boot-cpuid boot_cpuid.test.dtb 16

    run_dtc_test -I dts -O dtb -b 17 -o boot_cpuid_17.test.dtb boot-cpuid.dts
    run_test boot-cpuid boot_cpuid_17.test.dtb 17

    run_dtc_test -I dtb -O dtb -o preserve_boot_cpuid.test.dtb boot_cpuid.test.dtb
    run_test boot-cpuid preserve_boot_cpuid.test.dtb 16
    run_test dtbs_equal_ordered preserve_boot_cpuid.test.dtb boot_cpuid.test.dtb

    run_dtc_test -I dtb -O dtb -o preserve_boot_cpuid_17.test.dtb boot_cpuid_17.test.dtb
    run_test boot-cpuid preserve_boot_cpuid_17.test.dtb 17
    run_test dtbs_equal_ordered preserve_boot_cpuid_17.test.dtb boot_cpuid_17.test.dtb

    run_dtc_test -I dtb -O dtb -b17 -o override17_boot_cpuid.test.dtb boot_cpuid.test.dtb
    run_test boot-cpuid override17_boot_cpuid.test.dtb 17

    run_dtc_test -I dtb -O dtb -b0 -o override0_boot_cpuid_17.test.dtb boot_cpuid_17.test.dtb
    run_test boot-cpuid override0_boot_cpuid_17.test.dtb 0


    # Check -Oasm mode
    for tree in test_tree1.dts escapes.dts references.dts path-references.dts \
	comments.dts aliases.dts include0.dts incbin.dts \
	value-labels.dts ; do
	run_dtc_test -I dts -O asm -o oasm_$tree.test.s $tree
	asm_to_so_test oasm_$tree
	run_dtc_test -I dts -O dtb -o $tree.test.dtb $tree
	run_test asm_tree_dump ./oasm_$tree.test.so oasm_$tree.test.dtb
	run_wrap_test cmp oasm_$tree.test.dtb $tree.test.dtb
    done

    run_test value-labels ./oasm_value-labels.dts.test.so

    # Check -Odts mode preserve all dtb information
    for tree in test_tree1.dtb dtc_tree1.test.dtb dtc_escapes.test.dtb \
	dtc_extra-terminating-null.test.dtb dtc_references.test.dtb; do
	run_dtc_test -I dtb -O dts -o odts_$tree.test.dts $tree
	run_dtc_test -I dts -O dtb -o odts_$tree.test.dtb odts_$tree.test.dts
	run_test dtbs_equal_ordered $tree odts_$tree.test.dtb
    done

    # Check version conversions
    for tree in test_tree1.dtb ; do
	 for aver in 1 2 3 16 17; do
	     atree="ov${aver}_$tree.test.dtb"
	     run_dtc_test -I dtb -O dtb -V$aver -o $atree $tree
	     for bver in 16 17; do
		 btree="ov${bver}_$atree"
		 run_dtc_test -I dtb -O dtb -V$bver -o $btree $atree
		 run_test dtbs_equal_ordered $btree $tree
	     done
	 done
    done

    # Check merge/overlay functionality
    run_dtc_test -I dts -O dtb -o dtc_tree1_merge.test.dtb test_tree1_merge.dts
    tree1_tests dtc_tree1_merge.test.dtb test_tree1.dtb
    run_dtc_test -I dts -O dtb -o dtc_tree1_merge_labelled.test.dtb test_tree1_merge_labelled.dts
    tree1_tests dtc_tree1_merge_labelled.test.dtb test_tree1.dtb
    run_dtc_test -I dts -O dtb -o dtc_tree1_label_noderef.test.dtb test_tree1_label_noderef.dts
    run_test dtbs_equal_unordered dtc_tree1_label_noderef.test.dtb test_tree1.dtb
    run_dtc_test -I dts -O dtb -o multilabel_merge.test.dtb multilabel_merge.dts
    run_test references multilabel.test.dtb
    run_test dtbs_equal_ordered multilabel.test.dtb multilabel_merge.test.dtb
    run_dtc_test -I dts -O dtb -o dtc_tree1_merge_path.test.dtb test_tree1_merge_path.dts
    tree1_tests dtc_tree1_merge_path.test.dtb test_tree1.dtb

    # Check prop/node delete functionality
    run_dtc_test -I dts -O dtb -o dtc_tree1_delete.test.dtb test_tree1_delete.dts
    tree1_tests dtc_tree1_delete.test.dtb

    run_dtc_test -I dts -O dts -o delete_reinstate_multilabel.dts.test.dts delete_reinstate_multilabel.dts
    run_wrap_test cmp delete_reinstate_multilabel.dts.test.dts delete_reinstate_multilabel_ref.dts

    # Check some checks
    check_tests dup-nodename.dts duplicate_node_names
    check_tests dup-propname.dts duplicate_property_names
    check_tests dup-phandle.dts explicit_phandles
    check_tests zero-phandle.dts explicit_phandles
    check_tests minusone-phandle.dts explicit_phandles
    run_sh_test dtc-checkfails.sh phandle_references -- -I dts -O dtb nonexist-node-ref.dts
    run_sh_test dtc-checkfails.sh phandle_references -- -I dts -O dtb nonexist-label-ref.dts
    run_sh_test dtc-fatal.sh -I dts -O dtb nonexist-node-ref2.dts
    check_tests bad-name-property.dts name_properties

    check_tests bad-ncells.dts address_cells_is_cell size_cells_is_cell interrupt_cells_is_cell
    check_tests bad-string-props.dts device_type_is_string model_is_string status_is_string
    check_tests bad-reg-ranges.dts reg_format ranges_format
    check_tests bad-empty-ranges.dts ranges_format
    check_tests reg-ranges-root.dts reg_format ranges_format
    check_tests default-addr-size.dts avoid_default_addr_size
    check_tests obsolete-chosen-interrupt-controller.dts obsolete_chosen_interrupt_controller
    run_sh_test dtc-checkfails.sh node_name_chars -- -I dtb -O dtb bad_node_char.dtb
    run_sh_test dtc-checkfails.sh node_name_format -- -I dtb -O dtb bad_node_format.dtb
    run_sh_test dtc-checkfails.sh prop_name_chars -- -I dtb -O dtb bad_prop_char.dtb

    run_sh_test dtc-checkfails.sh duplicate_label -- -I dts -O dtb reuse-label1.dts
    run_sh_test dtc-checkfails.sh duplicate_label -- -I dts -O dtb reuse-label2.dts
    run_sh_test dtc-checkfails.sh duplicate_label -- -I dts -O dtb reuse-label3.dts
    run_sh_test dtc-checkfails.sh duplicate_label -- -I dts -O dtb reuse-label4.dts
    run_sh_test dtc-checkfails.sh duplicate_label -- -I dts -O dtb reuse-label5.dts
    run_sh_test dtc-checkfails.sh duplicate_label -- -I dts -O dtb reuse-label6.dts

    # Check warning options
    run_sh_test dtc-checkfails.sh address_cells_is_cell interrupt_cells_is_cell -n size_cells_is_cell -- -Wno_size_cells_is_cell -I dts -O dtb bad-ncells.dts
    run_sh_test dtc-fails.sh -n test-warn-output.test.dtb -I dts -O dtb bad-ncells.dts
    run_sh_test dtc-fails.sh test-error-output.test.dtb -I dts -O dtb bad-ncells.dts -Esize_cells_is_cell
    run_sh_test dtc-checkfails.sh always_fail -- -Walways_fail -I dts -O dtb test_tree1.dts
    run_sh_test dtc-checkfails.sh -n always_fail -- -Walways_fail -Wno_always_fail -I dts -O dtb test_tree1.dts
    run_sh_test dtc-fails.sh test-negation-1.test.dtb -Ealways_fail -I dts -O dtb test_tree1.dts
    run_sh_test dtc-fails.sh -n test-negation-2.test.dtb -Ealways_fail -Eno_always_fail -I dts -O dtb test_tree1.dts
    run_sh_test dtc-fails.sh test-negation-3.test.dtb -Ealways_fail -Wno_always_fail -I dts -O dtb test_tree1.dts
    run_sh_test dtc-fails.sh -n test-negation-4.test.dtb -Esize_cells_is_cell -Eno_size_cells_is_cell -I dts -O dtb bad-ncells.dts
    run_sh_test dtc-checkfails.sh size_cells_is_cell -- -Esize_cells_is_cell -Eno_size_cells_is_cell -I dts -O dtb bad-ncells.dts

    # Check for proper behaviour reading from stdin
    run_dtc_test -I dts -O dtb -o stdin_dtc_tree1.test.dtb - < test_tree1.dts
    run_wrap_test cmp stdin_dtc_tree1.test.dtb dtc_tree1.test.dtb
    run_dtc_test -I dtb -O dts -o stdin_odts_test_tree1.dtb.test.dts - < test_tree1.dtb
    run_wrap_test cmp stdin_odts_test_tree1.dtb.test.dts odts_test_tree1.dtb.test.dts

    # Check integer expresisons
    run_test integer-expressions -g integer-expressions.test.dts
    run_dtc_test -I dts -O dtb -o integer-expressions.test.dtb integer-expressions.test.dts
    run_test integer-expressions integer-expressions.test.dtb

    # Check for graceful failure in some error conditions
    run_sh_test dtc-fatal.sh -I dts -O dtb nosuchfile.dts
    run_sh_test dtc-fatal.sh -I dtb -O dtb nosuchfile.dtb
    run_sh_test dtc-fatal.sh -I fs -O dtb nosuchfile

    # Dependencies
    run_dtc_test -I dts -O dtb -o dependencies.test.dtb -d dependencies.test.d dependencies.dts
    run_wrap_test cmp dependencies.test.d dependencies.cmp

    # Search paths
    run_wrap_error_test $DTC -I dts -O dtb -o search_paths.dtb search_paths.dts
    run_dtc_test -i search_dir -I dts -O dtb -o search_paths.dtb \
	search_paths.dts
    run_wrap_error_test $DTC -i search_dir_b -I dts -O dtb \
	-o search_paths_b.dtb search_paths_b.dts
    run_dtc_test -i search_dir_b -i search_dir -I dts -O dtb \
	-o search_paths_b.dtb search_paths_b.dts
    run_dtc_test -I dts -O dtb -o search_paths_subdir.dtb \
	search_dir_b/search_paths_subdir.dts
}

cmp_tests () {
    basetree="$1"
    shift
    wrongtrees="$@"

    run_test dtb_reverse $basetree

    # First dtbs_equal_ordered
    run_test dtbs_equal_ordered $basetree $basetree
    run_test dtbs_equal_ordered -n $basetree $basetree.reversed.test.dtb
    for tree in $wrongtrees; do
	run_test dtbs_equal_ordered -n $basetree $tree
    done

    # now unordered
    run_test dtbs_equal_unordered $basetree $basetree
    run_test dtbs_equal_unordered $basetree $basetree.reversed.test.dtb
    run_test dtbs_equal_unordered $basetree.reversed.test.dtb $basetree
    for tree in $wrongtrees; do
	run_test dtbs_equal_unordered -n $basetree $tree
    done

    # now dtc --sort
    run_dtc_test -I dtb -O dtb -s -o $basetree.sorted.test.dtb $basetree
    run_test dtbs_equal_unordered $basetree $basetree.sorted.test.dtb
    run_dtc_test -I dtb -O dtb -s -o $basetree.reversed.sorted.test.dtb $basetree.reversed.test.dtb
    run_test dtbs_equal_unordered $basetree.reversed.test.dtb $basetree.reversed.sorted.test.dtb
    run_test dtbs_equal_ordered $basetree.sorted.test.dtb $basetree.reversed.sorted.test.dtb
}

dtbs_equal_tests () {
    WRONG_TREE1=""
    for x in 1 2 3 4 5 6 7 8 9; do
	run_dtc_test -I dts -O dtb -o test_tree1_wrong$x.test.dtb test_tree1_wrong$x.dts
	WRONG_TREE1="$WRONG_TREE1 test_tree1_wrong$x.test.dtb"
    done
    cmp_tests test_tree1.dtb $WRONG_TREE1
}

fdtget_tests () {
    dts=label01.dts
    dtb=$dts.fdtget.test.dtb
    run_dtc_test -O dtb -o $dtb $dts

    # run_fdtget_test <expected-result> [<flags>] <file> <node> <property>
    run_fdtget_test "MyBoardName" $dtb / model
    run_fdtget_test "MyBoardName MyBoardFamilyName" $dtb / compatible
    run_fdtget_test "77 121 66 111 \
97 114 100 78 97 109 101 0 77 121 66 111 97 114 100 70 97 109 105 \
108 121 78 97 109 101 0" -t bu $dtb / compatible
    run_fdtget_test "MyBoardName MyBoardFamilyName" -t s $dtb / compatible
    run_fdtget_test 32768 $dtb /cpus/PowerPC,970@1 d-cache-size
    run_fdtget_test 8000 -tx $dtb /cpus/PowerPC,970@1 d-cache-size
    run_fdtget_test "61 62 63 0" -tbx $dtb /randomnode tricky1
    run_fdtget_test "a b c d de ea ad be ef" -tbx $dtb /randomnode blob

    # Here the property size is not a multiple of 4 bytes, so it should fail
    run_wrap_error_test $DTGET -tlx $dtb /randomnode mixed
    run_fdtget_test "6162 6300 1234 0 a 0 b 0 c" -thx $dtb /randomnode mixed
    run_fdtget_test "61 62 63 0 12 34 0 0 0 a 0 0 0 b 0 0 0 c" \
	-thhx $dtb /randomnode mixed
    run_wrap_error_test $DTGET -ts $dtb /randomnode doctor-who

    # Test multiple arguments
    run_fdtget_test "MyBoardName\nmemory" -ts $dtb / model /memory device_type

    # Test defaults
    run_wrap_error_test $DTGET -tx $dtb /randomnode doctor-who
    run_fdtget_test "<the dead silence>" -tx \
	-d "<the dead silence>" $dtb /randomnode doctor-who
    run_fdtget_test "<blink>" -tx -d "<blink>" $dtb /memory doctor-who
}

fdtput_tests () {
    dts=label01.dts
    dtb=$dts.fdtput.test.dtb
    text=lorem.txt

    # Allow just enough space for $text
    run_dtc_test -O dtb -p $(stat -c %s $text) -o $dtb $dts

    # run_fdtput_test <expected-result> <file> <node> <property> <flags> <value>
    run_fdtput_test "a_model" $dtb / model -ts "a_model"
    run_fdtput_test "board1 board2" $dtb / compatible -ts board1 board2
    run_fdtput_test "board1 board2" $dtb / compatible -ts "board1 board2"
    run_fdtput_test "32768" $dtb /cpus/PowerPC,970@1 d-cache-size "" "32768"
    run_fdtput_test "8001" $dtb /cpus/PowerPC,970@1 d-cache-size -tx 0x8001
    run_fdtput_test "2 3 12" $dtb /randomnode tricky1 -tbi "02 003 12"
    run_fdtput_test "a b c ea ad be ef" $dtb /randomnode blob \
	-tbx "a b c ea ad be ef"
    run_fdtput_test "a0b0c0d deeaae ef000000" $dtb /randomnode blob \
	-tx "a0b0c0d deeaae ef000000"
    run_fdtput_test "$(cat $text)" $dtb /randomnode blob -ts "$(cat $text)"

    # Test expansion of the blob when insufficient room for property
    run_fdtput_test "$(cat $text $text)" $dtb /randomnode blob -ts "$(cat $text $text)"

    # Start again with a fresh dtb
    run_dtc_test -O dtb -p $(stat -c %s $text) -o $dtb $dts

    # Node creation
    run_wrap_error_test $DTPUT $dtb -c /baldrick sod
    run_wrap_test $DTPUT $dtb -c /chosen/son /chosen/daughter
    run_fdtput_test "eva" $dtb /chosen/daughter name "" -ts "eva"
    run_fdtput_test "adam" $dtb /chosen/son name "" -ts "adam"

    # Not allowed to create an existing node
    run_wrap_error_test $DTPUT $dtb -c /chosen
    run_wrap_error_test $DTPUT $dtb -c /chosen/son

    # Automatic node creation
    run_wrap_test $DTPUT $dtb -cp /blackadder/the-second/turnip \
	/blackadder/the-second/potato
    run_fdtput_test 1000 $dtb /blackadder/the-second/turnip cost "" 1000
    run_fdtput_test "fine wine" $dtb /blackadder/the-second/potato drink \
	"-ts" "fine wine"
    run_wrap_test $DTPUT $dtb -p /you/are/drunk/sir/winston slurp -ts twice

    # Test expansion of the blob when insufficent room for a new node
    run_wrap_test $DTPUT $dtb -cp "$(cat $text $text)/longish"

    # Allowed to create an existing node with -p
    run_wrap_test $DTPUT $dtb -cp /chosen
    run_wrap_test $DTPUT $dtb -cp /chosen/son

    # Start again with a fresh dtb
    run_dtc_test -O dtb -p $(stat -c %s $text) -o $dtb $dts

    # Node delete
    run_wrap_test $DTPUT $dtb -c /chosen/node1 /chosen/node2 /chosen/node3
    run_fdtget_test "node3\nnode2\nnode1" $dtb -l  /chosen
    run_wrap_test $DTPUT $dtb -r /chosen/node1 /chosen/node2
    run_fdtget_test "node3" $dtb -l  /chosen

    # Delete the non-existent node
    run_wrap_error_test $DTPUT $dtb -r /non-existent/node

    # Property delete
    run_fdtput_test "eva" $dtb /chosen/ name "" -ts "eva"
    run_fdtput_test "016" $dtb /chosen/ age  "" -ts "016"
    run_fdtget_test "age\nname\nbootargs\nlinux,platform" $dtb -p  /chosen
    run_wrap_test $DTPUT $dtb -d /chosen/ name age
    run_fdtget_test "bootargs\nlinux,platform" $dtb -p  /chosen

    # Delete the non-existent property
    run_wrap_error_test $DTPUT $dtb -d /chosen   non-existent-prop

    # TODO: Add tests for verbose mode?
}

utilfdt_tests () {
    run_test utilfdt_test
}

fdtdump_tests () {
    run_fdtdump_test fdtdump.dts
    return

    local dts=fdtdump.dts
    local dtb=fdtdump.dts.dtb
    local out=fdtdump.dts.out
    run_dtc_test -O dtb $dts -o ${dtb}
    $FDTDUMP ${dtb} | grep -v "//" >${out}
    if cmp $dts $out >/dev/null; then
	PASS
    else
	if [ -z "$QUIET_TEST" ]; then
	    diff -w fdtdump.dts $out
	fi
	FAIL "Results differ from expected"
    fi
}

# Add a property to a tree, then hash it and see if it changed
# Args:
#   $1: 0 if we expect it to stay the same, 1 if we expect a change
#   $2: node to add a property to
#   $3: arguments for fdtget
#   $4: filename of device tree binary
#   $5: hash of unchanged file (empty to calculate it)
#   $6: node to add a property to ("testing" by default if empty)
check_hash () {
    local changed="$1"
    local node="$2"
    local args="$3"
    local tree="$4"
    local base="$5"
    local nodename="$6"

    if [ -z "$nodename" ]; then
	nodename=testing
    fi
    if [ -z "$base" ]; then
	base=$($DTGREP ${args} -O bin $tree | sha1sum)
    fi
    $DTPUT $tree $node $nodename 1
    hash=$($DTGREP ${args} -O bin $tree | sha1sum)
    if [ "$base" == "$hash" ]; then
	if [ "$changed" == 1 ]; then
	    echo "$test: Hash should have changed"
	    echo base $base
	    echo hash $hash
	    false
	fi
    else
	if [ "$changed" == 0 ]; then
	    echo "$test: Base hash is $base but it was changed to $hash"
	    false
	fi
    fi
}

# Check the number of lines generated matches what we expect
# Args:
#   $1: Expected number of lines
#   $2...: Command line to run to generate output
check_lines () {
    local base="$1"

    shift
    lines=$($@ | wc -l)
    if [ "$base" != "$lines" ]; then
	echo "Expected $base lines but got $lines lines"
	false
    fi
}

# Check the number of bytes generated matches what we expect
# Args:
#   $1: Expected number of bytes
#   $2...: Command line to run to generate output
check_bytes () {
    local base="$1"

    shift
    bytes=$($@ | wc -c)
    if [ "$base" != "$bytes" ]; then
	echo "Expected $base bytes but got $bytes bytes"
	false
    fi
}

# Check whether a command generates output which contains a string
# Args:
#   $1: 0 to expect the string to be absent, 1 to expect it to be present
#   $2: text to grep for
#   $3...: Command to execute
check_contains () {
    contains="$1"
    text="$2"

    shift 2
    if $@ | grep -q $text; then
	if [ $contains -ne 1 ]; then
	    echo "Did not expect to find $text in output"
	    false
	fi
    else
	if [ $contains -ne 0 ]; then
	    echo "Expected to find $text in output"
	    false
	fi
    fi
}

# Check that $2 and $3 are equal. $1 is the test name to display
equal_test () {
    echo -n "$1:	"
    if [ "$2" == "$3" ]; then
	PASS
    else
	FAIL "$2 != $3"
    fi
}

fdtgrep_tests () {
    local addr
    local all_lines        # Total source lines in .dts file
    local base
    local dt_start
    local lines
    local node_lines       # Number of lines of 'struct' output
    local orig
    local string_size
    local tmp
    local tree

    tmp=/tmp/tests.$$
    orig=region_tree.test.dtb
    run_wrap_test ./region_tree 0 1000 ${orig}

    # Hash of partial tree
    # - modify tree in various ways and check that hash is unaffected
    tree=region_tree.mod.dtb
    cp $orig $tree
    args="-n /images/kernel@1"
    run_wrap_test check_hash 0 /images "$args" $tree
    run_wrap_test check_hash 0 /images/kernel@1/hash@1 "$args" $tree
    run_wrap_test check_hash 0 / "$args" $tree
    $DTPUT -c $tree /images/kernel@1/newnode
    run_wrap_test check_hash 0 / "$args" $tree
    run_wrap_test check_hash 1 /images/kernel@1 "$args" $tree

    # Now hash immediate subnodes so we detect a new subnode added
    cp $orig $tree
    args="-n /images/kernel@1 -e"
    run_wrap_test check_hash 0 /images "$args" $tree
    run_wrap_test check_hash 0 /images/kernel@1/hash@1 "$args" $tree
    run_wrap_test check_hash 0 / "$args" $tree
    base=$($DTGREP $args -O bin $tree | sha1sum)
    $DTPUT -c $tree /images/kernel@1/newnode
    run_wrap_test check_hash 1 / "$args" $tree "$base"
    cp $orig $tree
    run_wrap_test check_hash 1 /images/kernel@1 "$args" $tree

    # Hash the string table, which should change if we add a new property name
    # (Adding an existing property name will just reuse that string)
    cp $orig $tree
    args="-t -n /images/kernel@1"
    run_wrap_test check_hash 0 /images "$args" $tree "" data
    run_wrap_test check_hash 1 /images/kernel@1 "$args" $tree

    dts=grep.dts
    dtb=grep.dtb
    run_dtc_test -O dtb -p 0x1000 -o $dtb $dts

    # Tests for each argument are roughly in alphabetical order
    #
    # First a sanity check that we can get back the source from the .dtb
    all_lines=$(cat $dts | wc -l)
    run_wrap_test check_lines ${all_lines} $DTGREP -Im $dtb
    node_lines=$(($all_lines - 2))

    # Get the offset of the dt_struct start (also tests -H somewhat)
    dt_start=$($DTGREP -H $dtb | awk '/off_dt_struct:/ {print $3}')
    dt_size=$($DTGREP -H $dtb | awk '/size_dt_struct:/ {print $3}')

    # Check -a: the first line should contain the offset of the dt_start
    addr=$($DTGREP -a $dtb | head -1 | tr -d : | awk '{print $1}')
    run_wrap_test equal_test "-a offset first" "$dt_start" "0x$addr"

    # Last line should be 8 bytes less than the size (NODE, END tags)
    addr=$($DTGREP -a $dtb | tail -1 | tr -d : | awk '{print $1}')
    last=$(printf "%#x" $(($dt_start + $dt_size - 8)))
    run_wrap_test equal_test "-a offset last" "$last" "0x$addr"

    # Check the offset option in a similar way. The first offset should be 0
    # and the last one should be the size of the struct area.
    addr=$($DTGREP -f $dtb | head -1 | tr -d : | awk '{print $1}')
    run_wrap_test equal_test "-o offset first" "0x0" "0x$addr"
    addr=$($DTGREP -f $dtb | tail -1 | tr -d : | awk '{print $1}')
    last=$(printf "%#x" $(($dt_size - 8)))
    run_wrap_test equal_test "-f offset last" "$last" "0x$addr"

    # Check that -A controls display of all lines
    # The 'chosen' node should only have four output lines
    run_wrap_test check_lines $node_lines $DTGREP -S -A -n /chosen $dtb
    run_wrap_test check_lines 4 $DTGREP -S -n /chosen $dtb

    # Check that -c picks out nodes
    run_wrap_test check_lines 5 $DTGREP -S -c ixtapa $dtb
    run_wrap_test check_lines $(($node_lines - 5)) $DTGREP -S -C ixtapa $dtb

    # -d marks selected lines with +
    run_wrap_test check_lines $node_lines $DTGREP -S -Ad -n /chosen $dtb
    run_wrap_test check_lines 4 $DTGREP -S -Ad -n /chosen $dtb |grep +

    # -g should find a node, property or compatible string
    run_wrap_test check_lines 2 $DTGREP -S -g / $dtb
    run_wrap_test check_lines 2 $DTGREP -S -g /chosen $dtb
    run_wrap_test check_lines $(($node_lines - 2)) $DTGREP -S -G /chosen $dtb

    run_wrap_test check_lines 1 $DTGREP -S -g bootargs $dtb
    run_wrap_test check_lines $(($node_lines - 1)) $DTGREP -S -G bootargs $dtb

    # We should find the /holiday node, so 1 line for 'holiday {', one for '}'
    run_wrap_test check_lines 2 $DTGREP -S -g ixtapa $dtb
    run_wrap_test check_lines $(($node_lines - 2)) $DTGREP -S -G ixtapa $dtb

    run_wrap_test check_lines 3 $DTGREP -S -g ixtapa -g bootargs $dtb
    run_wrap_test check_lines $(($node_lines - 3)) $DTGREP -S -G ixtapa \
	-G bootargs $dtb

    # -l outputs a,list of regions - here we should get 3: one for the header,
    # one for the node and one for the 'end' tag.
    run_wrap_test check_lines 3 $DTGREP -S -l -n /chosen $dtb -o $tmp

    # -L outputs all the strings in the string table
    cat >$tmp <<END
	#address-cells
	airline
	bootargs
	compatible
	linux,platform
	model
	#size-cells
	status
	weather
END
    lines=$(cat $tmp | wc -l)
    run_wrap_test check_lines $lines $DTGREP -S -L -n // $dtb

    # Check that the -m flag works
    run_wrap_test check_contains 1 memreserve $DTGREP -Im $dtb
    run_wrap_test check_contains 0 memreserve $DTGREP -I $dtb

    # Test -n
    run_wrap_test check_lines 0 $DTGREP -S -n // $dtb
    run_wrap_test check_lines 0 $DTGREP -S -n chosen $dtb
    run_wrap_test check_lines 0 $DTGREP -S -n holiday $dtb
    run_wrap_test check_lines 0 $DTGREP -S -n \"\" $dtb
    run_wrap_test check_lines 4 $DTGREP -S -n /chosen $dtb
    run_wrap_test check_lines 5 $DTGREP -S -n /holiday $dtb
    run_wrap_test check_lines 9 $DTGREP -S -n /chosen -n /holiday $dtb

    # Test -N which should list everything except matching nodes
    run_wrap_test check_lines $node_lines $DTGREP -S -N // $dtb
    run_wrap_test check_lines $node_lines $DTGREP -S -N chosen $dtb
    run_wrap_test check_lines $(($node_lines - 4)) $DTGREP -S -N /chosen $dtb
    run_wrap_test check_lines $(($node_lines - 5)) $DTGREP -S -N /holiday $dtb
    run_wrap_test check_lines $(($node_lines - 9)) $DTGREP -S -N /chosen \
	-N /holiday $dtb

    # Using -n and -N together is undefined, so we don't have tests for that
    # The same applies for -p/-P and -c/-C.
    run_wrap_error_test $DTGREP -n chosen -N holiday $dtb
    run_wrap_error_test $DTGREP -c chosen -C holiday $dtb
    run_wrap_error_test $DTGREP -p chosen -P holiday $dtb

    # Test -o: this should output just the .dts file to a file
    # Where there is non-dts output it should go to stdout
    rm -f $tmp
    run_wrap_test check_lines 0 $DTGREP $dtb -o $tmp
    run_wrap_test check_lines $node_lines cat $tmp

    # Here we expect a region list with a single entry, plus a header line
    # on stdout
    run_wrap_test check_lines 2 $DTGREP $dtb -o $tmp -l
    run_wrap_test check_lines $node_lines cat $tmp

    # Here we expect a list of strings on stdout
    run_wrap_test check_lines ${lines} $DTGREP $dtb -o $tmp -L
    run_wrap_test check_lines $node_lines cat $tmp

    # Test -p: with -S we only get the compatible lines themselves
    run_wrap_test check_lines 2 $DTGREP -S -p compatible -n // $dtb
    run_wrap_test check_lines 1 $DTGREP -S -p bootargs -n // $dtb

    # Without -S we also get the node containing these properties
    run_wrap_test check_lines 6 $DTGREP -p compatible -n // $dtb
    run_wrap_test check_lines 5 $DTGREP -p bootargs -n // $dtb

    # Now similar tests for -P
    # First get the number of property lines (containing '=')
    lines=$(grep "=" $dts |wc -l)
    run_wrap_test check_lines $(($lines - 2)) $DTGREP -S -P compatible \
	-n // $dtb
    run_wrap_test check_lines $(($lines - 1)) $DTGREP -S -P bootargs \
	-n // $dtb
    run_wrap_test check_lines $(($lines - 3)) $DTGREP -S -P compatible \
	-P bootargs -n // $dtb

    # Without -S we also get the node containing these properties
    run_wrap_test check_lines $(($node_lines - 2)) $DTGREP -P compatible \
	-n // $dtb
    run_wrap_test check_lines $(($node_lines - 1)) $DTGREP -P bootargs \
	-n // $dtb
    run_wrap_test check_lines $(($node_lines - 3)) $DTGREP -P compatible \
	-P bootargs -n // $dtb

    # -s should bring in all sub-nodes
    run_wrap_test check_lines 2 $DTGREP -p none -n / $dtb
    run_wrap_test check_lines 6 $DTGREP -e -p none -n / $dtb
    run_wrap_test check_lines 2 $DTGREP -S -p none -n /holiday $dtb
    run_wrap_test check_lines 4 $DTGREP  -p none -n /holiday $dtb
    run_wrap_test check_lines 8 $DTGREP -e -p none -n /holiday $dtb

    # -v inverts the polarity of any condition
    run_wrap_test check_lines $(($node_lines - 2)) $DTGREP -Sv -p none \
	-n / $dtb
    run_wrap_test check_lines $(($node_lines - 2)) $DTGREP -Sv -p compatible \
	-n // $dtb
    run_wrap_test check_lines $(($node_lines - 2)) $DTGREP -Sv -g /chosen \
	$dtb
    run_wrap_test check_lines $node_lines $DTGREP -Sv -n // $dtb
    run_wrap_test check_lines $node_lines $DTGREP -Sv -n chosen $dtb
    run_wrap_error_test $DTGREP -v -N holiday $dtb

    # Check that the -I flag works
    run_wrap_test check_contains 1 dts-v1 $DTGREP -I $dtb
    run_wrap_test check_contains 0 dts-v1 $DTGREP $dtb

    # Now some dtb tests. The dts tests above have tested the basic grepping
    # features so we only need to concern ourselves with things that are
    # different about dtb/bin output.

    # An empty node list should just give us the FDT_END tag
    run_wrap_test check_bytes 4 $DTGREP -n // -O bin $dtb

    # The mem_rsvmap is two entries of 16 bytes each
    run_wrap_test check_bytes $((4 + 32)) $DTGREP -m -n // -O bin $dtb

    # Check we can add the string table
    string_size=$($DTGREP -H $dtb | awk '/size_dt_strings:/ {print $3}')
    run_wrap_test check_bytes $((4 + $string_size)) $DTGREP -t -n // -O bin \
	$dtb
    run_wrap_test check_bytes $((4 + 32 + $string_size)) $DTGREP -tm \
	-n // -O bin $dtb

    # Check that a pass-through works ok. fdtgrep aligns the mem_rsvmap table
    # to a 16-bytes boundary, but dtc uses 8 bytes so we expect the size to
    # increase by 8 bytes...
    run_dtc_test -O dtb -o $dtb $dts
    base=$(stat -c %s $dtb)
    run_wrap_test check_bytes $(($base + 8)) $DTGREP -O dtb $dtb

    # ...but we should get the same output from fdtgrep in a second pass
    run_wrap_test check_bytes 0 $DTGREP -O dtb $dtb -o $tmp
    base=$(stat -c %s $tmp)
    run_wrap_test check_bytes $base $DTGREP -O dtb $tmp

    rm -f $tmp
}

while getopts "vt:me" ARG ; do
    case $ARG in
	"v")
	    unset QUIET_TEST
	    ;;
	"t")
	    TESTSETS=$OPTARG
	    ;;
	"m")
	    VALGRIND="valgrind --tool=memcheck -q --error-exitcode=$VGCODE"
	    ;;
	"e")
	    STOP_ON_FAIL=1
	    ;;
    esac
done

if [ -z "$TESTSETS" ]; then
    TESTSETS="libfdt utilfdt dtc dtbs_equal fdtget fdtput fdtdump fdtgrep"
fi

# Make sure we don't have stale blobs lying around
rm -f *.test.dtb *.test.dts

for set in $TESTSETS; do
    case $set in
	"libfdt")
	    libfdt_tests
	    ;;
	"utilfdt")
	    utilfdt_tests
	    ;;
	"dtc")
	    dtc_tests
	    ;;
	"dtbs_equal")
	    dtbs_equal_tests
	    ;;
	"fdtget")
	    fdtget_tests
	    ;;
	"fdtput")
	    fdtput_tests
	    ;;
	"fdtdump")
	    fdtdump_tests
	    ;;
	"fdtgrep")
	    fdtgrep_tests
	    ;;
    esac
done

echo "********** TEST SUMMARY"
echo "*     Total testcases:	$tot_tests"
echo "*                PASS:	$tot_pass"
echo "*                FAIL:	$tot_fail"
echo "*   Bad configuration:	$tot_config"
if [ -n "$VALGRIND" ]; then
    echo "*    valgrind errors:	$tot_vg"
fi
echo "* Strange test result:	$tot_strange"
echo "**********"

