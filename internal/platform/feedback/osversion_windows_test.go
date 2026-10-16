package feedback

import "testing"

func TestProductVersionString(t *testing.T) {
	cases := []struct {
		major, minor, build uint32
		want                string
	}{
		{6, 3, 9600, "6.3.9600"},     // Windows 8.1
		{10, 0, 19045, "10.0.19045"}, // Windows 10 22H2
		{10, 0, 22000, "11.0.22000"}, // Windows 11 21H2, the first 22000 build
		{10, 0, 26200, "11.0.26200"}, // the build issue #12696 reported
	}
	for _, c := range cases {
		if got := productVersionString(c.major, c.minor, c.build); got != c.want {
			t.Errorf("productVersionString(%d, %d, %d) = %q, want %q", c.major, c.minor, c.build, got, c.want)
		}
	}
}
