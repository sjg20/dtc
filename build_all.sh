#!/bin/bash

FLAGS=f00
DTC=~/cosarm/dtc/dtc
KERNEL_DIR=~/cosarm/src/third_party/kernel/v4.19

do_arm32() {
	export ARCH=arm
	export CROSS_COMPILE=~/.buildman-toolchains/gcc-7.3.0-nolibc/arm-linux-gnueabi/bin/arm-linux-gnueabi- make O=b/imx_v6_v7  -j8 imx_v6_v7_defconfig && ARCH=arm CROSS_COMPILE=~/.buildman-toolchains/gcc-7.3.0-nolibc/arm-linux-gnueabi/bin/arm-linux-gnueabi-

	pushd ${KERNEL_DIR} >/dev/null
	for defconf in arch/arm/configs/*; do
		config="${defconf%_defconfig}"
		config="${config#arch/arm/configs/}"
		echo "Building ${config}"

		make O=b/${config} ${config}_defconfig
		make O=b/${config} -j8 arch/arm/boot/dts/
	done
	popd >/dev/null
}

do_arm64() {
	export ARCH=arm64
	export CROSS_COMPILE=~/.buildman-toolchains/gcc-7.3.0-nolibc/aarch64-linux/bin/aarch64-linux-

	pushd ${KERNEL_DIR} >/dev/null
	make O=b/arm64 menuconfig
	make O=b/arm64 -j8 arch/arm64/boot/dts/
	popd >/dev/null
}

recomp() {
	pushd ~/cosarm/dtc/kdtb >/dev/null
	out=v18
	mkdir -p $out
	mkdir -p dts
	for f in *.dtb; do
# 		echo $f
		fdtdump $f >"dts/${f%.dtb}.dts"
		if ! fdtdump $f | \
			${DTC} -V 18 -F $FLAGS -o $out/$f 2>/dev/null; then
			echo "Error on $f"
			exit 1
		fi
	done
	total1=0
	total2=0
	MB=$((1024 * 1024))
	for f in *.dtb; do
		#ls -l $f $out/$f
		size1="$(stat -c %s $f)"
		size2="$(stat -c %s $out/$f)";
		total1=$(($total1 + $size1))
		total2=$(($total2 + $size2))
# 		echo -e "$(($size2 * 100 / $size1))\t$f"
	done
	percent=$(echo "scale=1; $total2 * 100 / $total1" | bc -l)
	echo -en "${percent}%\t"
	mb1=$(echo "scale=1; $total1 / $MB" | bc -l)
	mb2=$(echo "scale=1; $total2 / $MB" | bc -l)
# 	echo -e "$(($total1 / KB))\t$(($total2 / KB))\tall"
	echo -e "${mb2} MB / ${mb1} MB"
	popd >/dev/null
}

# do_arm32
# do_arm64
recomp
