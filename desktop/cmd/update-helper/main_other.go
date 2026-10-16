//go:build !linux

package main

import (
	"fmt"
	"os"
)

func main() {
	fmt.Fprintln(os.Stderr, "reasonix-update-helper is only used by Linux builds")
	os.Exit(2)
}
