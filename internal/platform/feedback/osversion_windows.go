package feedback

import (
	"fmt"

	"golang.org/x/sys/windows"
)

// osVersion reports the product line users recognize. Windows 11 kept the
// kernel's 10.0 major, so only the build number tells it apart (22000 is the
// first Windows 11 release); reporting "10.0.26200" read as Windows 10.
func osVersion() string {
	v := windows.RtlGetVersion()
	return productVersionString(v.MajorVersion, v.MinorVersion, v.BuildNumber)
}

func productVersionString(major, minor, build uint32) string {
	if build >= 22000 {
		major, minor = 11, 0
	}
	return fmt.Sprintf("%d.%d.%d", major, minor, build)
}
