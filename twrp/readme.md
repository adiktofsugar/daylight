# twrp

<https://github.com/minimal-manifest-twrp/platform_manifest_twrp_aosp>

I was thinking maybe I could build twrp for daylight? I think, actually, a guy on the discord already did, but it's basically impossible to read. Hm.

Anyway, you need to build either:

- recovery partition (and build with `mka recoveryimage`)
- boot image ramdisk (and build with `mka bootimage`)
- vendor_boot image ramdisk (and build with `mka vendorbootimage`)

It doesn't look like the daylight has a recovery partition. I checked:

- `fastboot getvar all`
- `ls -1 /dev/block/by-name`
- `lpdump`

There is a boot (a/b) and vendor_boot (a/b), so I'm not sure which one of those to build.
